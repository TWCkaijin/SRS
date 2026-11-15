"""Slurm cluster configuration previews and staged batches for the reviewed executor."""

import asyncio
import hashlib
import json
import re
import secrets
import shlex
import time

from fastapi import Depends, HTTPException
from pydantic import Field, SecretStr, model_validator

from srs.host_access import connect, host_config
from srs.models import new_id
from srs.schemas import StrictModel
from srs.service import audit
from srs.ssh_hosts import SSHHostInput

PROBE = r"""import json,os,platform,shutil,subprocess,pathlib,pwd
r={'hostname':platform.node().split('.')[0],'system':platform.system(),'uid':os.getuid(),'gid':os.getgid(),'username':pwd.getpwuid(os.getuid()).pw_name,'cpus':os.cpu_count(),'memory_mb':0,'slurm_version':None,'slurmd_config':None,'munge':bool(shutil.which('munge')),'gpus':[],'services':{},'existing_config':False}
for line in pathlib.Path('/proc/meminfo').read_text().splitlines():
 if line.startswith('MemTotal:'):r['memory_mb']=int(line.split()[1])//1024
for binary,args,key in [('slurmd',['-V'],'slurm_version'),('slurmd',['-C'],'slurmd_config')]:
 exe=shutil.which(binary) or ('/usr/sbin/'+binary if pathlib.Path('/usr/sbin/'+binary).exists() else None)
 if exe:
  p=subprocess.run([exe,*args],capture_output=True,text=True,timeout=10)
  if p.returncode==0:r[key]=p.stdout.strip()[:4096]
for name in ('slurmctld','slurmd','munge'):
 try:r['services'][name]=subprocess.run(['systemctl','is-active',name],capture_output=True,text=True,timeout=5).stdout.strip()
 except OSError:r['services'][name]='unknown'
r['existing_config']=any(pathlib.Path(p).exists() for p in ('/etc/slurm/slurm.conf','/etc/slurm-llnl/slurm.conf','/usr/local/etc/slurm.conf'))
try:
 p=subprocess.run(['nvidia-smi','--query-gpu=index,uuid,name','--format=csv,noheader,nounits'],capture_output=True,text=True,timeout=10)
 if p.returncode==0:
  import csv,io
  for a in csv.reader(io.StringIO(p.stdout),skipinitialspace=True):
   if len(a)==3:r['gpus'].append({'index':int(a[0]),'uuid':a[1],'name':a[2]})
except OSError:pass
print(json.dumps(r))
"""


class SlurmNode(StrictModel):
    host_id: str = Field(min_length=1, max_length=64)
    name: str = Field(pattern=r"^[a-zA-Z][a-zA-Z0-9_-]{0,62}$")
    address: str = Field(min_length=1, max_length=255)
    cpus: int = Field(ge=1, le=1024)
    memory_mb: int = Field(ge=256, le=16777216)
    gpu_indices: list[int] = Field(default_factory=list, max_length=128)


class SlurmInput(StrictModel):
    cluster: str = Field(default="research", pattern=r"^[a-z][a-z0-9_-]{0,31}$")
    partition: str = Field(default="research", pattern=r"^[a-z][a-z0-9_-]{0,31}$")
    controller_id: str = Field(min_length=1, max_length=64)
    nodes: list[SlurmNode] = Field(min_length=1, max_length=32)
    max_minutes: int = Field(default=1440, ge=1, le=10080)

    @model_validator(mode="after")
    def valid_nodes(self):
        if len({n.host_id for n in self.nodes}) != len(self.nodes) or len(
            {n.name for n in self.nodes}
        ) != len(self.nodes):
            raise ValueError("節點 ID 與名稱不可重複")
        if self.controller_id not in [n.host_id for n in self.nodes]:
            raise ValueError("控制節點須在所選節點內")
        for node in self.nodes:
            node.address = SSHHostInput.valid_host(node.address)
            if len(set(node.gpu_indices)) != len(node.gpu_indices) or any(
                i < 0 or i > 127 for i in node.gpu_indices
            ):
                raise ValueError("GPU 索引不正確")
        return self


