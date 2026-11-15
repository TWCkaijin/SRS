"""Reviewable, standard-library-only SSH execution protocol.

The source below is embedded verbatim in each approved remote manifest. A
read-only invocation writes no remote journal. Mutating invocations reserve a
run directory before dispatch and attach to an existing run rather than replay.
"""

import hashlib
import inspect
import re

from srs.redaction import StreamingRedactor

PROTOCOL_VERSION = 1
SUDO_PROMPT = "[SRS_SUDO_PASSWORD]"
STRUCTURED_RECORD_KINDS = {
    "preflight": ["host_snapshot", "preflight_evidence"],
    "host_snapshot": ["host_snapshot"],
    "cluster_probe": ["cluster_probe"],
    "deployment_probe": ["deployment_probe"],
    "job_logs": ["job_log"],
    "collect_results": ["artifact"],
}


def _remote_main():
    import codecs
    import hashlib
    import json
    import os
    import pathlib
    import pwd
    import re
    import selectors
    import signal
    import stat
    import subprocess
    import sys
    import time

    def wire(item):
        print(json.dumps(item, ensure_ascii=True), flush=True)

    wire({"type": "ready", "protocol": 1})
    payload = json.loads(sys.stdin.readline())
    secrets = payload.get("redactions", [])
    structured_stdout = payload.get("structured_stdout")
    if structured_stdout not in (None, "job_log", "artifact", "probe_json") or (
        structured_stdout is not None and payload.get("read_only") is not True
    ):
        raise ValueError("Structured stdout requires a reviewed read-only probe")

    def masked(text):
        redactor = StreamingRedactor(secrets)
        return redactor.feed(str(text)) + redactor.flush()

    def reject(message):
        wire({"type": "result", "result": {"exit_code": 65, "signal": None,
              "timed_out": False, "unknown": False, "outputs": {}, "error": masked(message)}})
        raise SystemExit(65)

    execution_id = payload.get("execution_id", "")
    step_id = payload.get("step_id", "")
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,100}", execution_id):
        reject("Invalid execution identifier")
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}", step_id):
        reject("Invalid step identifier")
    if not re.fullmatch(r"[a-f0-9]{64}", payload.get("plan_hash", "")):
        reject("Invalid plan hash")
    if pwd.getpwuid(os.geteuid()).pw_name != payload.get("execution_user"):
        reject("Effective execution identity differs from approved identity")
    argv = payload.get("argv")
    if not isinstance(argv, list) or not argv or not all(isinstance(x, str) and "\0" not in x for x in argv):
        reject("Approved argv must be a nonempty string array")
    if not os.path.isabs(argv[0]):
        reject("Executable must use an absolute path")
    cwd = payload.get("cwd") or "/"
    if not os.path.isabs(cwd):
        reject("Working directory must be absolute")
    timeout = payload.get("timeout_seconds", 60)
    if not isinstance(timeout, (int, float)) or not 1 <= timeout <= 86400:
        reject("Invalid execution timeout")
    environment = payload.get("environment", {})
    if not isinstance(environment, dict) or not all(
        re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", k) and isinstance(v, str) and "\0" not in v
        for k, v in environment.items()
    ):
        reject("Invalid environment")

    def check_preconditions():
        for condition in payload.get("preconditions", []):
            kind = condition.get("type")
            if kind == "uid_equals":
                if os.geteuid() != condition.get("value"):
                    raise ValueError("UID precondition failed")
                continue
            if kind not in ("file_sha256", "file_absent", "command_exists"):
                raise ValueError("Unsupported precondition type")
            path = condition.get("path", "")
            if not os.path.isabs(path):
                raise ValueError("Precondition path must be absolute")
            if kind == "file_absent":
                if os.path.lexists(path):
                    raise ValueError("File absence precondition failed: " + path)
            elif kind == "command_exists":
                if not os.path.isfile(path) or not os.access(path, os.X_OK):
                    raise ValueError("Executable precondition failed: " + path)
            else:
                descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
                with os.fdopen(descriptor, "rb") as source:
                    if not stat.S_ISREG(os.fstat(source.fileno()).st_mode):
                        raise ValueError("Hash precondition requires a regular file")
                    digest = hashlib.file_digest(source, "sha256").hexdigest()
                if digest != condition.get("sha256"):
                    raise ValueError("File hash precondition failed: " + path)

    def execute(emit):
        try:
            check_preconditions()
        except Exception as exc:
            return {"exit_code": 65, "signal": None, "timed_out": False, "unknown": False,
                    "outputs": {}, "error": masked(str(exc)), "precondition_failed": True}
        env = {"PATH": "/usr/local/bin:/usr/bin:/bin", "LANG": "C.UTF-8",
               "HOME": pwd.getpwuid(os.geteuid()).pw_dir}
        env.update(environment)
        try:
            child = subprocess.Popen(argv, cwd=cwd, env=env, stdin=subprocess.DEVNULL,
                                     stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=True)
        except Exception as exc:
            return {"exit_code": 126, "signal": None, "timed_out": False, "unknown": False,
                    "outputs": {}, "error": masked(str(exc))}
        emit("audit", "Approved process started; pid=" + str(child.pid))
        selector = selectors.DefaultSelector()
        streams = {}
        for stream, pipe in (("stdout", child.stdout), ("stderr", child.stderr)):
            os.set_blocking(pipe.fileno(), False)
            selector.register(pipe, selectors.EVENT_READ, stream)
            stream_secrets = [] if stream == "stdout" and structured_stdout else secrets
            streams[stream] = (codecs.getincrementaldecoder("utf-8")("replace"), StreamingRedactor(stream_secrets))
        deadline = time.monotonic() + timeout
        terminated_at = None
        killed = False
        timed_out = False
        try:
            while selector.get_map() or child.poll() is None:
                now = time.monotonic()
                if terminated_at is None and now >= deadline:
                    timed_out = True
                    terminated_at = now
                    emit("audit", "Approved timeout reached; terminating this process group")
                    try:
                        os.killpg(child.pid, signal.SIGTERM)
                    except ProcessLookupError:
                        pass
                if terminated_at is not None and not killed and now - terminated_at >= 5:
                    killed = True
                    try:
                        os.killpg(child.pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                for key, _ in selector.select(0.1):
                    stream = key.data
                    decoder, redactor = streams[stream]
                    try:
                        chunk = os.read(key.fileobj.fileno(), 16384)
                    except BlockingIOError:
                        continue
                    if chunk:
                        safe = redactor.feed(decoder.decode(chunk))
                        if safe:
                            emit(stream, safe)
                    else:
                        safe = redactor.feed(decoder.decode(b"", final=True)) + redactor.flush()
                        if safe:
                            emit(stream, safe)
                        selector.unregister(key.fileobj)
                        key.fileobj.close()
            code = child.wait()
            return {"exit_code": code if code >= 0 else None,
                    "signal": -code if code < 0 else None, "timed_out": timed_out,
                    "unknown": False, "outputs": {}}
        except BaseException:
            # Losing the ability to persist output stops only this owned group.
            try:
                os.killpg(child.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            child.wait()
            raise
        finally:
            selector.close()

    sequence = 0

    def event(stream, text):
        nonlocal sequence
        sequence += 1
        safe_text = text if stream == "stdout" and structured_stdout else masked(text)
        return {"type": "event", "sequence": sequence, "stream": stream, "text": safe_text,
                "received_at": time.time()}

    if payload.get("read_only") is True:
        result = execute(lambda stream, text: wire(event(stream, text)))
        wire({"type": "result", "result": result})
        return

    root = pathlib.Path("/var/tmp") / ("srs-runner-" + str(os.geteuid()))
    # Tests can use an explicitly approved alternative state root.
    if payload.get("state_root"):
        root = pathlib.Path(payload["state_root"])
    if not root.is_absolute() or root.parent.is_symlink():
        reject("Unsafe journal root")

    def private_dir(path):
        try:
            path.mkdir(mode=0o700)
        except FileExistsError:
            pass
        info = path.lstat()
        if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) & 0o077:
            raise ValueError("Journal directory is not private and owned by execution user")

    def atomic_json(path, value):
        tmp = path.with_suffix(".tmp-" + str(os.getpid()))
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w") as target:
            json.dump(value, target)
            target.flush()
            os.fsync(target.fileno())
        os.replace(tmp, path)

    try:
        if payload.get("reconcile_only") and not (root / execution_id / step_id).is_dir():
            wire({"type": "result", "result": {"exit_code": None, "signal": None, "timed_out": False,
                  "unknown": True, "outputs": {}, "error": "No remote run record exists; no command was dispatched"}})
            return
        private_dir(root)
        execution_dir = root / execution_id
        private_dir(execution_dir)
        run_dir = execution_dir / step_id
        try:
            if payload.get("reconcile_only"):
                raise FileExistsError
            run_dir.mkdir(mode=0o700)
            new_run = True
        except FileExistsError:
            private_dir(run_dir)
            new_run = False
        state_path = run_dir / "state.json"
        events_path = run_dir / "events.jsonl"
        expected_hash = payload["plan_hash"]
        if new_run:
            atomic_json(state_path, {"status": "reserved", "plan_hash": expected_hash})
            fd = os.open(events_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            os.close(fd)
            supervisor_pid = os.fork()
            if supervisor_pid == 0:
                os.setsid()
                null = os.open(os.devnull, os.O_RDWR)
                for descriptor in (0, 1, 2):
                    os.dup2(null, descriptor)
                if null > 2:
                    os.close(null)
                try:
                    atomic_json(state_path, {"status": "running", "plan_hash": expected_hash,
                                            "supervisor_pid": os.getpid()})
                    with events_path.open("a", buffering=1) as journal:
                        def persist(stream, text):
                            journal.write(json.dumps(event(stream, text), ensure_ascii=True) + "\n")
                            journal.flush()
                            os.fsync(journal.fileno())
                        result = execute(persist)
                    atomic_json(state_path, {"status": "finished", "plan_hash": expected_hash,
                                            "result": result})
                except BaseException:
                    # Do not persist exception strings which may embed unknown credentials.
                    try:
                        atomic_json(state_path, {"status": "unknown", "plan_hash": expected_hash,
                                                "error": "Supervisor failed; reconciliation required"})
                    except BaseException:
                        pass
                os._exit(0)
        deadline = time.monotonic() + timeout + 15
        offset = 0
        while True:
            try:
                state = json.loads(state_path.read_text())
            except (FileNotFoundError, json.JSONDecodeError):
                state = {"status": "reserved"}
            if state.get("plan_hash") not in (None, expected_hash):
                reject("Existing run belongs to another approved plan")
            if events_path.exists():
                with events_path.open() as journal:
                    journal.seek(offset)
                    while True:
                        start = journal.tell()
                        line = journal.readline()
                        if not line or not line.endswith("\n"):
                            offset = start
                            break
                        item = json.loads(line)
                        offset = journal.tell()
                        wire(item)
            if state.get("status") == "finished":
                wire({"type": "result", "result": state["result"], "resumed": not new_run})
                return
            if state.get("status") == "unknown" or time.monotonic() >= deadline:
                wire({"type": "result", "result": {"exit_code": None, "signal": None,
                     "timed_out": False, "unknown": True, "outputs": {},
                     "error": "Remote completion is unknown; do not replay this step"}})
                return
            time.sleep(0.1)
    except (BrokenPipeError, ConnectionError):
        return
    except Exception as exc:
        reject(masked(str(exc)))


WRAPPER_SOURCE = (
    inspect.getsource(StreamingRedactor)
    + "\n\n"
    + inspect.getsource(_remote_main)
    + "\ntry:\n    _remote_main()\nexcept (BrokenPipeError, ConnectionError):\n    pass\n"
)
WRAPPER_SHA256 = hashlib.sha256(WRAPPER_SOURCE.encode()).hexdigest()


def runtime_substitutions(step: dict, context: dict) -> dict | None:
    from srs.job_logs import JOB_ID_PATTERN, OFFSET_PATTERN

    argv = step.get("argv") or []
    tokens = set()
    for value in argv:
        if not isinstance(value, str):
            raise ValueError("Approved argv is invalid")
        tokens.update(match.group(0) for match in OFFSET_PATTERN.finditer(value))
        if "${job_log_" in OFFSET_PATTERN.sub("", value):
            raise ValueError("Unsupported or malformed job-log runtime placeholder")
    scope = step.get("job_log_scope")
    if not tokens and scope is None and step.get("operation") != "job_logs":
        return None
    if step.get("operation") != "job_logs" or step.get("read_only") is not True:
        raise ValueError("Job-log offsets are allowed only in approved read-only job-log probes")
    if not isinstance(scope, dict) or set(scope) != {"job_id", "slurm_job_id", "streams"}:
        raise ValueError("An exact approved job-log scope is required")
    job_id, slurm_id, streams = scope["job_id"], scope["slurm_job_id"], scope["streams"]
    if not isinstance(job_id, str) or not re.fullmatch(JOB_ID_PATTERN, job_id):
        raise ValueError("Invalid registered job-log identifier")
    slurm_pattern = r"(?:demo-)?[0-9]{1,20}" if context.get("mode") == "demo" else r"[0-9]{1,20}"
    if not isinstance(slurm_id, str) or not re.fullmatch(slurm_pattern, slurm_id):
        raise ValueError("Invalid approved Slurm job-log identifier")
    if not isinstance(streams, list) or sorted(streams) != ["stderr", "stdout"]:
        raise ValueError("Job-log scope must explicitly include stdout and stderr")
    expected = {"${job_log_offset:" + job_id + ":" + stream + "}" for stream in streams}
    if tokens != expected:
        raise ValueError("Job-log placeholders differ from the approved job and stream scope")
    return {
        "kind": "job_log_offsets", "scope": {"job_id": job_id, "slurm_job_id": slurm_id,
                                                "streams": list(streams)},
        "tokens": [{"token": token, "value_type": "integer", "minimum": 0,
                    "maximum": 9223372036854775807} for token in sorted(expected)],
        "source": "context.job_log_offsets[job_id][stream]",
        "target": "literal decimal substitution in approved argv strings only",
        "missing_value_policy": "fail before connection; no defaults; restart must restore persisted offsets",
        "read_scope": "/srv/srs/results/<approved-slurm-job-id>.stdout and .stderr only",
        "page_max_bytes": 65536, "redaction_lookaround_bytes": 4096,
        "raw_byte_policy": "lookaround is memory-only encrypted transport; emit only masked page bytes and text",
    }


def resolve_runtime_argv(step: dict, context: dict) -> list[str]:
    """Bind only manifest-declared byte offsets; never modify the approved step."""
    from srs.job_logs import OFFSET_PATTERN

    rules = runtime_substitutions(step, context)
    if rules is None:
        return list(step["argv"])
    values = context.get("job_log_offsets")
    job_id = rules["scope"]["job_id"]
    if not isinstance(values, dict) or not isinstance(values.get(job_id), dict):
        raise ValueError("Persisted job-log offsets are required; zero is not an implicit default")
    for stream in rules["scope"]["streams"]:
        value = values[job_id].get(stream)
        if type(value) is not int or not 0 <= value <= 9223372036854775807:
            raise ValueError("Persisted job-log byte offsets must be explicit nonnegative integers")

    def substitute(match):
        return str(values[match.group(1)][match.group(2)])

    return [OFFSET_PATTERN.sub(substitute, value) for value in step["argv"]]


def build_manifest(step: dict, context: dict) -> dict:
    """No I/O: the planner can include this complete transport in its hash."""
    username = step.get("execution_user") or context.get("host", {}).get("ssh_username")
    if not username or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_-]{0,63}", username):
        raise ValueError("A concrete execution_user is required")
    argv = ["/usr/bin/python3", "-u", "-c", WRAPPER_SOURCE]
    if step.get("requires_sudo"):
        argv = ["/usr/bin/sudo", "-k", "-S", "-p", SUDO_PROMPT, "-u", username, "--", *argv]
    return {
        "protocol_version": PROTOCOL_VERSION,
        "runtime_substitutions": runtime_substitutions(step, context),
        "structured_stdout": "job_log" if step.get("operation") == "job_logs" else
        "artifact" if step.get("operation") == "collect_results" else
        "probe_json" if step.get("operation") in STRUCTURED_RECORD_KINDS else None,
        "structured_record_kinds": list(STRUCTURED_RECORD_KINDS.get(step.get("operation"), [])),
        "structured_stdout_policy": "read-only memory transfer; parse records and nested JSON text then redact before local persistence",
        "launch_argv": argv,
        "wrapper_source": WRAPPER_SOURCE,
        "wrapper_sha256": WRAPPER_SHA256,
        "execution_id": "${execution_id}",
        "step_id": step.get("id") or context.get("step_id"),
        "plan_hash": "${plan_hash}",
        "execution_user": username,
        "read_only": step.get("read_only") is True,
        "state_root": None if step.get("read_only") else "/var/tmp/srs-runner-${uid}",
        "transport": "SSH encrypted stdin JSON; passwords never appear in argv",
        "timeout_policy": {"seconds": step.get("timeout_seconds", 60), "terminate_own_group": True,
                           "kill_grace_seconds": 5},
        "disconnect_policy": "no replay; inspect the same run identity" if not step.get("read_only")
        else "mark incomplete; no retry in this invocation",
    }
