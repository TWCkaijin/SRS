"""Reviewable read-only deployment evidence collection; no downloads or installs."""

import hashlib
import json
import re
from datetime import datetime, timedelta

from infra.catalog import COMPONENTS, SAFE_NAME, _sha, _staged_path


def validate_deployment_spec(component: str, node: dict, spec: dict) -> dict:
    if component not in COMPONENTS or not isinstance(spec, dict):
        raise ValueError("Unknown component or invalid deployment probe specification")
    if not isinstance(node.get("ssh_username"), str) or not re.fullmatch(r"[a-z_][a-z0-9_-]{0,31}", node["ssh_username"]):
        raise ValueError("Deployment probe requires the exact inventory SSH username")
    if not isinstance(node.get("id"), str) or not SAFE_NAME.fullmatch(node["id"]):
        raise ValueError("Invalid host identity")
    packages = spec.get("packages", [])
    if not isinstance(packages, list) or len(packages) > 32:
        raise ValueError("Probe at most 32 explicitly staged packages")
    cleaned_packages = []
    seen = set()
    for package in packages:
        if not (isinstance(package, dict) and _staged_path(package.get("path"), ".deb")
                and package["path"] not in seen and _sha(package.get("source_sha256"))
                and isinstance(package.get("source_url"), str) and package["source_url"].startswith("https://")
                and len(package["source_url"]) <= 2048):
            raise ValueError("Each package needs a unique staged .deb and reviewed public source/hash")
        seen.add(package["path"])
        cleaned_packages.append({key: package[key] for key in ("path", "source_sha256", "source_url")})
    staged = spec.get("staged_files", {})
    if not isinstance(staged, dict) or any(destination not in COMPONENTS[component]["destinations"].values()
                                         or not _staged_path(path) for destination, path in staged.items()):
        raise ValueError("Only fixed component destinations and staging paths may be inspected")
    window = spec.get("maintenance_window", {})
    if not isinstance(window, dict):
        raise ValueError("Invalid maintenance window")
    try:
        start = datetime.fromisoformat(window["starts_at"].replace("Z", "+00:00"))
        end = datetime.fromisoformat(window["expires_at"].replace("Z", "+00:00"))
        if start.tzinfo is None or end.tzinfo is None or not timedelta(0) < end - start <= timedelta(hours=4):
            raise ValueError
    except (KeyError, ValueError, TypeError, AttributeError) as error:
        raise ValueError("An explicit timezone-aware maintenance window of at most four hours is required") from error
    if spec.get("use_cuda", False) is not False:
        raise ValueError("This automatic evidence path supports CPU-only installation; GPU compatibility needs separate proof")
    return {"schema_version": 1, "component": component, "packages": cleaned_packages,
            "staged_files": dict(staged), "maintenance_window": {"starts_at": start.isoformat(), "expires_at": end.isoformat()},
            "use_cuda": False, "configuration": spec.get("configuration", {})}


def deployment_probe_script(component: str, node: dict, spec: dict) -> str:
    cleaned = validate_deployment_spec(component, node, spec)
    request = {"spec": cleaned, "host": {key: node.get(key) for key in ("id", "inventory_revision", "host_key_sha256")},
               "username": node["ssh_username"], "destinations": list(COMPONENTS[component]["destinations"].values())}
    return "INPUT = " + repr(json.dumps(request, sort_keys=True)) + "\n" + _SCRIPT