def render_configuration(payload, facts):
    head = next(n for n in payload.nodes if n.host_id == payload.controller_id)
    lines = [
        f"ClusterName={payload.cluster}",
        f"SlurmctldHost={facts[head.host_id]['hostname']}({head.address})",
        "SlurmUser=slurm",
        "AuthType=auth/munge",
        "CredType=cred/munge",
        "SlurmctldPort=6817",
        "SlurmdPort=6818",
        "StateSaveLocation=/var/spool/slurmctld",
        "SlurmdSpoolDir=/var/spool/slurmd",
        "SlurmctldLogFile=/var/log/slurm/slurmctld.log",
        "SlurmdLogFile=/var/log/slurm/slurmd.log",
        "ProctrackType=proctrack/cgroup",
        "TaskPlugin=task/cgroup,task/affinity",
        "SelectType=select/cons_tres",
        "SelectTypeParameters=CR_CPU_Memory",
        "SchedulerType=sched/backfill",
        "ReturnToService=0",
    ]
    if any(n.gpu_indices for n in payload.nodes):
        lines.append("GresTypes=gpu")
    files = {}
    for node in payload.nodes:
        observed = facts[node.host_id]
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,252}", observed["hostname"]):
            raise ValueError("主機回傳無效 hostname")
        if node.cpus > observed["cpus"] or node.memory_mb > observed["memory_mb"] - 512:
            raise ValueError(f"{node.name} 的 CPU／RAM 超過實際資源，RAM 至少需保留 512 MiB")
        available = {g["index"] for g in observed["gpus"]}
        if not set(node.gpu_indices) <= available:
            raise ValueError(f"{node.name} 的 GPU 選擇已變更")
        line = f"NodeName={node.name} NodeHostname={observed['hostname']} NodeAddr={node.address} CPUs={node.cpus} RealMemory={node.memory_mb} State=UNKNOWN"
        if node.gpu_indices:
            line += f" Gres=gpu:{len(node.gpu_indices)}"
        lines.append(line)
        files[node.host_id] = {
            "/etc/slurm/gres.conf": "".join(
                f"NodeName={node.name} Name=gpu File=/dev/nvidia{i}\n" for i in sorted(node.gpu_indices)
            )
            or "# CPU-only node\n"
        }
    lines.append(
        f"PartitionName={payload.partition} Nodes={','.join(n.name for n in payload.nodes)} Default=YES MaxTime={payload.max_minutes} State=UP OverSubscribe=NO"
    )
    config = "\n".join(lines) + "\n"
    for fileset in files.values():
        fileset.update(
            {
                "/etc/slurm/slurm.conf": config,
                "/etc/slurm/cgroup.conf": "CgroupPlugin=autodetect\nConstrainCores=yes\nConstrainRAMSpace=yes\nConstrainDevices=yes\n",
            }
        )
    for node in payload.nodes:
        files[node.host_id]["/etc/systemd/system/slurmd.service.d/srs.conf"] = (
            "[Service]\nExecStart=\nExecStart=/usr/sbin/slurmd -D -s -N " + node.name + "\n"
        )
    return files


class InspectInput(StrictModel):
    host_ids: list[str] = Field(min_length=1, max_length=32)
    training_user: str = Field(pattern=r"^[a-z_][a-z0-9_-]{0,31}$")


class SlurmPreview(SlurmInput):
    training_user: str = Field(pattern=r"^[a-z_][a-z0-9_-]{0,31}$")


class SlurmApply(StrictModel):
    token: str = Field(min_length=20, max_length=128)
    content_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    sudo_passwords: dict[str, SecretStr] = Field(default_factory=dict, max_length=32)


