"""Pure plan construction: database evidence in, reviewable immutable document out.

No subprocesses, SSH connections or Slurm requests are permitted in this module.
"""

import copy
import difflib
import hashlib
import json
import re
import shlex
from datetime import UTC, datetime, timedelta
from pathlib import PurePosixPath

from fastapi import HTTPException
from sqlalchemy import select

from srs.config import Settings
from srs.models import Draft, Job, Node, Plan, Project, Schedule, now, parse_time
from srs.probes import HOST_PROBE, preflight_script

TEMPLATE_VERSION = "2026-11-07.1"
READ_OPERATIONS = frozenset({"host_snapshot", "job_status", "job_logs"})
CONFIG_PATH = "/etc/srs/approved-config.json"


def content_hash(document: dict) -> str:
    raw = json.dumps(document, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)
    return hashlib.sha256(raw.encode()).hexdigest()


def node_binding(node: Node) -> dict:
    return {
        "id": node.id, "alias": node.alias, "hostname": node.hostname,
        "port": node.port, "ssh_username": node.ssh_username,
        "enabled": node.enabled, "inventory_revision": node.inventory_revision,
        "host_key_sha256": node.host_key_sha256,
        "gpu_uuids": [g.get("uuid") for g in node.hardware.get("gpus", []) if g.get("in_scope")],
    }


def validate_public_parameters(value, path="parameters") -> None:
    if isinstance(value, dict):
        for key, nested in value.items():
            if re.search(r"password|passwd|secret|private_key|access_token|ssh_command", key, re.I):
                raise HTTPException(422, f"{path}.{key} 不可包含憑證或任意 SSH 指令")
            validate_public_parameters(nested, f"{path}.{key}")
    elif isinstance(value, list):
        if len(value) > 128:
            raise HTTPException(422, "參數陣列過長")
        for nested in value:
            validate_public_parameters(nested, path)
    elif isinstance(value, str):
        if "\x00" in value or len(value) > 65536:
            raise HTTPException(422, "字串包含 NUL 或超過長度限制")
    elif value is not None and not isinstance(value, (int, float, bool)):
        raise HTTPException(422, "不支援的參數型別")


def within(path: str, root: str) -> bool:
    p, r = PurePosixPath(path), PurePosixPath(root)
    return p.is_absolute() and ".." not in p.parts and p != r and r in p.parents


def validate_project(payload, settings: Settings) -> None:
    if not within(payload.path, str(settings.project_root)):
        raise HTTPException(422, f"專案必須位於 {settings.project_root} 之下")
    entry = PurePosixPath(payload.entrypoint)
    if entry.is_absolute() or ".." in entry.parts or not payload.entrypoint.endswith(".py"):
        raise HTTPException(422, "入口必須是專案內的相對 .py 路徑")
    if not within(payload.container, "/srv/srs/images") or not payload.container.endswith(".sif"):
        raise HTTPException(422, "容器必須是 /srv/srs/images 內的 .sif 檔")
    for value in (payload.path, payload.entrypoint, payload.container, payload.revision):
        if any(c in value for c in ("\x00", "\n", "\r")):
            raise HTTPException(422, "路徑與版本不能包含控制字元")


def base_step(index: int, node: Node, title: str, operation: str, *, read_only: bool = False) -> dict:
    return {
        "id": f"step-{index}", "host_id": node.id, "title": title,
        "operation": operation, "kind": "ssh", "argv": [], "script": None,
        "cwd": "/tmp", "environment": {}, "request": None, "file_change": None,
        "reason": "", "evidence": [], "requires_sudo": False,
        "execution_user": node.ssh_username, "timeout_seconds": 30,
        "preconditions": [{"type": "command_exists", "path": "/usr/bin/python3"}],
        "on_failure": "stop_dependents", "read_only": read_only,
        "credential_references": {"ssh_username": "HOST_" + node.alias.upper() + "_USERNAME",
                                  "ssh_password": "HOST_" + node.alias.upper() + "_SSH_PASSWORD",
                                  "sudo_password": "HOST_" + node.alias.upper() + "_SUDO_PASSWORD"},
        "verification": {"success_exit_code": 0, "record_stdout_stderr": True,
                         "after_failure": "no dependent dispatch or unapproved repair"},
    }


def add_manifest(step: dict, node: Node, settings: Settings) -> None:
    if step["kind"] != "ssh":
        return
    from srs.remote_runner import build_manifest
    step["remote_manifest"] = build_manifest(step, {
        "execution_id": "${execution_id}", "step_id": step["id"], "mode": settings.mode,
        "host": node_binding(node), "data_dir": str(settings.data_dir), "plan_hash": "${plan_hash}",
    })