_SCRIPT = r'''import hashlib, json, os, pathlib, stat, subprocess
request = json.loads(INPUT)
spec = request["spec"]
def command(args, timeout=10):
    try:
        r = subprocess.run(args, capture_output=True, text=True, timeout=timeout,
                           env={"PATH": "/usr/sbin:/usr/bin:/sbin:/bin", "LC_ALL": "C"})
        if len(r.stdout) > 2000000 or len(r.stderr) > 65536:
            return {"error": "oversized_command_output"}
        return {"exit_code": r.returncode, "stdout": r.stdout, "stderr": r.stderr}
    except (OSError, subprocess.TimeoutExpired) as error:
        return {"error": type(error).__name__}
def inspect(path, content=False):
    p = pathlib.Path(path)
    if any(part.is_symlink() for part in [p, *p.parents]):
        return {"error": "symlink"}
    if path.startswith("/srv/srs/staging/"):
        try:
            for part in [p, *p.parents]:
                metadata = part.lstat()
                if metadata.st_uid != 0 or metadata.st_mode & 0o022:
                    return {"error": "staging_is_not_root_owned_and_protected"}
        except OSError as error:
            return {"error": type(error).__name__}
    if not p.exists():
        return {"exists": False}
    try:
        fd = os.open(p, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        try:
            metadata = os.fstat(fd)
            if not stat.S_ISREG(metadata.st_mode) or (content and metadata.st_size > 65536):
                return {"error": "not_a_bounded_regular_file"}
            with os.fdopen(fd, "rb", closefd=False) as stream:
                if content:
                    raw = stream.read(65537)
                    if len(raw) > 65536:
                        return {"error": "oversized_file"}
                    return {"exists": True, "sha256": hashlib.sha256(raw).hexdigest(), "content": raw.decode("utf-8")}
                return {"exists": True, "sha256": hashlib.file_digest(stream, "sha256").hexdigest()}
        finally:
            os.close(fd)
    except (OSError, UnicodeDecodeError) as error:
        return {"error": type(error).__name__}
os_info = {}
for line in pathlib.Path("/etc/os-release").read_text().splitlines():
    key, _, value = line.partition("=")
    if key in ("ID", "VERSION_ID"):
        os_info[key.lower()] = value.strip('"')
arch = command(["dpkg", "--print-architecture"])
os_info["architecture"] = arch.get("stdout", "").strip() if arch.get("exit_code") == 0 else None
result = {"kind": "deployment_probe", "schema_version": 1, "component": spec["component"], "host": request["host"],
          "spec_sha256": hashlib.sha256(json.dumps(spec, sort_keys=True, separators=(",", ":")).encode()).hexdigest(),
          "os": os_info, "uid": os.getuid(), "packages": [], "files": {}, "staged_files": {},
          "workloads": command(["squeue", "--noheader", "--nodes", "srs-" + request["host"]["id"], "--format", "%A"]),
          "userns": command(["runuser", "--user", request["username"], "--", "unshare", "--user", "--map-root-user", "/usr/bin/true"]),
          "fuse": command(["runuser", "--user", request["username"], "--", "/usr/bin/python3", "-B", "-c",
                           "import os; raise SystemExit(0 if os.access('/dev/fuse', os.R_OK|os.W_OK) else 1)"])}
for item in spec["packages"]:
    p = dict(item)
    p.update(inspect(item["path"]))
    if p.get("exists"):
        p["metadata"] = command(["dpkg-deb", "--field", item["path"], "Package", "Version", "Architecture"])
        p["contents"] = command(["dpkg-deb", "--contents", item["path"]])
    result["packages"].append(p)
if spec["packages"]:
    result["dependency_simulation"] = command(["apt-get", "--simulate", "--no-download", "--no-install-recommends", "install",
                                                *[p["path"] for p in spec["packages"]]], timeout=30)
for destination in request["destinations"]:
    result["files"][destination] = inspect(destination, content=True)
for destination, path in spec["staged_files"].items():
    result["staged_files"][destination] = dict(inspect(path), path=path)
print(json.dumps(result, sort_keys=True))
'''