def bootstrap_issues(facts, training_user):
    issues = []
    if facts.get("os", {}).get("ID") != "ubuntu" or facts.get("os", {}).get("VERSION_ID") != "24.04":
        issues.append("目前新叢集安裝支援 Ubuntu 24.04。")
    if not facts.get("cgroup2") or not facts.get("systemd"):
        issues.append("需要 systemd 與 cgroup v2。")
    if (
        facts.get("existing_config")
        or facts.get("munge_key_exists")
        or any(facts.get("services", {}).get(s) == "active" for s in ("slurmctld", "slurmd", "munge"))
    ):
        issues.append("偵測到既有 Slurm／MUNGE，不能以新叢集流程覆寫。")
    if facts.get("policy_exists"):
        issues.append("主機已有 policy-rc.d，請由管理員確認套件啟動政策。")
    if not facts.get("accounts", {}).get(training_user):
        issues.append("訓練帳號尚未存在。請先建立各節點一致的帳號。")
    for name, number in [("slurm", 64030), ("munge", 64031)]:
        account = facts.get("accounts", {}).get(name)
        if account and account != {"uid": number, "gid": number}:
            issues.append(f"{name} 服務帳號 UID/GID 與本次計畫 {number} 不一致。")
        if (
            facts.get("reserved_ids", {}).get(str(number), name) != name
            or facts.get("reserved_ids", {}).get("g" + str(number), name) != name
        ):
            issues.append(f"預定服務 UID/GID {number} 已被使用。")
    if facts.get("package_error"):
        issues.append(facts["package_error"])
    if abs(time.time() - facts.get("observed_time", 0)) > 30:
        issues.append("主機時間與控制平台差異超過 30 秒。")
    return issues


