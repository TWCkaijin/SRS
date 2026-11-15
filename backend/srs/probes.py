"""Reviewed read-only probes. No environment dumps or process command lines."""
import json

HOST_PROBE = '''import json, os, pathlib, pwd, subprocess, time
def command(args):
    try:
        r = subprocess.run(args, capture_output=True, text=True, timeout=5)
        return {"exit_code": r.returncode, "stdout": r.stdout, "stderr": r.stderr}
    except (OSError, subprocess.TimeoutExpired) as e:
        return {"error": type(e).__name__}
memory = {}
for line in pathlib.Path("/proc/meminfo").read_text().splitlines():
    key, value = line.split(":", 1)
    if key in ("MemTotal", "MemAvailable"):
        memory[key] = int(value.strip().split()[0]) // 1024
gpu = command(["nvidia-smi", "--query-gpu=index,name,uuid,memory.total,memory.used,utilization.gpu", "--format=csv,noheader,nounits"])
gpus = []
for line in gpu.get("stdout", "").splitlines():
    fields = [v.strip() for v in line.split(",")]
    if len(fields) == 6:
        gpus.append({"index": int(fields[0]), "name": fields[1], "uuid": fields[2], "memory_total_mb": float(fields[3]), "memory_used_mb": float(fields[4]), "utilization_percent": float(fields[5])})
cpu1 = list(map(int, pathlib.Path("/proc/stat").read_text().splitlines()[0].split()[1:9]))
time.sleep(0.1)
cpu2 = list(map(int, pathlib.Path("/proc/stat").read_text().splitlines()[0].split()[1:9]))
total = sum(cpu2) - sum(cpu1)
idle = (cpu2[3] + cpu2[4]) - (cpu1[3] + cpu1[4])
print(json.dumps({"kind": "host_snapshot", "username": pwd.getpwuid(os.getuid()).pw_name, "uid": os.getuid(), "status": "online", "source": "remote", "cpu_percent": round(100 * (total-idle) / total, 1) if total else 0, "memory_total_mb": memory.get("MemTotal", 0), "memory_used_mb": memory.get("MemTotal", 0)-memory.get("MemAvailable", 0), "gpus": gpus, "gpu_probe": gpu, "cgroup": command(["stat", "-fc", "%T", "/sys/fs/cgroup"]), "clock": command(["timedatectl", "show", "-p", "NTPSynchronized"]), "slurm": command(["sinfo", "--json"])}))
'''


def preflight_script(projects: list[dict], inspect_config: bool = True) -> str:
    # JSON data is encoded as a Python string literal, never interpolated as source.
    return HOST_PROBE + f'''import hashlib
projects = json.loads({json.dumps(json.dumps(projects))})
facts = {{"kind": "preflight_evidence", "projects": {{}}, "files": {{}}}}
for p in projects:
    root = pathlib.Path(p["path"])
    item = {{"revision": command(["git", "-C", str(root), "rev-parse", "HEAD"]), "entrypoint": p["entrypoint"]}}
    image = pathlib.Path(p["container"])
    if image.is_file() and not image.is_symlink():
        with image.open("rb") as stream:
            item["container_sha256"] = hashlib.file_digest(stream, "sha256").hexdigest()
    facts["projects"][p["id"]] = item
config = pathlib.Path("/etc/srs/approved-config.json")
if {inspect_config!r}:
    if config.is_symlink():
        facts["files"][str(config)] = {{"error": "symlink"}}
    elif config.exists():
        raw = config.read_bytes()
        if len(raw) <= 65536:
            facts["files"][str(config)] = {{"exists": True, "sha256": hashlib.sha256(raw).hexdigest(), "content": raw.decode("utf-8")}}
    else:
        facts["files"][str(config)] = {{"exists": False}}
print(json.dumps(facts))
'''