def parse_deployment_evidence(observation: dict, component: str, node: dict, spec: dict, *, execution_id: str,
                              observed_at: str) -> dict:
    """Bind authentic successful probe output; unknown prerequisites remain absent.

    The caller verifies the source execution/step ownership/hash/mode. An input
    JSON document alone does not authenticate any observation or requirement.
    """
    cleaned = validate_deployment_spec(component, node, spec)
    spec_sha = hashlib.sha256(json.dumps(cleaned, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    host = {key: node.get(key) for key in ("id", "inventory_revision", "host_key_sha256")}
    if not (isinstance(observation, dict) and observation.get("kind") == "deployment_probe"
            and observation.get("component") == component and observation.get("spec_sha256") == spec_sha
            and observation.get("host") == host):
        raise ValueError("Deployment output is not bound to the exact approved host/specification")
    if not isinstance(execution_id, str) or not SAFE_NAME.fullmatch(execution_id):
        raise ValueError("A successful source execution id is required")
    timestamp = datetime.fromisoformat(observed_at.replace("Z", "+00:00"))
    if timestamp.tzinfo is None:
        raise ValueError("An aware observation timestamp is required")
    result = {"schema_version": 1, "component": component, "execution_id": execution_id,
              "observed_at": observed_at, "host": host, "os": observation.get("os", {}), "requirements": {},
              "packages": [], "files": observation.get("files", {}), "staged_files": observation.get("staged_files", {}),
              "configuration": cleaned["configuration"], "raw_observation": observation, "spec_sha256": spec_sha}
    def verified(name, detail):
        result["requirements"][name] = {"status": "verified", "execution_id": execution_id, "detail": detail}
    verified("target_identity_and_pinned_host_key", "Source approved execution binds this pinned inventory host")
    verified("immutable_inventory_revision", "Exact source execution inventory revision: " + str(host["inventory_revision"]))
    os_info = result["os"]
    if (observation.get("uid") == 0 and isinstance(os_info, dict) and os_info.get("id") == "ubuntu"
            and os_info.get("version_id") == "24.04" and os_info.get("architecture") in {"amd64", "arm64"}):
        verified("fresh_os_architecture_and_privilege_evidence", "Observed Ubuntu 24.04 architecture and uid=0 in approved sudo probe")
    observed_packages = observation.get("packages", [])
    requested_paths = {p["path"] for p in cleaned["packages"]}
    if not isinstance(observed_packages, list) or {p.get("path") for p in observed_packages if isinstance(p, dict)} != requested_paths:
        raise ValueError("Observed package set differs from approved staged package set")
    for package in observed_packages:
        metadata = package.get("metadata", {})
        fields = {}
        if metadata.get("exit_code") == 0:
            for line in metadata.get("stdout", "").splitlines():
                key, sep, value = line.partition(":")
                if sep:
                    fields[key] = value.strip()
        contents = package.get("contents", {})
        listing = contents.get("stdout", "")
        non_setuid = (contents.get("exit_code") == 0 and bool(listing)
                      and all(not any(char in line.split()[0] for char in "sS") for line in listing.splitlines() if line.split()))
        requested = next(p for p in cleaned["packages"] if p["path"] == package["path"])
        result["packages"].append({**requested, "sha256": package.get("sha256"), "name": fields.get("Package"),
                                   "version": fields.get("Version"), "architecture": fields.get("Architecture"),
                                   "non_setuid": non_setuid})
    if result["packages"] and all(p["sha256"] == p["source_sha256"] for p in result["packages"]):
        verified("verified_staged_artifact_sha256_and_provenance", "Observed bytes match each source SHA256/reference explicitly reviewed in source plan")
    workloads = observation.get("workloads", {})
    start = datetime.fromisoformat(cleaned["maintenance_window"]["starts_at"])
    end = datetime.fromisoformat(cleaned["maintenance_window"]["expires_at"])
    if workloads.get("exit_code") == 0 and not workloads.get("stdout", "").strip() and start <= timestamp <= end:
        verified("maintenance_window_and_current_workload_check", "No Slurm jobs on this node during the explicitly approved maintenance window; unmanaged processes are not inspected")
    if component == "apptainer":
        verified("reviewed_before_and_after_file_diff", "No directly managed configuration destinations in this package-only plan")
        verified("backup_or_absence_evidence_for_every_destination", "No directly managed configuration destinations; package maintainer effects remain explicit review impact")
        primary = [p for p in result["packages"] if p.get("name") == "apptainer" and p.get("version") == "1.5.5"]
        simulation = observation.get("dependency_simulation", {})
        output = simulation.get("stdout", "")
        installed = {match.group(1) for match in re.finditer(r"^Inst ([A-Za-z0-9_.+-]+)(?::[A-Za-z0-9_-]+)? ", output, re.M)}
        names = {p["name"] for p in result["packages"]}
        requested_versions = {p["name"]: p["version"] for p in result["packages"]}
        simulated_versions = {match.group(1): match.group(2) for match in re.finditer(
            r"^Inst ([A-Za-z0-9_.+-]+)(?::[A-Za-z0-9_-]+)? (?:\[[^\]]+\] )?\(([^\s]+)", output, re.M)}
        versions_match = set(simulated_versions) == installed and all(
            version == requested_versions.get(name) for name, version in simulated_versions.items())
        if (primary and simulation.get("exit_code") == 0 and installed <= names and versions_match
                and not re.search(r"^Remv ", output, re.M)):
            verified("verified_apptainer_1_5_5_deb_and_dependencies", "Observed exact package version; offline apt simulation proposes no package outside the reviewed staged set and no removals")
        if primary and all(p.get("non_setuid") for p in result["packages"]):
            verified("non_setuid_package_selection", "dpkg-deb contents contain no setuid/setgid file modes")
        if observation.get("userns", {}).get("exit_code") == 0 and observation.get("fuse", {}).get("exit_code") == 0:
            verified("unprivileged_user_namespaces_and_FUSE", "Exact inventory SSH user successfully created transient user namespace and can read/write /dev/fuse")
        verified("GPU_driver_compatibility_if_CUDA", "Not applicable: this is CPU-only installation evidence; CUDA readiness is not asserted")
    return result
