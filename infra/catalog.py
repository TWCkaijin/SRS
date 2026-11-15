"""Pure review catalog. This module cannot SSH, start VMs, or install packages."""

from __future__ import annotations

import difflib
import hashlib
import json
import re
from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[1]
COMMON_REQUIREMENTS = [
    "target_identity_and_pinned_host_key",
    "fresh_os_architecture_and_privilege_evidence",
    "immutable_inventory_revision",
    "verified_staged_artifact_sha256_and_provenance",
    "reviewed_before_and_after_file_diff",
    "maintenance_window_and_current_workload_check",
    "backup_or_absence_evidence_for_every_destination",
]
COMPONENTS = {
    "slurm": {
        "files": ["infra/versions.lock.json", "infra/deploy/install-local-package.sh.in",
                  "infra/deploy/install-platform-file.sh.in", "infra/slurm/slurm.conf.in",
                  "infra/slurm/cgroup.conf", "infra/slurm/gres.conf.in"],
        "requirements": ["verified_slurm_26_05_5_debs_and_dependencies", "slurmd_C_and_reserved_os_resources",
                         "consistent_test_user_UID_GID_and_munge_key_reference", "private_peer_connectivity",
                         "cgroup_v2_device_enforcement", "selected_GPU_UUID_to_device_mapping",
                         "separate_service_start_and_restart_review"],
        "destinations": {"infra/slurm/slurm.conf.in": "/etc/slurm/slurm.conf",
                         "infra/slurm/cgroup.conf": "/etc/slurm/cgroup.conf",
                         "infra/slurm/gres.conf.in": "/etc/slurm/gres.conf"},
        "impact": "Installs reviewed Slurm packages and configuration. May run package maintainer scripts. Does not authorize driver, PAM, firewall, account, or running-job changes.",
    },
    "apptainer": {
        "files": ["infra/versions.lock.json", "infra/deploy/install-local-package.sh.in"],
        "requirements": ["verified_apptainer_1_5_5_deb_and_dependencies", "unprivileged_user_namespaces_and_FUSE",
                         "GPU_driver_compatibility_if_CUDA", "non_setuid_package_selection"],
        "destinations": {},
        "impact": "Installs a verified non-setuid Apptainer package; does not install or replace NVIDIA drivers.",
    },
    "platform": {
        "files": ["infra/versions.lock.json", "infra/deploy/install-platform-file.sh.in",
                  "infra/systemd/srs-api.service.in", "infra/systemd/srs-worker.service.in",
                  "infra/systemd/srs-scheduler.service.in"],
        "requirements": ["verified_application_release_and_dependency_lock", "separate_api_and_executor_accounts",
                         "executor_only_credential_file_permissions", "MariaDB_backup_and_migration_review",
                         "API_entrypoint_and_worker_command_match_release", "separate_service_start_review"],
        "destinations": {"infra/systemd/srs-api.service.in": "/etc/systemd/system/srs-api.service",
                         "infra/systemd/srs-worker.service.in": "/etc/systemd/system/srs-worker.service",
                         "infra/systemd/srs-scheduler.service.in": "/etc/systemd/system/srs-scheduler.service"},
        "impact": "Stages reviewed systemd unit files. Credentials are supplied separately to the executor. No automatic service enable/start, migration, or remote connection.",
    },
}


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def deployment_review(component: str) -> dict:
    """Return template hashes, exact scripts, destinations and unresolved prerequisites.

    Main planner must bind real evidence and a host, render validated substitutions,
    include staged bytes/diffs and service actions, then hash the final document.
    This catalog result is deliberately blocked; it is never executable approval.
    """
    if component not in COMPONENTS:
        raise ValueError("Unknown deploy component")
    config = COMPONENTS[component]
    files = [{"path": relative, "sha256": sha256_file(ROOT / relative),
              "destination": config["destinations"].get(relative), "text": (ROOT / relative).read_text()}
             for relative in config["files"]]
    hash_input = [{"path": file["path"], "sha256": file["sha256"]} for file in files]
    content_hash = hashlib.sha256(json.dumps(hash_input, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    requirements = [*COMMON_REQUIREMENTS, *config["requirements"]]
    scripts = [file for file in files if file["path"].endswith(".sh.in")]
    steps = [{"id": "template-" + str(index), "title": Path(file["path"]).name,
              "kind": "ssh", "argv": ["/bin/sh", "-s"], "script": file["text"],
              "requires_sudo": True, "execution_user": "root", "timeout_seconds": 120,
              "template_sha256": file["sha256"], "unresolved_template": True,
              "preconditions": requirements, "on_failure": "stop_and_report_no_blind_retry"}
             for index, file in enumerate(scripts, 1)]
    return {"schema_version": 1, "component": component, "status": "blocked_template",
            "catalog_hash": content_hash, "files": files, "steps": steps,
            "file_changes": [{"destination": file["destination"], "template_path": file["path"],
                              "template_sha256": file["sha256"], "before": None, "after": file["text"],
                              "diff_verified": False} for file in files if file["destination"]],
            "required_evidence": requirements, "issues": ["Unbound evidence: " + value for value in requirements],
            "impact": config["impact"], "automatic_execution": False}


def all_template_hashes() -> dict:
    return {name: deployment_review(name)["catalog_hash"] for name in COMPONENTS}


STAGING = "/srv/srs/staging/"
SHA256 = re.compile(r"[a-f0-9]{64}\Z")
SAFE_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,100}\Z")


def _staged_path(value: object, suffix: str = "") -> bool:
    return (isinstance(value, str) and value.startswith(STAGING)
            and bool(SAFE_NAME.fullmatch(value.removeprefix(STAGING))) and value.endswith(suffix))


def _sha(value: object) -> bool:
    return isinstance(value, str) and bool(SHA256.fullmatch(value))


def _digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _substitute(template: str, values: dict[str, str]) -> str:
    rendered = template
    for key, value in values.items():
        rendered = rendered.replace("@@" + key + "@@", value)
    if "@@" in rendered:
        raise ValueError("Template contains unresolved placeholders")
    return rendered


def _slurm_configuration(evidence: dict, node: dict) -> dict[str, str]:
    """Build Slurm directives from bounded structured facts; never accept raw lines."""
    config = evidence.get("configuration", {})
    head = config.get("head_node", "")
    if not isinstance(head, str) or not SAFE_NAME.fullmatch(head):
        raise ValueError("A verified head_node is required")
    entries = config.get("nodes", [])
    if not isinstance(entries, list) or not 1 <= len(entries) <= 2:
        raise ValueError("Slurm pilot requires one or two verified nodes")
    lines, names, this_gpu = [], [], None
    for entry in entries:
        if not isinstance(entry, dict):
            raise ValueError("Invalid Slurm node evidence")
        node_id = entry.get("id", "")
        name = "srs-" + node_id if isinstance(node_id, str) else ""
        if not SAFE_NAME.fullmatch(name) or name in names:
            raise ValueError("Invalid or duplicate Slurm node id")
        cpus, memory = entry.get("cpus"), entry.get("real_memory_mb")
        observed_cpus, observed_memory = entry.get("observed_cpus"), entry.get("observed_memory_mb")
        if not (all(type(value) is int for value in (cpus, memory, observed_cpus, observed_memory))
                and 1 <= cpus <= observed_cpus <= 1024 and 256 <= memory <= observed_memory - 512):
            raise ValueError("Slurm CPU/RAM must be observed and reserve at least 512 MiB for the OS")
        gpu = entry.get("gpu")
        line = f"NodeName={name} CPUs={cpus} RealMemory={memory} State=UNKNOWN"
        if gpu is not None:
            if not (isinstance(gpu, dict) and SAFE_NAME.fullmatch(str(gpu.get("type", "")))
                    and re.fullmatch(r"GPU-[A-Za-z0-9-]{8,80}", str(gpu.get("uuid", "")))
                    and re.fullmatch(r"/dev/nvidia[0-9]{1,3}", str(gpu.get("device", "")))):
                raise ValueError("GPU requires an observed UUID, type and exact device mapping")
            line += f" Gres=gpu:{gpu['type']}:1"
        if node_id == node.get("id"):
            this_gpu = gpu
            if gpu:
                allowed = {item.get("uuid") for item in node.get("hardware", {}).get("gpus", []) if item.get("in_scope")}
                if gpu["uuid"] not in allowed:
                    raise ValueError("GPU UUID is not included in the approved inventory scope")
        names.append(name)
        lines.append(line)
    if head not in names or "srs-" + str(node.get("id")) not in names:
        raise ValueError("Head and destination node must appear in the selected Slurm nodes")
    lines.append(f"PartitionName=srs Nodes={','.join(names)} Default=YES MaxTime=00:05:00 State=UP OverSubscribe=NO")
    result = {
        "infra/slurm/slurm.conf.in": _substitute((ROOT / "infra/slurm/slurm.conf.in").read_text(),
                                                {"HEAD_NODE": head, "NODE_AND_PARTITION_LINES": "\n".join(lines)}),
        "infra/slurm/cgroup.conf": (ROOT / "infra/slurm/cgroup.conf").read_text(),
    }
    if this_gpu:
        result["infra/slurm/gres.conf.in"] = _substitute((ROOT / "infra/slurm/gres.conf.in").read_text(), {
            "NODE_NAME": "srs-" + node["id"], "GPU_TYPE": this_gpu["type"], "GPU_DEVICE_FILE": this_gpu["device"],
        })
    else:
        result["infra/slurm/gres.conf.in"] = "# CPU-only node: no GPU devices are assigned to this Slurm node.\n"
    return result


def render_deployment(component: str, node: dict, evidence: dict | None, *, max_age_seconds: int = 900) -> dict:
    """Render exact fixed-template steps using executor-collected evidence.

    The caller MUST verify evidence.execution_id identifies an owned, succeeded,
    same-mode execution whose approved document hashes correctly and binds the
    current inventory. This pure function cannot authenticate database rows.
    Never populate this evidence from unchecked draft parameters or request JSON.
    A 'ready' result means reviewable, not approved or already executed.
    """
    review = deployment_review(component)
    review.update(status="blocked", steps=[], file_changes=[], host_id=node.get("id"))
    review["issues"] = []
    issues = review["issues"]
    if not isinstance(evidence, dict):
        issues.append("No executor-collected deployment evidence exists for this host")
        return review
    execution_id = evidence.get("execution_id")
    if not isinstance(execution_id, str) or not SAFE_NAME.fullmatch(execution_id):
        issues.append("Deployment evidence must name its successful source execution")
    if evidence.get("schema_version") != 1 or evidence.get("component") != component:
        issues.append("Deployment evidence schema or component mismatch")
    binding = evidence.get("host", {})
    if not isinstance(binding, dict) or any(binding.get(key) != node.get(key)
            for key in ("id", "inventory_revision", "host_key_sha256")):
        issues.append("Deployment evidence does not match the current immutable host binding")
    try:
        observed = datetime.fromisoformat(evidence.get("observed_at", "").replace("Z", "+00:00"))
        if observed.tzinfo is None or not timedelta(0) <= datetime.now(UTC) - observed <= timedelta(seconds=max_age_seconds):
            raise ValueError
    except (ValueError, TypeError, AttributeError):
        issues.append("Deployment evidence is missing, future-dated, or expired")
    os_info = evidence.get("os", {})
    if not isinstance(os_info, dict) or not (os_info.get("id") == "ubuntu" and os_info.get("version_id") == "24.04"
                                           and os_info.get("architecture") in {"amd64", "arm64"}):
        issues.append("Deployment packages require observed Ubuntu 24.04 and a supported architecture")
        os_info = {}
    requirements = evidence.get("requirements", {})
    if not isinstance(requirements, dict):
        requirements = {}
    for requirement in review["required_evidence"]:
        proof = requirements.get(requirement, {})
        if not (isinstance(proof, dict) and proof.get("status") == "verified"
                and proof.get("execution_id") == execution_id and isinstance(proof.get("detail"), str)
                and 1 <= len(proof["detail"]) <= 4096):
            issues.append("Unbound evidence: " + requirement)
    review["evidence_execution_id"] = execution_id
    rendered = {}
    try:
        if component == "slurm":
            rendered = _slurm_configuration(evidence, node)
        elif component == "platform":
            rendered = {path: (ROOT / path).read_text() for path in COMPONENTS[component]["destinations"]}
    except (ValueError, TypeError, KeyError) as error:
        issues.append(str(error))
    package_steps = []
    packages = evidence.get("packages", [])
    if not isinstance(packages, list) or len(packages) > 32:
        issues.append("Packages must be a bounded list of verified staged files")
        packages = []
    if component in {"slurm", "apptainer"} and not packages:
        issues.append("A verified package and its complete dependency closure are required")
    if component == "platform" and packages:
        issues.append("Platform unit rendering does not authorize arbitrary package installation")
    primary = False
    seen_paths = set()
    for package in packages:
        if not isinstance(package, dict):
            issues.append("Invalid package evidence")
            continue
        path, sha = package.get("path"), package.get("sha256")
        name, version, arch = package.get("name"), package.get("version"), package.get("architecture")
        source = urlparse(package.get("source_url", "") if isinstance(package.get("source_url"), str) else "")
        if not (_staged_path(path, ".deb") and path not in seen_paths and _sha(sha)
                and sha == package.get("source_sha256") and source.scheme == "https" and source.hostname
                and not source.username and not source.password and not source.fragment
                and isinstance(name, str) and SAFE_NAME.fullmatch(name)
                and isinstance(version, str) and re.fullmatch(r"[0-9A-Za-z.+:~_-]{1,100}", version)
                and arch in {os_info.get("architecture"), "all"}):
            issues.append("Package requires a unique staged .deb, matching trusted-source SHA256, metadata and architecture")
            continue
        if re.match(r"(?:nvidia|cuda|linux-|libpam|openssh|ufw|iptables|firewalld|wireguard)", name):
            issues.append("Driver, kernel, PAM, SSH and firewall packages are outside this deployment scope")
            continue
        seen_paths.add(path)
        is_primary = ((component == "apptainer" and name == "apptainer")
                      or (component == "slurm" and name in {"slurm-smd", "slurm-smd-client", "slurm-smd-slurmd", "slurm-smd-slurmctld"}))
        wanted = "1.5.5" if component == "apptainer" else "26.05.5"
        if is_primary and not re.fullmatch(re.escape(wanted) + r"(?:[-+~][0-9A-Za-z.+~_-]+)?", version):
            issues.append("Package version does not match the fixed component baseline")
            continue
        if component == "apptainer" and (name == "apptainer-suid" or (is_primary and package.get("non_setuid") is not True)):
            issues.append("Apptainer pilot requires observed non-setuid package contents")
            continue
        primary = primary or is_primary
        script = _substitute((ROOT / "infra/deploy/install-local-package.sh.in").read_text(),
                             {"PACKAGE_PATH": path, "PACKAGE_SHA256": sha})
        package_steps.append({"title": f"Install verified {name} {version}", "script": script,
                              "artifact": package, "preconditions": [{"type": "file_sha256", "path": path, "sha256": sha}]})
    if component in {"slurm", "apptainer"} and not primary:
        issues.append("No verified primary package matches the fixed component version")
    file_steps = []
    before_files, staged_files = evidence.get("files", {}), evidence.get("staged_files", {})
    if not isinstance(before_files, dict) or not isinstance(staged_files, dict):
        issues.append("File evidence and staging evidence must be mappings")
        before_files, staged_files = {}, {}
    for template, after in rendered.items():
        destination = COMPONENTS[component]["destinations"][template]
        before, staged = before_files.get(destination, {}), staged_files.get(destination, {})
        if not isinstance(before, dict) or not isinstance(staged, dict):
            issues.append("Malformed file evidence: " + destination)
            continue
        old_content = before.get("content")
        if before.get("exists") is False and old_content in (None, ""):
            old_content, old_sha = "", "absent"
        elif (before.get("exists") is True and isinstance(old_content, str) and len(old_content.encode()) <= 65536
              and _sha(before.get("sha256")) and _digest(old_content) == before["sha256"]):
            old_sha = before["sha256"]
        else:
            issues.append("Full before content or verified absence is missing: " + destination)
            continue
        after_sha = _digest(after)
        diff = "".join(difflib.unified_diff(old_content.splitlines(keepends=True), after.splitlines(keepends=True),
                                            fromfile=destination + " (before)", tofile=destination + " (after)"))
        change = {"destination": destination, "template_path": template,
                  "template_sha256": sha256_file(ROOT / template), "before": old_content if old_sha != "absent" else None,
                  "before_sha256": old_sha, "after": after, "after_sha256": after_sha,
                  "diff": diff, "diff_verified": True, "evidence_execution_id": execution_id}
        change["backup_path"] = None if old_sha == "absent" else destination + ".srs-backup-" + old_sha[:16]
        change["backup_policy"] = "not_needed_absent_destination" if old_sha == "absent" else "create_exclusive_no_overwrite"
        change["temporary_file_pattern"] = str(Path(destination).parent / ".srs-stage-*")
        change["publish_policy"] = "atomic_create_if_absent" if old_sha == "absent" else "atomic_replace_after_backup"
        review["file_changes"].append(change)
        if not _staged_path(staged.get("path")) or staged.get("sha256") != after_sha:
            issues.append("Staged rendered bytes do not match the exact reviewed after content: " + destination)
            continue
        script = _substitute((ROOT / "infra/deploy/install-platform-file.sh.in").read_text(), {
            "STAGED_FILE": staged["path"], "DESTINATION": destination,
            "FILE_SHA256": after_sha, "OLD_FILE_SHA256": old_sha,
            "BACKUP_PATH": change["backup_path"] or "",
        })
        file_steps.append({"title": "Install reviewed " + destination, "script": script, "file_change": change,
                           "preconditions": [{"type": "file_sha256", "path": staged["path"], "sha256": after_sha}]})
    for index, step in enumerate([*package_steps, *file_steps], 1):
        step.update(id=f"deploy-{node.get('id')}-{index}", host_id=node.get("id"), kind="ssh", operation="deploy",
                    argv=["/bin/sh", "-c", step["script"]], cwd="/", environment={}, requires_sudo=True,
                    execution_user="root", timeout_seconds=120, read_only=False, unresolved_template=False,
                    on_failure="stop_and_report_no_blind_retry", reason=review["impact"],
                    evidence=[{"execution_id": execution_id, "component": component}])
        review["steps"].append(step)
    if not issues and review["steps"]:
        review["status"] = "ready"
    elif not review["steps"]:
        issues.append("No fully rendered deployment steps are available")
    # Consumers must reject a blocked review even if partial review-only steps are present.
    return review
