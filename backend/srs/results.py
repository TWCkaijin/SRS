"""Fixed, small result collection and reversible cleanup templates."""
import re

from fastapi import HTTPException

from srs.models import Job

RESULT_FILES = ("rank-0.json", "rank-1.json")
SAFE_OPEN = '''import os, pathlib, stat, hashlib, json
def open_output(job_id):
    fd = os.open("/", os.O_RDONLY | os.O_DIRECTORY)
    try:
        for part in ("srv", "srs", "results", job_id, "output"):
            next_fd = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            os.close(fd)
            fd = next_fd
        return fd
    except BaseException:
        os.close(fd)
        raise
'''


def selected_job(db, draft, settings):
    job = db.get(Job, draft.parameters.get("job_id", ""))
    if job is None or job.owner != draft.owner:
        raise HTTPException(404, "請選擇本帳號已登錄的工作")
    if settings.mode != "demo" and not re.fullmatch(r"[0-9]+", job.slurm_job_id):
        raise HTTPException(422, "需要有效的 Slurm job ID")
    if draft.host_ids != [settings.slurm_controller_id]:
        raise HTTPException(422, f"成果收集與清理僅在控制節點 {settings.slurm_controller_id} 的共享成果路徑進行")
    return job


def collection_script(job):
    return SAFE_OPEN + f'''directory = open_output({job.slurm_job_id!r})
try:
    for filename in {RESULT_FILES!r}:
        try:
            fd = os.open(filename, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=directory)
        except FileNotFoundError:
            continue
        with os.fdopen(fd, "rb") as stream:
            info = os.fstat(stream.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_size > 65536:
                raise RuntimeError("Only regular result JSON files of at most 64KiB may be collected")
            raw = stream.read(65537)
        if len(raw) > 65536: raise RuntimeError("Result grew beyond limit")
        text = raw.decode("utf-8")
        if not isinstance(json.loads(text), dict): raise RuntimeError("Result must be a JSON object")
        print(json.dumps({{"kind":"artifact", "job_id":{job.id!r}, "filename":filename,
                          "source_sha256":hashlib.sha256(raw).hexdigest(), "text":text}}))
finally:
    os.close(directory)
'''


def build_result_steps(db, draft, node, settings, base_step, add_manifest):
    job = selected_job(db, draft, settings)
    step = base_step(1, node, "收集固定訓練結果 JSON", "collect_results", read_only=True)
    step.update(argv=["/usr/bin/python3", "-B", "-c", collection_script(job)],
                reason="僅讀取此已登錄工作的 rank-0.json 與 rank-1.json，每檔最多 64KiB；不讀取任意路徑或 checkpoint。",
                evidence=[{"job_id": job.id, "slurm_job_id": job.slurm_job_id, "owner": job.owner}],
                artifact_scope={"job_id": job.id, "filenames": list(RESULT_FILES), "max_bytes": 65536})
    add_manifest(step, node, settings)
    return [step], []


def build_cleanup_steps(db, draft, node, settings, base_step, add_manifest):
    job = selected_job(db, draft, settings)
    names = draft.parameters.get("filenames", ["rank-0.json"])
    if not isinstance(names, list) or not names or len(names) != len(set(names)) or any(n not in RESULT_FILES for n in names):
        raise HTTPException(422, "第一版僅允許清理已收集的 rank-0.json／rank-1.json")
    if job.status not in {"COMPLETED", "CANCELLED", "FAILED", "TIMEOUT"}:
        return [], ["清理前必須透過已核准的工作狀態排程確認工作已結束"]
    steps, issues = [], []
    for filename in names:
        evidence = node.facts.get("results", {}).get(job.id, {}).get(filename)
        if not evidence or not re.fullmatch(r"[a-f0-9]{64}", evidence.get("source_sha256", "")):
            issues.append(f"缺少 {filename} 的已核准收集證據，先建立收集成果計畫")
            continue
        source_hash = evidence["source_sha256"]
        backup = ".srs-cleanup-" + source_hash[:16] + "-" + filename
        script = SAFE_OPEN + f'''directory = open_output({job.slurm_job_id!r})
try:
    fd = os.open({filename!r}, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=directory)
    with os.fdopen(fd, "rb") as stream:
        original = os.fstat(stream.fileno())
        if not stat.S_ISREG(original.st_mode) or original.st_size > 65536: raise RuntimeError("Invalid result file")
        raw = stream.read(65537)
    if hashlib.sha256(raw).hexdigest() != {source_hash!r}: raise RuntimeError("Result changed after approval")
    backup_fd = os.open({backup!r}, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=directory)
    with os.fdopen(backup_fd, "wb") as stream:
        stream.write(raw); stream.flush(); os.fsync(stream.fileno())
    current = os.stat({filename!r}, dir_fd=directory, follow_symlinks=False)
    if (original.st_dev, original.st_ino, original.st_size, original.st_mtime_ns, original.st_ctime_ns) != (current.st_dev, current.st_ino, current.st_size, current.st_mtime_ns, current.st_ctime_ns):
        raise RuntimeError("Result changed during backup; source retained")
    os.unlink({filename!r}, dir_fd=directory)
    os.fsync(directory)
    print("Selected result archived; backup retained")
finally:
    os.close(directory)
'''
        path = f"/srv/srs/results/{job.slurm_job_id}/output/{filename}"
        step = base_step(len(steps) + 1, node, "備份後移除已收集的結果檔", "cleanup")
        step.update(argv=["/usr/bin/python3", "-B", "-c", script], script=script,
            file_change={"path": path, "before_sha256": source_hash, "after_sha256": None,
                         "before": evidence.get("text", ""), "after": "", "backup_path": path.rsplit("/", 1)[0] + "/" + backup,
                         "diff": "Remove only this verified result after exclusive backup; no recursive deletion"},
            reason="只清理已結束工作、已收集且摘要一致的單一結果檔；保留不可覆寫的備份，不刪除日誌或 checkpoint。",
            evidence=[{"job_id": job.id, "source_sha256": source_hash, "observed_at": evidence.get("observed_at")}])
        add_manifest(step, node, settings)
        steps.append(step)
    return steps, issues