def choose_nodes(db, ids: list[str]) -> list[Node]:
    if not ids or len(ids) != len(set(ids)):
        raise HTTPException(422, "請選擇不重複的主機")
    nodes = []
    for node_id in ids:
        node = db.get(Node, node_id)
        if not node:
            raise HTTPException(404, f"找不到主機 {node_id}")
        if not node.enabled:
            raise HTTPException(422, f"{node.alias} 未納入試點，不能產生執行計畫")
        nodes.append(node)
    return nodes


def common_issues(nodes: list[Node], settings: Settings) -> list[str]:
    if settings.mode == "demo":
        return []
    issues = []
    for node in nodes:
        if not node.host_key_sha256 or not node.host_key_sha256.startswith("SHA256:"):
            issues.append(f"{node.alias} 尚未設定經可信管道核對的 SSH 主機金鑰指紋")
    return issues


def evidence_projects(db, owner: str) -> list[dict]:
    return [{"id": p.id, "path": p.path, "entrypoint": p.entrypoint, "container": p.container}
            for p in db.scalars(select(Project).where(Project.owner == owner))]


def preflight_steps(db, nodes, owner, settings):
    steps = []
    for node in nodes:
        step = base_step(len(steps) + 1, node, "唯讀預檢與專案摘要", "preflight", read_only=True)
        step["argv"] = ["/usr/bin/python3", "-B", "-c", preflight_script(evidence_projects(db, owner))]
        step["reason"] = "取得 CPU、記憶體、GPU、cgroup、時鐘、專案 Git 版本與容器摘要；不讀取密碼或程序命令列。"
        step["evidence"] = [{"source": "user_selected_host", "value": node.alias}]
        step["timeout_seconds"] = 120
        add_manifest(step, node, settings)
        steps.append(step)
    return steps