def register_slurm_routes(app, factory, permission, settings):
    from srs.models import SessionRecord, now
    from srs.slurm_bootstrap import BOOTSTRAP, EXTRA_PROBE, PACKAGE_PLAN

    manage = Depends(permission("hosts.terminal"))
    approve = Depends(permission("plan.approve"))
    plans = {}
    tasks = set()
    locked = set()
    limit = asyncio.Semaphore(4)
    probe_script = PROBE.replace("print(json.dumps(r))", "") + PACKAGE_PLAN + EXTRA_PROBE

    async def inspect_hosts(ids, user):
        hosts = {identifier: host_config(factory, identifier) for identifier in dict.fromkeys(ids)}

        async def check(identifier, host):
            async with limit:
                try:
                    async with asyncio.timeout(70):
                        async with await connect(host, settings) as conn:
                            result = await conn.run(
                                shlex.join(["/usr/bin/python3", "-c", probe_script, user]),
                                check=True,
                                timeout=60,
                            )
                            facts = json.loads(result.stdout)
                            return {
                                "host_id": identifier,
                                "facts": facts,
                                "issues": bootstrap_issues(facts, user),
                            }
                except Exception:
                    return {
                        "host_id": identifier,
                        "facts": {},
                        "issues": ["SSH 檢查未完成。請確認連線、Python 3 與讀取權限。"],
                    }

        rows = await asyncio.gather(*(check(identifier, host) for identifier, host in hosts.items()))
        return hosts, rows

    @app.post("/api/v1/slurm/inspect")
    async def inspect(payload: InspectInput, session=manage):
        _, rows = await inspect_hosts(payload.host_ids, payload.training_user)
        return {"rows": rows}

    @app.post("/api/v1/slurm/preview")
    async def preview(payload: SlurmPreview, session=manage):
        for token in list(plans):
            if plans[token]["expires"] < time.monotonic() and plans[token]["status"] != "running":
                plans.pop(token)
        if len(plans) >= 64:
            raise HTTPException(429, "預覽過多，請稍後重試")
        hosts, rows = await inspect_hosts([n.host_id for n in payload.nodes], payload.training_user)
        facts = {r["host_id"]: r["facts"] for r in rows}
        accounts = {
            tuple(r["facts"].get("accounts", {}).get(payload.training_user, {}).values())
            for r in rows
            if r["facts"].get("accounts", {}).get(payload.training_user)
        }
        if len(accounts) > 1:
            for row in rows:
                row["issues"].append("訓練帳號 UID／GID 在節點間不一致。")
        versions = {
            p["version"] for r in rows for p in r["facts"].get("packages", []) if p["name"] == "slurmd"
        }
        if len(versions) > 1:
            for row in rows:
                row["issues"].append("節點的 Slurm 套件版本不同，請統一 APT 來源與索引。")
        names = [r["facts"].get("hostname") for r in rows]
        if len(set(names)) != len(names):
            for row in rows:
                row["issues"].append("各節點的實際 hostname 必須不同。")
        files = {}
        try:
            files = render_configuration(payload, facts)
        except (KeyError, TypeError, ValueError) as exc:
            for row in rows:
                row["issues"].append(
                    str(exc) if isinstance(exc, ValueError) else "硬體資料不完整，無法產生設定。"
                )
        document = {
            "cluster": payload.cluster,
            "partition": payload.partition,
            "controller_id": payload.controller_id,
            "training_user": payload.training_user,
            "nodes": [n.model_dump() for n in payload.nodes],
            "bootstrap_sha256": hashlib.sha256(BOOTSTRAP.encode()).hexdigest(),
            "files": files,
            "packages": {r["host_id"]: r["facts"].get("packages", []) for r in rows},
            "actions": [
                "建立 slurm UID/GID 64030、munge UID/GID 64031（若不存在）",
                "下載並驗證預覽清單中的 Ubuntu 套件 SHA256，再安裝新增套件",
                "所有節點配置相同 Slurm 設定與新 MUNGE 共用金鑰",
                "設定 cgroup CPU／RAM／GPU 限制",
                "啟用 MUNGE、控制節點 slurmctld 與所有運算節點 slurmd",
                "讀取 sinfo 檢查節點是否註冊；不提交訓練工作",
            ],
        }
        digest = hashlib.sha256(json.dumps(document, sort_keys=True).encode()).hexdigest()
        token = secrets.token_urlsafe(32)
        plan = {
            "id": new_id(),
            "owner": session.token_hash,
            "username": session.username,
            "hosts": hosts,
            "document": document,
            "rows": rows,
            "ready": all(not r["issues"] for r in rows),
            "content_hash": digest,
            "expires": time.monotonic() + 1800,
            "status": "preview",
            "results": {},
            "verification": None,
        }
        plans[token] = plan
        with factory.begin() as db:
            audit(
                db,
                session.username,
                "slurm.preview",
                plan["id"],
                {"host_ids": list(hosts), "content_hash": digest, "document": document},
            )
        return {
            "token": token,
            **{k: plan[k] for k in ("id", "document", "rows", "ready", "content_hash", "status")},
        }

    def owned_plan(token, session):
        plan = plans.get(token)
        if not plan or plan["owner"] != session.token_hash:
            raise HTTPException(404, "找不到此登入階段的 Slurm 計畫；服務重啟後需重新檢查")
        return plan

    @app.get("/api/v1/slurm/status/{token}")
    def status(token: str, session=manage):
        plan = owned_plan(token, session)
        return {k: plan[k] for k in ("id", "status", "results", "verification")}

    def validate_access(plan):
        with factory() as db:
            session = db.get(SessionRecord, plan["owner"])
            if (
                not session
                or session.expires_at <= now()
                or session.auth_revision
                != hashlib.sha256(
                    (
                        settings.web_admin_username + "\x00" + settings.web_admin_password.get_secret_value()
                    ).encode()
                ).hexdigest()
                or not {"hosts.terminal", "plan.approve"} <= settings.permission_set
            ):
                raise RuntimeError("authorization expired")
        for identifier, expected in plan["hosts"].items():
            if host_config(factory, identifier, expected["revision"]) != expected:
                raise RuntimeError("host changed")

    async def execute(plan, passwords):
        import base64

        shared_key = base64.b64encode(secrets.token_bytes(1024)).decode()
        ordered = sorted(
            plan["hosts"], key=lambda identifier: identifier != plan["document"]["controller_id"]
        )
        phase = "precheck"
        host_id = ordered[0]
        try:
            # Validate privilege on every node before any installation.
            for host_id in ordered:
                validate_access(plan)
                plan["results"][host_id] = {"phase": phase, "status": "running"}
                host = plan["hosts"][host_id]
                async with asyncio.timeout(35):
                    async with await connect(host, settings) as conn:
                        command = (
                            ["/usr/bin/id", "-u"]
                            if host["username"] == "root"
                            else ["sudo", "-S", "-k", "-p", "", "--", "/usr/bin/id", "-u"]
                        )
                        result = await conn.run(
                            shlex.join(command),
                            input=passwords.get(host_id, "") + "\n",
                            check=True,
                            timeout=25,
                        )
                        if result.stdout.strip() != "0":
                            raise RuntimeError("root required")
            for phase in ("install", "configure", "activate"):
                for host_id in ordered:
                    validate_access(plan)
                    plan["results"][host_id] = {"phase": phase, "status": "running"}
                    node = next(n for n in plan["document"]["nodes"] if n["host_id"] == host_id)
                    payload = {
                        "phase": phase,
                        "id": plan["id"],
                        "files": plan["document"]["files"][host_id],
                        "packages": plan["document"]["packages"][host_id],
                        "munge_key": shared_key,
                        "node_name": node["name"],
                        "training_user": plan["document"]["training_user"],
                        "expected_facts": next(r["facts"] for r in plan["rows"] if r["host_id"] == host_id),
                        "controller": host_id == plan["document"]["controller_id"],
                    }
                    host = plan["hosts"][host_id]
                    command = ["/usr/bin/python3", "-c", BOOTSTRAP]
                    if host["username"] != "root":
                        command = ["sudo", "-S", "-k", "-p", "", "--", *command]
                    async with asyncio.timeout(900):
                        async with await connect(host, settings) as conn:
                            result = await conn.run(
                                shlex.join(command),
                                input=passwords.get(host_id, "") + "\n" + json.dumps(payload) + "\n",
                                check=True,
                                timeout=880,
                            )
                            output = json.loads(result.stdout)
                            plan["results"][host_id] = {"phase": output["phase"], "status": "success"}
                    with factory.begin() as db:
                        audit(db, plan["username"], "slurm." + phase, plan["id"], {"host_id": host_id})
            phase = "verify"
            validate_access(plan)
            async with asyncio.timeout(45):
                async with await connect(plan["hosts"][plan["document"]["controller_id"]], settings) as conn:
                    result = await conn.run(
                        "sinfo --Node --noheader --format='%N|%T'", check=True, timeout=30
                    )
                    states = dict(
                        line.strip().split("|", 1) for line in result.stdout.splitlines() if "|" in line
                    )
                    expected = {n["name"] for n in plan["document"]["nodes"]}
                    healthy = expected <= states.keys() and all(
                        states[n] in {"idle", "allocated", "mixed"} for n in expected
                    )
                    plan["verification"] = {
                        "nodes": states,
                        "ready": healthy,
                        "note": "節點註冊檢查；尚未進行實際訓練與跨節點 GPU 通訊測試。",
                    }
                    plan["status"] = "ready" if healthy else "configured"
        except asyncio.CancelledError:
            plan["status"] = "unconfirmed"
            plan["results"][host_id] = {
                "phase": phase,
                "status": "unconfirmed",
                "error": "服務中斷，遠端可能已部分變更。請檢查執行紀錄。",
            }
            raise
        except Exception:
            plan["status"] = "unconfirmed"
            plan["results"][host_id] = {
                "phase": phase,
                "status": "unconfirmed",
                "error": "此步驟未確認完成。已完成的主機變更保留，未執行後續步驟。請檢查 sudo、套件、systemd 與節點網路；不自動重試。",
            }
        finally:
            passwords.clear()
            shared_key = ""
            locked.difference_update(plan["hosts"])
            plan["expires"] = time.monotonic() + 86400
            with factory.begin() as db:
                audit(
                    db, plan["username"], "slurm." + plan["status"], plan["id"], {"results": plan["results"]}
                )

    @app.post("/api/v1/slurm/apply", status_code=202)
    async def apply(payload: SlurmApply, session=manage, approver=approve):
        plan = owned_plan(payload.token, session)
        if (
            plan["status"] != "preview"
            or not plan["ready"]
            or plan["expires"] < time.monotonic()
            or payload.content_hash != plan["content_hash"]
        ):
            raise HTTPException(409, "計畫已變更、過期、尚未通過檢查或已執行；請重新預覽")
        if locked.intersection(plan["hosts"]):
            raise HTTPException(409, "所選主機已有進行中的設定")
        try:
            validate_access(plan)
        except Exception:
            raise HTTPException(409, "主機或登入權限已變更，請重新預覽") from None
        if any(k not in plan["hosts"] for k in payload.sudo_passwords):
            raise HTTPException(422, "sudo 密碼包含未選取主機")
        locked.update(plan["hosts"])
        plan["status"] = "running"
        with factory.begin() as db:
            audit(db, session.username, "slurm.approved", plan["id"], {"content_hash": plan["content_hash"]})
        task = asyncio.create_task(
            execute(plan, {k: v.get_secret_value() for k, v in payload.sudo_passwords.items()})
        )
        tasks.add(task)
        task.add_done_callback(tasks.discard)
        return {"id": plan["id"], "status": "running"}
