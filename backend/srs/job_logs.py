"""Approved, byte-cursor-based job-log probe and pre-persistence sanitization."""

import base64
import binascii
import inspect
import re
from pathlib import PurePosixPath

LOG_ROOT = "/srv/srs/results"
MAX_PAGE_BYTES = 65536
LOOKAROUND_BYTES = 4096
JOB_ID_PATTERN = r"[A-Za-z0-9][A-Za-z0-9_-]{0,100}"
OFFSET_PATTERN = re.compile(r"\$\{job_log_offset:([A-Za-z0-9][A-Za-z0-9_-]{0,100}):(stdout|stderr)\}")


def _probe(job_id, slurm_job_id, offsets, page_bytes, root, lookaround):
    import base64
    import codecs
    import json
    import os
    import pathlib
    import stat

    for stream in ("stdout", "stderr"):
        requested = offsets[stream]
        if type(requested) is not int or not 0 <= requested <= 9223372036854775807:
            raise ValueError("Invalid approved byte offset")
        descriptors = []
        try:
            parent = os.open("/", os.O_RDONLY | os.O_DIRECTORY)
            descriptors.append(parent)
            for component in pathlib.PurePosixPath(root).parts[1:]:
                parent = os.open(component, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent)
                descriptors.append(parent)
            fd = os.open(slurm_job_id + "." + stream,
                         os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent)
            descriptors.append(fd)
            info = os.fstat(fd)
            if not stat.S_ISREG(info.st_mode):
                raise RuntimeError("Job log must be a regular file")
            size = info.st_size
            start = requested if requested <= size else 0
            gap = None if requested <= size else "truncated"
            os.lseek(fd, start, os.SEEK_SET)
            page = os.read(fd, min(page_bytes, size - start))
            # A valid UTF-8 sequence spanning pages belongs wholly to the next page.
            decoder = codecs.getincrementaldecoder("utf-8")("surrogateescape")
            decoder.decode(page, final=start + len(page) >= size)
            pending = decoder.getstate()[0]
            if pending:
                page = page[:-len(pending)]
            end = start + len(page)
            window_start = max(0, start - lookaround)
            window_end = min(size, end + lookaround)
            os.lseek(fd, window_start, os.SEEK_SET)
            window = bytearray()
            while len(window) < window_end - window_start:
                part = os.read(fd, window_end - window_start - len(window))
                if not part:
                    raise RuntimeError("Job log changed during read; cursor not advanced")
                window.extend(part)
            current = os.fstat(fd)
            if current.st_size < size:
                raise RuntimeError("Job log truncated during read; cursor not advanced")
            record = {
                "kind": "job_log", "job_id": job_id, "stream": stream, "exists": True,
                "requested_start": requested, "start": start, "end": end,
                "inode": info.st_ino, "device": info.st_dev, "size": size,
                "gap": gap, "has_more": end < size,
                "window_start": window_start, "window_end": window_end,
                "data_b64": base64.b64encode(window).decode("ascii"),
            }
        except FileNotFoundError:
            record = {
                "kind": "job_log", "job_id": job_id, "stream": stream, "exists": False,
                "requested_start": requested, "start": requested, "end": requested,
                "inode": None, "device": None, "size": 0,
                "gap": "missing" if requested else None, "has_more": False,
                "window_start": requested, "window_end": requested, "data_b64": "",
            }
        finally:
            for descriptor in reversed(descriptors):
                os.close(descriptor)
        print(json.dumps(record, ensure_ascii=True), flush=True)


def script_builder(job_id: str, slurm_job_id: str, page_bytes: int = MAX_PAGE_BYTES) -> str:
    """Build source only; no local or remote file is read during plan construction."""
    if not isinstance(job_id, str) or not re.fullmatch(JOB_ID_PATTERN, job_id):
        raise ValueError("Invalid registered job identifier")
    if not isinstance(slurm_job_id, str) or not re.fullmatch(r"(?:demo-)?[0-9]{1,20}", slurm_job_id):
        raise ValueError("Invalid Slurm job identifier")
    if type(page_bytes) is not int or not 4 <= page_bytes <= MAX_PAGE_BYTES:
        raise ValueError("Job-log page size must be between 4 and 65536 bytes")
    root = PurePosixPath(LOG_ROOT)
    if not root.is_absolute() or ".." in root.parts:
        raise ValueError("Job-log root must be an absolute fixed path")
    offset_values = ", ".join(
        repr(stream) + ": int(" + repr("${job_log_offset:" + job_id + ":" + stream + "}") + ")"
        for stream in ("stdout", "stderr")
    )
    return (inspect.getsource(_probe) + "\n_probe(" + repr(job_id) + ", " + repr(slurm_job_id)
            + ", {" + offset_values + "}, " + str(page_bytes) + ", " + repr(str(root))
            + ", " + str(LOOKAROUND_BYTES) + ")\n")