def job_script(project: Project, params: dict, nodes: list[Node], image_sha: str | None) -> str:
    profile = params.get("profile", "single")
    args = params.get("args", [])
    if profile not in {"cpu", "single", "ddp", "fsdp2"}:
        raise HTTPException(422, "未知訓練模式")
    if not isinstance(args, list) or len(args) > 32 or any(
        not isinstance(value, str) or len(value) > 64 for value in args
    ):
        raise HTTPException(422, "訓練參數必須是有限長度的字串陣列")
    # Only mathematical workload options are configurable. Routing, device,
    # output/checkpoint paths and resume sources cannot override the approved plan.
    limits = {
        "steps": (1, 100), "warmup": (0, 10), "global-batch": (1, 32),
        "sequence-length": (1, 128), "width": (32, 512), "layers": (1, 4),
        "heads": (1, 8), "vocabulary": (16, 1024), "seed": (0, 2147483647),
    }
    parsed = {}
    index = 0
    while index < len(args):
        option = args[index]
        if "=" in option:
            flag, value = option.split("=", 1)
            index += 1
        else:
            if index + 1 >= len(args):
                raise HTTPException(422, "每個訓練參數都需要整數值")
            flag, value = option, args[index + 1]
            index += 2
        key = flag[2:] if flag.startswith("--") else ""
        if key not in limits or key in parsed or not re.fullmatch(r"[0-9]{1,10}", value):
            raise HTTPException(422, "只允許不重複的合成模型整數參數；不能覆寫模式、裝置或路徑")
        number = int(value)
        if not limits[key][0] <= number <= limits[key][1]:
            raise HTTPException(422, f"{flag} 超出試點範圍")
        parsed[key] = number
    width, heads = parsed.get("width", 128), parsed.get("heads", 4)
    if width not in {32, 64, 128, 256, 512} or heads not in {1, 2, 4, 8} or width % heads:
        raise HTTPException(422, "模型 width／heads 不相容")
    world = 2 if profile in {"ddp", "fsdp2"} else 1
    if len(nodes) != world or parsed.get("global-batch", 4) % world:
        raise HTTPException(422, "主機數或全域 batch 與訓練模式不相容")
    if any(not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", node.id) for node in nodes):
        raise HTTPException(422, "不合法的主機識別碼")
    args = [value for key in limits if key in parsed for value in ("--" + key, str(parsed[key]))]
    # Every rank checks the immutable image independently inside its own step.
    image_check = ""
    if image_sha:
        if not re.fullmatch(r"[a-fA-F0-9]{64}", image_sha):
            raise HTTPException(422, "不合法的容器 SHA256")
        image_check = f"printf '%s  %s\\n' {shlex.quote(image_sha)} {shlex.quote(project.container)} | sha256sum --check --status\n"
    device = "cpu" if profile == "cpu" else "cuda"
    training = ["apptainer", "exec", "--cleanenv"] + ([] if device == "cpu" else ["--nv"])
    training += ["--bind", "/srv/srs:/srv/srs", project.container, "python", "-u", project.entrypoint,
                 "--profile", profile, "--device", device]
    launch = (shlex.join(training) + ' --output-dir "$RUN_DIR/output"'
              + ' --checkpoint-dir "$RUN_DIR/checkpoint" ' + shlex.join(args))
    snapshot = (
        f"git -C {shlex.quote(project.path)} archive --format=tar {shlex.quote(project.revision)} | tar -x -C \"$RUN_DIR/code\"\n"
        if re.fullmatch(r"[a-fA-F0-9]{40}|[a-fA-F0-9]{64}", project.revision)
        else f"# DEMO ONLY: no real project snapshot is executed ({project.revision})\n"
    )
    rank_environment = (
        'export APPTAINERENV_SLURM_JOB_ID="${SLURM_JOB_ID:?}"\n'
        'export APPTAINERENV_RANK="${SLURM_PROCID:?}"\n'
        'export APPTAINERENV_WORLD_SIZE="${SLURM_NTASKS:?}"\n'
        'export APPTAINERENV_LOCAL_RANK="${SLURM_LOCALID:?}"\n'
        'export APPTAINERENV_MASTER_ADDR="${MASTER_ADDR:?}"\n'
        'export APPTAINERENV_MASTER_PORT="${MASTER_PORT:?}"\n'
        'export APPTAINERENV_OMP_NUM_THREADS=1\nexport APPTAINERENV_MKL_NUM_THREADS=1\n'
    )
    if device == "cuda":
        rank_environment += 'export APPTAINERENV_CUDA_VISIBLE_DEVICES="${CUDA_VISIBLE_DEVICES:?}"\n'
    master_address = "srs-" + nodes[0].id
    if world > 1:
        import ipaddress

        cases = []
        for node in nodes:
            network = (node.facts or {}).get("cluster", {}).get("network", {})
            interface = network.get("interface", "unverified")
            if not isinstance(interface, str) or not re.fullmatch(r"[A-Za-z0-9_.-]{1,32}", interface):
                raise HTTPException(422, "不合法的已核對網路介面")
            cases.append(f"srs-{node.id}) export APPTAINERENV_NCCL_SOCKET_IFNAME={shlex.quote('=' + interface)} ;;\n")
            if node.id == nodes[0].id and network.get("address"):
                try:
                    master_address = str(ipaddress.ip_address(network["address"]))
                except ValueError as error:
                    raise HTTPException(422, "跨節點 rendezvous 必須使用已核對 IP") from error
        rank_environment += 'case "${SLURMD_NODENAME:?}" in\n' + "".join(cases) + "*) exit 64 ;;\nesac\n"
    per_rank = rank_environment + image_check + "exec " + launch
    return (
        "#!/bin/bash\nset -euo pipefail\numask 077\n"
        "export RUN_DIR=\"/srv/srs/results/${SLURM_JOB_ID}\"\n"
        'mkdir -m 0700 "$RUN_DIR"\nmkdir "$RUN_DIR/code" "$RUN_DIR/output"\n'
        + snapshot + "cd \"$RUN_DIR/code\"\n"
        "export OMP_NUM_THREADS=1\nexport SLURM_EXPORT_ENV=ALL\n"
        + f"export MASTER_ADDR={shlex.quote(master_address)}\n"
        "export MASTER_PORT=$((20000 + SLURM_JOB_ID % 20000))\n"
        + image_check
        + f"srun --kill-on-bad-exit=1 --export=ALL --ntasks={world} --ntasks-per-node=1 "
        + shlex.join(["bash", "-euo", "pipefail", "-c", per_rank]) + "\n"
    )


def build_job_steps(db, draft: Draft, nodes: list[Node], settings: Settings, plan_id: str):
    params = draft.parameters
    project = db.get(Project, params.get("project_id", ""))
    if project is None or project.owner != draft.owner:
        raise HTTPException(422, "請選擇已登錄且屬於你的專案")
    profile = params.get("profile", "single")
    if profile not in project.modes:
        raise HTTPException(422, "專案未聲明支援此訓練模式")
    if profile in ("ddp", "fsdp2") and len(nodes) != 2:
        raise HTTPException(422, "跨機 DDP／FSDP2 試點必須選擇兩台主機")
    if profile in ("cpu", "single") and len(nodes) != 1:
        raise HTTPException(422, "CPU／單卡範例請選擇一台主機")
    for key, low, high, default in (("cpus", 1, 4, 1), ("memory_mb", 256, 16384, 2048), ("time_limit_minutes", 1, 5, 5)):
        value = params.get(key, default)
        if type(value) is not int or not low <= value <= high:
            raise HTTPException(422, f"{key} 必須介於 {low} 與 {high}")
    issues, steps = [], []
    image_sha = None
    shared_proofs, network_proofs = [], []
    selected_ids = {node.id for node in nodes}
    for node in nodes:
        fact = (node.facts or {}).get("projects", {}).get(project.id, {})
        candidate_sha = fact.get("container_sha256")
        if settings.mode != "demo":
            if not re.fullmatch(r"[a-fA-F0-9]{40}|[a-fA-F0-9]{64}", project.revision):
                issues.append("實機專案 revision 必須是固定 Git commit；先完成核准的預檢")
            if fact.get("revision", {}).get("stdout", "").strip() != project.revision:
                issues.append(f"{node.alias} 尚無與專案版本一致的預檢證據")
            if not isinstance(candidate_sha, str) or not re.fullmatch(r"[a-fA-F0-9]{64}", candidate_sha):
                issues.append(f"{node.alias} 尚無容器檔案 SHA256 證據")
                candidate_sha = None
            if image_sha and candidate_sha and image_sha != candidate_sha:
                issues.append("兩台節點的容器摘要不一致")
            if len(selected_ids) > 1:
                from srs.models import Execution

                cluster = (node.facts or {}).get("cluster", {})
                source = db.get(Execution, cluster.get("execution_id", "")) if cluster.get("execution_id") else None
                source_valid = bool(source and source.status == "succeeded" and source.mode == settings.mode
                                    and source.owner == draft.owner and source.approved_hash
                                    and content_hash(source.plan_document) == source.approved_hash)
                if source_valid:
                    binding = next((item for item in source.plan_document.get("inventory", [])
                                    if item.get("id") == node.id), None)
                    source_valid = bool(binding and binding.get("inventory_revision") == node.inventory_revision
                                        and binding.get("host_key_sha256") == node.host_key_sha256)
                try:
                    validated_at = parse_time(cluster.get("validated_at", ""))
                    age = datetime.now(UTC) - validated_at
                    fresh = timedelta(0) <= age <= timedelta(minutes=settings.approval_minutes)
                except (ValueError, TypeError):
                    fresh = False
                if not source_valid or not fresh:
                    issues.append(f"{node.alias} 尚無綁定目前 inventory、有效期限內且來自已批准執行的跨節點證據")
                storage = cluster.get("shared_storage", {})
                if not (storage.get("verified") is True and storage.get("root") == "/srv/srs"
                        and set(storage.get("peer_ids", [])) == selected_ids and storage.get("probe_id")):
                    issues.append(f"{node.alias} 尚未證實 /srv/srs 在兩節點真實共享")
                else:
                    shared_proofs.append(storage["probe_id"])
                network = cluster.get("network", {})
                address_valid = False
                try:
                    import ipaddress

                    address = ipaddress.ip_address(network.get("address", ""))
                    address_valid = not (address.is_loopback or address.is_unspecified or address.is_multicast)
                except (ValueError, TypeError):
                    pass
                if not (network.get("verified") is True and address_valid
                        and set(network.get("peer_ids", [])) == selected_ids
                        and network.get("probe_id") and isinstance(network.get("interface"), str)
                        and re.fullmatch(r"[A-Za-z0-9_.-]{1,32}", network["interface"])):
                    issues.append(f"{node.alias} 尚未證實兩節點可互通的訓練網路與介面")
                else:
                    network_proofs.append(network["probe_id"])
        image_sha = candidate_sha or image_sha
        step = base_step(len(steps) + 1, node, "核對訓練環境與固定版本", "verify_project", read_only=True)
        step["argv"] = ["/usr/bin/git", "-C", project.path, "cat-file", "-e", f"{project.revision}^{{commit}}"]
        step["reason"] = "確保已核准 Git commit 存在；Slurm 作業使用該 commit 的快照，並在執行節點重驗容器摘要。"
        if candidate_sha:
            step["preconditions"].append({"type": "file_sha256", "path": project.container, "sha256": candidate_sha})
        add_manifest(step, node, settings)
        steps.append(step)
    if settings.mode != "demo" and len(selected_ids) > 1:
        if len(shared_proofs) != len(selected_ids) or len(set(shared_proofs)) != 1:
            issues.append("兩節點的共享儲存探測證據必須來自同一輪驗證")
        if len(network_proofs) != len(selected_ids) or len(set(network_proofs)) != 1:
            issues.append("兩節點的網路互通證據必須來自同一輪驗證")
    controller = db.get(Node, settings.slurm_controller_id)
    if controller is None or not controller.enabled:
        raise HTTPException(422, f"控制節點 {settings.slurm_controller_id} 尚未啟用")
    if controller.id not in {n.id for n in nodes}:
        nodes.append(controller)
    compute_nodes = [db.get(Node, n) for n in draft.host_ids]
    script = job_script(project, params, compute_nodes, image_sha)
    payload = {
        "script": script,
        "job": {
            "name": f"srs-{plan_id}", "comment": f"srs:{plan_id}", "partition": "srs",
            "current_working_directory": project.path,
            "environment": ["PATH=/usr/local/bin:/usr/bin:/bin", "SLURM_EXPORT_ENV=ALL"],
            "nodes": str(len(compute_nodes)), "tasks": len(compute_nodes),
            "cpus_per_task": params.get("cpus", 1),
            "memory_per_node": {"set": True, "number": params.get("memory_mb", 2048)},
            "time_limit": {"set": True, "number": params.get("time_limit_minutes", 5)},
            "required_nodes": [f"srs-{n.id}" for n in compute_nodes],
            "standard_output": "/srv/srs/results/%j.stdout", "standard_error": "/srv/srs/results/%j.stderr",
        },
    }
    if profile != "cpu":
        payload["job"]["tres_per_node"] = "gres/gpu:1"
    step = base_step(len(steps) + 1, controller, "透過 Slurm 提交固定訓練工作", "submit_job")
    step.update(kind="slurm_api", script=script, preconditions=[], timeout_seconds=30,
                execution_user=settings.slurm_owner,
                request={"method": "POST", "base_url": settings.slurm_base_url,
                         "path": f"/slurm/{settings.slurm_api_version}/job/submit", "body": payload},
                reconcile_request={"method": "GET", "path": f"/slurm/{settings.slurm_api_version}/jobs",
                                   "match_comment": f"srs:{plan_id}"},
                reason="表單的專案、資源與模式轉為實際 Slurm REST 請求；作業擁有者固定為專用運算帳號。",
                evidence=[{"project_id": project.id, "revision": project.revision, "container_sha256": image_sha}])
    steps.append(step)
    return steps, issues


def build_config_step(draft, node, settings):
    path = draft.parameters.get("path", CONFIG_PATH)
    if path != CONFIG_PATH:
        raise HTTPException(422, f"第一版僅允許管理 {CONFIG_PATH}")
    content = draft.parameters.get("content", {})
    if not isinstance(content, dict):
        raise HTTPException(422, "設定內容必須是 JSON object")
    validate_public_parameters(content)
    after = json.dumps(content, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    if len(after.encode()) > 65536:
        raise HTTPException(422, "設定檔超過 64KiB")
    evidence = node.facts.get("files", {}).get(path)
    issues = []
    if evidence is None and settings.mode != "demo":
        issues.append(f"尚無 {path} 的原始內容／不存在證據，請先核准預檢")
    evidence = evidence or {"exists": False}
    before = evidence.get("content", "")
    if type(evidence.get("exists")) is not bool:
        issues.append("設定檔預檢證據無有效的存在狀態，請重新核准預檢")
    if evidence.get("exists") and (
        not isinstance(before, str)
        or hashlib.sha256(before.encode()).hexdigest() != evidence.get("sha256")
    ):
        issues.append("設定檔原始內容與 SHA256 證據不一致，請重新核准預檢")
    if not isinstance(before, str):
        before = ""
    step = base_step(1, node, "備份並原子更新平台公開設定", "write_config")
    step.update(requires_sudo=True, execution_user="root")
    if evidence.get("exists"):
        step["preconditions"].append({"type": "file_sha256", "path": path, "sha256": evidence.get("sha256", "")})
    else:
        step["preconditions"].append({"type": "file_absent", "path": path})
    new_hash = hashlib.sha256(after.encode()).hexdigest()
    backup = path + ".srs-backup-" + new_hash[:12]
    script = f'''import hashlib, os, pathlib, secrets, stat
p = pathlib.PurePosixPath({path!r})
expected_exists = {evidence.get("exists") is True!r}
expected_sha256 = {evidence.get("sha256")!r}
directory_fds = []
directory_links = []
temp_name = None
parent_fd = None

def read_approved_original():
    try:
        fd = os.open(p.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent_fd)
    except FileNotFoundError:
        if expected_exists:
            raise RuntimeError("approved original is missing") from None
        return None, None
    with os.fdopen(fd, "rb") as stream:
        initial = os.fstat(stream.fileno())
        if not stat.S_ISREG(initial.st_mode):
            raise RuntimeError("configuration must be a regular file")
        raw = stream.read(65537)
        current = os.fstat(stream.fileno())
    if not expected_exists:
        raise RuntimeError("configuration appeared after approval")
    identity = lambda item: (item.st_dev, item.st_ino, item.st_mode, item.st_uid, item.st_gid,
                             item.st_size, item.st_mtime_ns, item.st_ctime_ns)
    if identity(initial) != identity(current):
        raise RuntimeError("configuration changed while being read")
    if len(raw) > 65536 or hashlib.sha256(raw).hexdigest() != expected_sha256:
        raise RuntimeError("configuration no longer matches approved SHA256")
    return raw, identity(current)

def verify_parent_chain():
    for ancestor_fd, component, opened_fd in directory_links:
        current = os.stat(component, dir_fd=ancestor_fd, follow_symlinks=False)
        opened = os.fstat(opened_fd)
        if not stat.S_ISDIR(current.st_mode) or (current.st_dev, current.st_ino) != (opened.st_dev, opened.st_ino):
            raise RuntimeError("configuration parent directory changed")

try:
    parent_fd = os.open("/", os.O_RDONLY | os.O_DIRECTORY)
    directory_fds.append(parent_fd)
    for component in p.parent.parts[1:]:
        try:
            child_fd = os.open(component, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent_fd)
        except FileNotFoundError:
            if expected_exists:
                raise RuntimeError("approved configuration parent is missing") from None
            os.mkdir(component, 0o755, dir_fd=parent_fd)
            child_fd = os.open(component, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent_fd)
        directory_links.append((parent_fd, component, child_fd))
        directory_fds.append(child_fd)
        parent_fd = child_fd
    original, original_identity = read_approved_original()
    if original is not None:
        backup_fd = os.open({PurePosixPath(backup).name!r},
                            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=parent_fd)
        with os.fdopen(backup_fd, "wb") as stream:
            stream.write(original)
            stream.flush()
            os.fsync(stream.fileno())
        os.fsync(parent_fd)
    candidate = ".srs-config-" + secrets.token_hex(16)
    fd = os.open(candidate, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=parent_fd)
    temp_name = candidate
    with os.fdopen(fd, "wb") as stream:
        stream.write({after.encode()!r})
        stream.flush()
        os.fchmod(stream.fileno(), 0o644)
        os.fsync(stream.fileno())
    verify_parent_chain()
    _, latest_identity = read_approved_original()
    if latest_identity != original_identity:
        raise RuntimeError("configuration identity changed before replacement")
    os.replace(temp_name, p.name, src_dir_fd=parent_fd, dst_dir_fd=parent_fd)
    temp_name = None
    os.fsync(parent_fd)
finally:
    if temp_name is not None:
        os.unlink(temp_name, dir_fd=parent_fd)
    for directory_fd in reversed(directory_fds):
        os.close(directory_fd)
print("configuration updated")
'''
    step["argv"] = ["/usr/bin/python3", "-B", "-c", script]
    step["script"] = script
    step["file_change"] = {
        "path": path, "before_sha256": evidence.get("sha256"), "after_sha256": new_hash,
        "before": before, "after": after, "backup_path": backup,
        "mode": "0644", "backup_mode": "0600", "backup_overwrite": False,
        "diff": "".join(difflib.unified_diff(before.splitlines(True), after.splitlines(True), fromfile=path, tofile=path)),
    }
    step["reason"] = "僅將已儲存的公開 JSON 設定寫至固定路徑；拒絕符號連結、排他建立備份，並在原子替換前再次核對原始摘要。"
    step["evidence"] = [{"source": "approved_preflight", "observed_at": node.observed_at, "file": evidence}]
    add_manifest(step, node, settings)
    return [step], issues

def build_plan(db, draft: Draft, settings: Settings) -> Plan:
    from srs.models import Execution, new_id
    plan_id = new_id()
    nodes = choose_nodes(db, draft.host_ids)
    validate_public_parameters(draft.parameters)
    issues = common_issues(nodes, settings)
    deployment_reviews = []
    if draft.operation == "preflight":
        steps = preflight_steps(db, nodes, draft.owner, settings)
        if "deployment_probe" in draft.parameters:
            from infra.evidence import deployment_probe_script, validate_deployment_spec
            probe = draft.parameters["deployment_probe"]
            if not isinstance(probe, dict) or probe.get("component") not in {"apptainer", "slurm", "platform"}:
                raise HTTPException(422, "部署預檢必須指定有效 component 與 spec")
            for node in nodes:
                public_node = {**node_binding(node), "hardware": node.hardware}
                try:
                    spec = validate_deployment_spec(probe["component"], public_node, probe.get("spec"))
                    script = deployment_probe_script(probe["component"], public_node, spec)
                except ValueError as error:
                    raise HTTPException(422, str(error)) from error
                step = base_step(len(steps) + 1, node, "核對已暫存部署檔案與系統前提", "deployment_probe", read_only=True)
                step.update(argv=["/usr/bin/python3", "-B", "-c", script], requires_sudo=True,
                            execution_user="root", timeout_seconds=120,
                            deployment_probe={"component": probe["component"], "spec": spec},
                            reason="以已審閱 sudo 身分唯讀檢查 OS、固定暫存套件與既有設定；只模擬依賴，不下載或安裝。",
                            evidence=[{"source": "user_selected_staged_artifacts", "component": probe["component"]}])
                add_manifest(step, node, settings)
                steps.append(step)
        if "cluster_probe" in draft.parameters:
            from srs.cluster_probe import cluster_probe_script, validate_probe_spec
            try:
                spec = validate_probe_spec(draft.parameters["cluster_probe"])
            except ValueError as error:
                raise HTTPException(422, str(error)) from error
            if {p["id"] for p in spec["peers"]} != {n.id for n in nodes}:
                raise HTTPException(422, "跨節點預檢必須與所選的兩台主機完全一致")
            for node in nodes:
                step = base_step(len(steps) + 1, node, "唯讀跨節點共享掛載與網路預檢", "cluster_probe", read_only=True)
                step.update(argv=["/usr/bin/python3", "-B", "-c", cluster_probe_script(spec, node.id)],
                            timeout_seconds=45, cluster_probe_spec=spec,
                            reason="核對現有 NFS 掛載與同一見證檔，並對所選 peer 的 TCP 6818 做連線檢查；不宣稱已驗證 NCCL 或跨機寫入。",
                            evidence=[{"source": "user_reviewed_probe_spec", "probe_id": spec["probe_id"]}])
                add_manifest(step, node, settings)
                steps.append(step)
    elif draft.operation == "submit_job":
        steps, extra = build_job_steps(db, draft, nodes, settings, plan_id)
        issues += extra + common_issues(nodes, settings)
    elif draft.operation == "cancel_job":
        job = db.get(Job, draft.parameters.get("job_id", ""))
        if job is None or job.owner != draft.owner:
            raise HTTPException(404, "找不到可取消的工作")
        if nodes[0].id != settings.slurm_controller_id or len(nodes) != 1:
            raise HTTPException(422, f"取消請求必須送至控制節點 {settings.slurm_controller_id}")
        if job.status.upper() in {"COMPLETED", "CANCELLED", "FAILED", "TIMEOUT"}:
            raise HTTPException(409, "工作已結束，不需取消")
        step = base_step(1, nodes[0], "取消此帳號已登錄的 Slurm 工作", "cancel_job")
        step.update(kind="slurm_api", execution_user=settings.slurm_owner, preconditions=[],
                    request={"method": "DELETE", "base_url": settings.slurm_base_url,
                             "path": f"/slurm/{settings.slurm_api_version}/job/{job.slurm_job_id}", "body": None},
                    reason="依據所選工作及本系統擁有權紀錄取消單一作業，不取消其他使用者或其他作業。",
                    evidence=[{"job_id": job.id, "slurm_job_id": job.slurm_job_id, "owner": job.owner}])
        steps = [step]
    elif draft.operation == "write_config":
        if len(nodes) != 1:
            raise HTTPException(422, "設定變更一次只選擇一台主機")
        steps, extra = build_config_step(draft, nodes[0], settings)
        issues += extra
    elif draft.operation in {"collect_results", "cleanup"}:
        from srs.results import build_cleanup_steps, build_result_steps
        builder = build_result_steps if draft.operation == "collect_results" else build_cleanup_steps
        steps, extra = builder(db, draft, nodes[0], settings, base_step, add_manifest)
        issues += extra
    elif draft.operation == "deploy":
        from infra.catalog import render_deployment
        component = draft.parameters.get("component", "slurm")
        if component not in {"slurm", "apptainer", "platform"}:
            raise HTTPException(422, "不支援的部署元件")
        steps = []
        for node in nodes:
            evidence = node.facts.get("deploy", {}).get(component)
            if evidence:
                source = db.get(Execution, evidence.get("execution_id", ""))
                if (not source or source.status != "succeeded" or source.owner != draft.owner or source.mode != settings.mode
                        or source.approved_hash != content_hash(source.plan_document)
                        or node_binding(node) not in source.plan_document.get("inventory", [])):
                    issues.append(f"{node.alias} 的部署證據無法追溯至目前主機身分下成功的已核准執行")
                    evidence = None
            public_node = {**node_binding(node), "hardware": node.hardware}
            review = render_deployment(component, public_node, evidence)
            deployment_reviews.append(review)
            issues += [node.alias + ": " + reason for reason in review["issues"]]
            for rendered in review["steps"]:
                step = base_step(len(steps) + 1, node, rendered["title"], "deploy")
                step.update(rendered)
                step["id"] = f"step-{len(steps) + 1}"
                add_manifest(step, node, settings)
                steps.append(step)
    else:
        steps = []
        issues.append("清理必須先選定已登錄成果及檔案摘要；不接受任意路徑或遞迴刪除")
    if settings.mode != "demo" and draft.operation not in {"preflight", "cancel_job", "collect_results"}:
        for node in nodes:
            checked = node.facts.get("preflight_at")
            if not checked or parse_time(checked) < datetime.now(UTC) - timedelta(hours=24):
                issues.append(f"{node.alias} 缺少 24 小時內的已核准預檢證據")
    document = {
        "schema_version": 1, "mode": settings.mode, "authorization_kind": "one_shot",
        "plan_id": plan_id, "inventory": [node_binding(n) for n in nodes], "steps": steps,
        "impact": {"operation": draft.operation, "host_ids": draft.host_ids,
                   "gpu_policy": "whole_gpu_exclusive; 250 GPU1 excluded", "retention": "no automatic log deletion"},
        "failure_policy": "stop dependent steps; no unapproved repair or rollback; reconcile unknown outcomes",
        "source": {"draft_id": draft.id, "draft_revision": draft.revision, "operation": draft.operation,
                   "parameters": copy.deepcopy(draft.parameters), "template_version": TEMPLATE_VERSION,
                   "generated_at": now(), "owner": draft.owner},
    }
    if deployment_reviews:
        document["deployment_reviews"] = deployment_reviews
    for index, step in enumerate(document["steps"]):
        step["depends_on"] = [document["steps"][index - 1]["id"]] if index else []
        if step["kind"] == "slurm_api":
            step["request"]["headers"] = {
                "X-SLURM-USER-NAME": {"secret_ref": "SLURM_USERNAME"},
                "X-SLURM-USER-TOKEN": {"secret_ref": "SLURM_TOKEN"},
            }
    return Plan(id=plan_id, draft_id=draft.id, revision=draft.revision, title=draft.title,
                status="awaiting_approval", content_hash=content_hash(document), document=document,
                owner=draft.owner, issues=list(dict.fromkeys(issues)))


def build_schedule_document(db, schedule: Schedule, settings: Settings) -> tuple[dict, list[str]]:
    if schedule.operation not in READ_OPERATIONS:
        raise HTTPException(422, "排程只能執行固定唯讀動作")
    nodes = choose_nodes(db, schedule.host_ids)
    issues = common_issues(nodes, settings)
    steps = []
    if schedule.operation == "host_snapshot":
        for node in nodes:
            step = base_step(len(steps) + 1, node, "唯讀 CPU／RAM／GPU 快照", "host_snapshot", read_only=True)
            step["argv"] = ["/usr/bin/python3", "-B", "-c", HOST_PROBE]
            step["reason"] = "執行已核准的固定監控查詢，不變更主機、程序或服務。"
            add_manifest(step, node, settings)
            steps.append(step)
    else:
        if schedule.host_ids != [settings.slurm_controller_id]:
            raise HTTPException(422, f"工作狀態／日誌同步只允許控制節點 {settings.slurm_controller_id}")
        ids = schedule.scope.get("job_ids", [])
        if not ids:
            issues.append("請明確選擇至少一個已登錄工作；排程不自動擴大至未來工作")
        for job_id in ids:
            job = db.get(Job, job_id)
            if not job or job.owner != schedule.owner:
                raise HTTPException(422, "排程不能存取其他帳號或未登錄工作")
            step = base_step(len(steps) + 1, nodes[0], "同步已登錄工作", schedule.operation, read_only=True)
            step["reason"] = "依明確核准的工作 ID 同步，沒有提交或取消權限。"
            step["evidence"] = [{"job_id": job.id, "slurm_job_id": job.slurm_job_id}]
            if schedule.operation == "job_status":
                step.update(kind="slurm_api", preconditions=[], execution_user=settings.slurm_owner,
                            request={"method": "GET", "base_url": settings.slurm_base_url,
                                     "path": f"/slurm/{settings.slurm_api_version}/job/{job.slurm_job_id}", "body": None})
            else:
                from srs.job_logs import script_builder
                job_ref = job.slurm_job_id
                if settings.mode != "demo" and not re.fullmatch(r"[0-9]+", job_ref):
                    issues.append("實機日誌同步需要有效的數字 Slurm job ID")
                script = script_builder(job.id, job_ref)
                step["argv"] = ["/usr/bin/python3", "-B", "-c", script]
                step["job_log_scope"] = {"job_id": job.id, "slurm_job_id": job_ref, "streams": ["stdout", "stderr"]}
                step["reason"] += " 每次自持久化 byte cursor 續讀 64KiB；尚有資料時由下一次授權排程繼續，不截去中間輸出。"
                add_manifest(step, nodes[0], settings)
            steps.append(step)
    for index, step in enumerate(steps):
        step["depends_on"] = [steps[index - 1]["id"]] if index else []
        if step["kind"] == "slurm_api":
            step["request"]["headers"] = {
                "X-SLURM-USER-NAME": {"secret_ref": "SLURM_USERNAME"},
                "X-SLURM-USER-TOKEN": {"secret_ref": "SLURM_TOKEN"},
            }
    doc = {
        "schema_version": 1, "mode": settings.mode, "authorization_kind": "schedule",
        "inventory": [node_binding(n) for n in nodes], "steps": steps,
        "schedule": {"id": schedule.id, "revision": schedule.revision, "operation": schedule.operation,
                     "interval_seconds": schedule.interval_seconds, "starts_at": schedule.starts_at,
                     "expires_at": schedule.expires_at, "scope": schedule.scope,
                     "max_concurrent": 1, "catch_up": False},
        "impact": {"read_only": True, "host_ids": schedule.host_ids},
        "failure_policy": "record failure; skip overlaps; no catch-up; stop at expiry",
        "source": {"owner": schedule.owner, "template_version": TEMPLATE_VERSION, "generated_at": now()},
    }
    return doc, issues


def validate_inventory(db, document: dict) -> list[str]:
    errors = []
    for binding in document.get("inventory", []):
        node = db.get(Node, binding["id"])
        if not node or node_binding(node) != binding:
            errors.append(f"主機 {binding['id']} 設定已變更，請重新產生與核准計畫")
    return errors