def validate_secret_window(secrets):
    if any(len(value.encode("utf-8")) > LOOKAROUND_BYTES for value in secrets if value):
        raise ValueError("Job-log redaction cannot safely process a secret larger than its approved lookaround")


def sanitize_record(record: dict, scope: dict, offsets: dict, secrets) -> dict:
    """Retain source byte cursors while masking decoded content before persistence.

    Raw lookaround bytes are transient transport data. Only the masked page and
    its display text leave this function; no caller should persist the input.
    """
    validate_secret_window(secrets)
    if not isinstance(record, dict) or record.get("kind") != "job_log":
        raise ValueError("Unexpected job-log probe record")
    expected_fields = {"kind", "job_id", "stream", "exists", "requested_start", "start", "end",
                       "inode", "device", "size", "gap", "has_more", "window_start", "window_end", "data_b64"}
    if set(record) != expected_fields:
        raise ValueError("Job-log probe record has unapproved fields")
    job_id, stream = record.get("job_id"), record.get("stream")
    if job_id != scope["job_id"] or stream not in scope["streams"]:
        raise ValueError("Job-log record is outside the approved scope")
    requested = offsets[job_id][stream]
    if record.get("requested_start") != requested:
        raise ValueError("Job-log record does not match the persisted byte offset")
    for key in ("requested_start", "start", "end", "size", "window_start", "window_end"):
        if type(record.get(key)) is not int or record[key] < 0:
            raise ValueError("Invalid job-log byte metadata")
    if type(record.get("exists")) is not bool or type(record.get("has_more")) is not bool:
        raise ValueError("Invalid job-log status metadata")
    if record["gap"] not in (None, "missing", "truncated"):
        raise ValueError("Invalid job-log gap marker")
    try:
        raw = base64.b64decode(record["data_b64"], validate=True)
    except (KeyError, TypeError, ValueError, binascii.Error) as exc:
        raise ValueError("Invalid encoded job-log bytes") from exc
    start, end, size = record["start"], record["end"], record["size"]
    low, high = record["window_start"], record["window_end"]
    if not record["exists"]:
        if raw or start != requested or end != requested or low != requested or high != requested or size:
            raise ValueError("Invalid missing job-log record")
        if record.get("inode") is not None or record.get("device") is not None or record["has_more"]:
            raise ValueError("Invalid missing job-log identity")
        if record["gap"] != ("missing" if requested else None):
            raise ValueError("Invalid missing job-log gap marker")
    else:
        if not (0 <= low <= start <= end <= high <= size and end - start <= MAX_PAGE_BYTES):
            raise ValueError("Job-log byte range is outside the approved page")
        if low != max(0, start - LOOKAROUND_BYTES) or high != min(size, end + LOOKAROUND_BYTES):
            raise ValueError("Job-log redaction lookaround is incomplete")
        if len(raw) != high - low or record["has_more"] != (end < size):
            raise ValueError("Job-log byte length or continuation marker is inconsistent")
        if start != (requested if requested <= size else 0):
            raise ValueError("Job-log start does not match the approved cursor")
        if record["gap"] != ("truncated" if requested > size else None):
            raise ValueError("Invalid job-log truncation marker")
        for key in ("inode", "device"):
            if type(record.get(key)) is not int or record[key] < 0:
                raise ValueError("Invalid job-log identity")
    intervals = []
    for value in sorted({secret.encode("utf-8") for secret in secrets if secret}, key=len, reverse=True):
        position = 0
        while True:
            found = raw.find(value, position)
            if found < 0:
                break
            intervals.append((found, found + len(value)))
            position = found + 1
        # A writer may have emitted only a prefix of a known secret at this EOF.
        if high == size:
            for length in range(min(len(value) - 1, len(raw)), 0, -1):
                if raw.endswith(value[:length]):
                    intervals.append((len(raw) - length, len(raw)))
                    break
    merged = []
    for left, right in sorted(intervals):
        if merged and left <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], right))
        else:
            merged.append((left, right))
    page_start, page_end = start - low, end - low
    position, parts, redacted = page_start, [], False
    for left, right in merged:
        left, right = max(left, page_start), min(right, page_end)
        if left < right:
            parts.extend((raw[position:left], b"[REDACTED]"))
            position, redacted = right, True
    parts.append(raw[position:page_end])
    safe = b"".join(parts)
    result = {key: value for key, value in record.items() if key not in ("data_b64", "window_start", "window_end")}
    result.update(text=safe.decode("utf-8", errors="backslashreplace"),
                  data_b64=base64.b64encode(safe).decode("ascii"), redacted=redacted)
    return result
