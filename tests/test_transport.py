import asyncio
import base64
import json
import os
import pwd
import subprocess
import sys
import time
from pathlib import Path

import httpx
import pytest
from srs.adapters.demo import DemoAdapter
from srs.adapters.slurm import SlurmAdapter
from srs.adapters.ssh import SSHAdapter
from srs.remote_runner import SUDO_PROMPT, WRAPPER_SOURCE, build_manifest
from srs.transport import CredentialStore, PinnedSSHClient, TransportError, make_adapter

PIN = "SHA256:" + "A" * 43
PLAN_HASH = "b" * 64


def host(alias="Test_Node_101"):
    return {"id": alias, "alias": alias, "hostname": "test.invalid", "ssh_username": "admin",
            "port": 22, "enabled": True, "host_key_sha256": PIN, "inventory_revision": 1}


def context(tmp_path, target=None):
    return {"mode": "remote-slurm", "execution_id": "execution-test", "step_id": "step-1",
            "plan_hash": PLAN_HASH, "host": target or host(), "data_dir": str(tmp_path)}


def ssh_step(ctx, *, sudo=False, read_only=True):
    step = {"id": "step-1", "host_id": ctx["host"]["id"], "kind": "ssh",
            "argv": ["/usr/bin/printf", "%s", "$(do-not-run); and 'quoted'"],
            "cwd": "/", "environment": {}, "timeout_seconds": 2, "requires_sudo": sudo,
            "execution_user": "root" if sudo else "admin", "read_only": read_only, "preconditions": []}
    step["remote_manifest"] = build_manifest(step, ctx)
    return step


def credentials(tmp_path, extra=""):
    path = tmp_path / ".env.hosts"
    path.write_text(
        "HOST_TEST_NODE_101_USERNAME=admin\nHOST_TEST_NODE_101_SSH_PASSWORD=ssh-secret-101\n"
        "HOST_TEST_NODE_101_SUDO_PASSWORD=sudo-secret-101\n"
        "HOST_TEST_NODE_103_USERNAME=otheradmin\nHOST_TEST_NODE_103_SSH_PASSWORD=ssh-secret-103\n"
        + extra
    )
    path.chmod(0o600)
    return path


class FakeKey:
    def __init__(self, pin=PIN):
        self.pin = pin

    def get_fingerprint(self, algorithm):
        assert algorithm == "sha256"
        return self.pin


class FakeInput:
    def __init__(self):
        self.writes = []
        self.password_sent = asyncio.Event()

    def write(self, text):
        self.writes.append(text)
        if text == "sudo-secret-101\n":
            self.password_sent.set()

    async def drain(self):
        await asyncio.sleep(0)

    def write_eof(self):
        pass


class FakeReader:
    def __init__(self, chunks, wait_for=None):
        self.chunks = list(chunks)
        self.wait_for = wait_for

    async def read(self, size=4096):
        await asyncio.sleep(0)
        if self.wait_for is not None:
            await self.wait_for.wait()
        return self.chunks.pop(0) if self.chunks else ""

    async def readline(self):
        return await self.read()


class FakeProcess:
    def __init__(self, *, sudo=False, lost=False, secret_output=False):
        self.stdin = FakeInput()
        items = [{"type": "ready", "protocol": 1}]
        if secret_output:
            items.extend([
                {"type": "event", "sequence": 1, "stream": "stdout", "text": "ssh-secret-"},
                {"type": "event", "sequence": 2, "stream": "stdout", "text": "101"},
            ])
        if not lost:
            items.append({"type": "result", "result": {"exit_code": 0, "signal": None,
                          "timed_out": False, "unknown": False, "outputs": {}}})
        self.stdout = FakeReader([json.dumps(item) + "\n" for item in items],
                                 self.stdin.password_sent if sudo else None)
        self.stderr = FakeReader([SUDO_PROMPT[:7], SUDO_PROMPT[7:]] if sudo else [])

    async def wait_closed(self):
        pass


class FakeConnection:
    def __init__(self, process=None, pin=PIN):
        self.process = process or FakeProcess()
        self.pin = pin
        self.commands = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return None

    def get_server_host_key(self):
        return FakeKey(self.pin)

    async def create_process(self, command, **kwargs):
        self.commands.append(command)
        return self.process


def connector_for(connection, calls):
    def connect(*args, **kwargs):
        calls.append((args, kwargs))
        return connection
    return connect


async def collected(adapter, step, ctx):
    logs = []

    async def emit(stream, text):
        logs.append((stream, text))

    result = await adapter.run_step(step, ctx, emit)
    return result, logs


@pytest.mark.asyncio
async def test_demo_never_loads_credentials_or_connects(monkeypatch, tmp_path):
    def forbidden(*args, **kwargs):
        raise AssertionError("demo attempted external access")
    monkeypatch.setattr(CredentialStore, "read", forbidden)
    monkeypatch.setattr("asyncssh.connect", forbidden)
    adapter = make_adapter("demo", tmp_path / "missing-secret-file")
    result, logs = await collected(adapter, {"kind": "ssh", "argv": ["/bin/false"]}, {"mode": "demo"})
    assert result["exit_code"] == 0
    assert result["outputs"]["simulation"] is True
    assert "[DEMO]" in logs[0][1]
    result, _ = await collected(DemoAdapter(), {}, {"mode": "remote-slurm"})
    assert result["exit_code"] == 65


def test_credentials_are_per_host_and_never_fall_back(tmp_path):
    store = CredentialStore(credentials(tmp_path))
    first = store.for_host(host())
    second_host = host("Test_Node_103")
    second_host["ssh_username"] = "otheradmin"
    second = store.for_host(second_host)
    assert first.password != second.password
    assert first.sudo_password is None
    assert store.for_host(host(), sudo=True).sudo_password == "sudo-secret-101"
    assert "ssh-secret-101" not in repr(first)
    with pytest.raises(TransportError, match="identity"):
        store.for_host(host("Test_Node_103"))
    with pytest.raises(TransportError):
        store.for_host(host("Test_Node_102"))


def test_credentials_insecure_permissions_and_env_interpolation(tmp_path, monkeypatch):
    path = credentials(tmp_path, "PROJECT_VALUE='${UNEXPECTED_ENV}'\n")
    monkeypatch.setenv("UNEXPECTED_ENV", "must-not-be-read")
    assert CredentialStore(path).read()["PROJECT_VALUE"] == "${UNEXPECTED_ENV}"
    path.chmod(0o644)
    with pytest.raises(TransportError, match="0600"):
        CredentialStore(path).read()


def test_pin_callback_requires_exact_identity():
    validator = PinnedSSHClient("test.invalid", 22, PIN)
    assert validator.validate_host_public_key("test.invalid", "127.0.0.1", 22, FakeKey())
    assert not validator.validate_host_public_key("another.invalid", "127.0.0.1", 22, FakeKey())
    assert not validator.validate_host_public_key("test.invalid", "127.0.0.1", 23, FakeKey())
    assert not validator.validate_host_public_key("test.invalid", "127.0.0.1", 22, FakeKey("SHA256:" + "B" * 43))
    assert not validator.validate_host_ca_key("test.invalid", "127.0.0.1", 22, FakeKey())


@pytest.mark.asyncio
async def test_ssh_manifest_identity_and_mode_fail_before_network(tmp_path):
    ctx = context(tmp_path)
    step = ssh_step(ctx)
    calls = []
    adapter = SSHAdapter("remote-slurm", credentials(tmp_path), connector=connector_for(FakeConnection(), calls))
    step["remote_manifest"]["wrapper_sha256"] = "tampered"
    result, _ = await collected(adapter, step, ctx)
    assert result["exit_code"] == 65 and not calls
    step = ssh_step(ctx)
    ctx["mode"] = "demo"
    result, _ = await collected(adapter, step, ctx)
    assert result["exit_code"] == 65 and not calls


@pytest.mark.asyncio
async def test_ssh_pin_mismatch_dispatches_nothing(tmp_path):
    ctx = context(tmp_path)
    connection = FakeConnection(pin="SHA256:" + "B" * 43)
    adapter = SSHAdapter("remote-slurm", credentials(tmp_path), connector=connector_for(connection, []))
    result, _ = await collected(adapter, ssh_step(ctx), ctx)
    assert result["exit_code"] == 65
    assert not connection.commands


@pytest.mark.asyncio
async def test_ssh_quoted_transport_and_stream_redaction(tmp_path):
    ctx = context(tmp_path)
    process = FakeProcess(secret_output=True)
    connection = FakeConnection(process)
    calls = []
    adapter = SSHAdapter("remote-slurm", credentials(tmp_path), connector=connector_for(connection, calls))
    result, logs = await collected(adapter, ssh_step(ctx), ctx)
    assert result["exit_code"] == 0
    assert "ssh-secret-101" not in json.dumps(logs)
    assert "[REDACTED]" in "".join(text for _, text in logs)
    assert calls[0][1]["client_keys"] == [] and calls[0][1]["agent_path"] is None
    assert calls[0][1]["known_hosts"] == [] and calls[0][1]["config"] == []
    assert "ssh-secret-101" not in connection.commands[0]
    payload = json.loads(process.stdin.writes[0])
    assert payload["argv"][-1] == "$(do-not-run); and 'quoted'"
    assert payload["read_only"] is True


@pytest.mark.asyncio
async def test_ssh_sudo_uses_its_own_password_only_after_prompt(tmp_path):
    ctx = context(tmp_path)
    process = FakeProcess(sudo=True)
    adapter = SSHAdapter("remote-slurm", credentials(tmp_path), connector=connector_for(FakeConnection(process), []))
    result, logs = await collected(adapter, ssh_step(ctx, sudo=True), ctx)
    assert result["exit_code"] == 0
    assert process.stdin.writes[0] == "sudo-secret-101\n"
    assert len(process.stdin.writes) == 2
    assert "sudo-secret-101" not in json.dumps(logs)


@pytest.mark.asyncio
async def test_lost_ack_is_unknown_without_retry(tmp_path):
    ctx = context(tmp_path)
    calls = []
    adapter = SSHAdapter("remote-slurm", credentials(tmp_path), connector=connector_for(FakeConnection(FakeProcess(lost=True)), calls))
    result, _ = await collected(adapter, ssh_step(ctx, read_only=False), ctx)
    assert result["unknown"] is True
    assert len(calls) == 1


def runner_payload(tmp_path, argv, *, read_only=False):
    return {"execution_id": "execution-local", "step_id": "step-1", "plan_hash": PLAN_HASH,
            "execution_user": pwd.getpwuid(os.geteuid()).pw_name, "argv": argv, "cwd": str(tmp_path),
            "environment": {}, "timeout_seconds": 3, "read_only": read_only,
            "preconditions": [], "redactions": ["fake-sensitive-password"],
            "state_root": str(tmp_path / "remote-journal")}


def run_wrapper(payload):
    result = subprocess.run([sys.executable, "-u", "-c", WRAPPER_SOURCE],
                            input=json.dumps(payload) + "\n", capture_output=True, text=True, timeout=15)
    items = [json.loads(line) for line in result.stdout.splitlines()]
    assert items and items[0]["type"] == "ready", result.stderr
    return items, result


def test_readonly_wrapper_writes_no_remote_journal_and_masks_split_secret(tmp_path):
    script = "import sys,time; sys.stdout.write('fake-sensitive-'); sys.stdout.flush(); time.sleep(.05); print('password'); print('error-output',file=sys.stderr)"
    payload = runner_payload(tmp_path, [sys.executable, "-c", script], read_only=True)
    items, completed = run_wrapper(payload)
    assert items[-1]["result"]["exit_code"] == 0
    assert not Path(payload["state_root"]).exists()
    assert "fake-sensitive-password" not in completed.stdout
    assert "[REDACTED]" in completed.stdout
    assert any(item.get("stream") == "stderr" for item in items)


def test_durable_wrapper_deduplicates_and_never_persists_raw_secret(tmp_path):
    counter = tmp_path / "counter"
    script = "from pathlib import Path; p=Path('counter'); p.write_text(p.read_text()+'x' if p.exists() else 'x'); print('fake-sensitive-password')"
    payload = runner_payload(tmp_path, [sys.executable, "-c", script])
    first, _ = run_wrapper(payload)
    second, _ = run_wrapper(payload)
    assert first[-1]["result"]["exit_code"] == second[-1]["result"]["exit_code"] == 0
    assert counter.read_text() == "x"
    assert second[-1]["resumed"] is True
    files = list(Path(payload["state_root"]).rglob("*.json*"))
    assert files
    assert all("fake-sensitive-password" not in path.read_text() for path in files)


def test_remote_preconditions_stop_target_execution(tmp_path):
    source = tmp_path / "source"
    source.write_text("new contents")
    payload = runner_payload(tmp_path, [sys.executable, "-c", "open('should-not-exist','w').write('bad')"])
    payload["preconditions"] = [{"type": "file_sha256", "path": str(source), "sha256": "0" * 64}]
    items, _ = run_wrapper(payload)
    assert items[-1]["result"]["precondition_failed"] is True
    assert not (tmp_path / "should-not-exist").exists()


def test_remote_timeout_records_signal_and_only_owns_its_group(tmp_path):
    payload = runner_payload(tmp_path, [sys.executable, "-c", "import time; time.sleep(10)"], read_only=True)
    payload["timeout_seconds"] = 1
    items, _ = run_wrapper(payload)
    assert items[-1]["result"]["timed_out"] is True
    assert items[-1]["result"]["signal"] == 15


def test_reconcile_missing_remote_record_never_executes(tmp_path):
    payload = runner_payload(tmp_path, [sys.executable, "-c", "open('not-allowed','w').write('x')"])
    payload["reconcile_only"] = True
    items, _ = run_wrapper(payload)
    assert items[-1]["result"]["unknown"] is True
    assert not (tmp_path / "not-allowed").exists()
    assert not Path(payload["state_root"]).exists()


def test_detached_runner_survives_connection_process_and_reconciles(tmp_path):
    payload = runner_payload(tmp_path, [sys.executable, "-c", "import time; time.sleep(.4); open('finished','w').write('once')"])
    process = subprocess.Popen([sys.executable, "-u", "-c", WRAPPER_SOURCE], stdin=subprocess.PIPE,
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        assert json.loads(process.stdout.readline())["type"] == "ready"
        process.stdin.write(json.dumps(payload) + "\n")
        process.stdin.flush()
        assert json.loads(process.stdout.readline())["type"] == "event"
        process.terminate()
        process.wait(timeout=5)
    finally:
        if process.poll() is None:
            process.kill()
        for stream in (process.stdin, process.stdout, process.stderr):
            stream.close()
    payload["reconcile_only"] = True
    items, _ = run_wrapper(payload)
    assert items[-1]["result"]["exit_code"] == 0
    assert (tmp_path / "finished").read_text() == "once"


def token(username="srs-runner", expiration=None):
    body = {"sun": username, "exp": expiration or time.time() + 300}
    encoded = base64.urlsafe_b64encode(json.dumps(body).encode()).decode().rstrip("=")
    return "test-header." + encoded + ".test-signature"


def slurm_step(ctx, method="POST"):
    path = "/slurm/v0.0.45/job/submit" if method == "POST" else "/slurm/v0.0.45/jobs"
    return {"id": "step-1", "host_id": ctx["host"]["id"], "kind": "slurm_api",
            "execution_user": "srs-runner", "requires_sudo": False, "read_only": method == "GET",
            "request": {"method": method, "path": path, "base_url": "http://127.0.0.1:6820",
                        "body": {"job": {"comment": "srs:test-plan"}, "script": "#!/bin/sh\ntrue\n"} if method == "POST" else None},
            "reconcile_request": {"method": "GET", "path": "/slurm/v0.0.45/jobs", "match_comment": "srs:test-plan"}}


def slurm_adapter(tmp_path, handler, raw_token=None):
    auth = raw_token or token()
    path = credentials(tmp_path, "SLURM_USERNAME=srs-runner\nSLURM_TOKEN=" + auth + "\n")
    return SlurmAdapter("remote-slurm", path,
                        client_factory=lambda **kwargs: httpx.AsyncClient(transport=httpx.MockTransport(handler), **kwargs))


@pytest.mark.asyncio
async def test_slurm_submit_records_id_and_deduplicates(tmp_path):
    requests = []
    def handler(request):
        requests.append(request)
        return httpx.Response(200, json={"job_id": 321, "errors": []})
    adapter = slurm_adapter(tmp_path, handler)
    ctx = context(tmp_path)
    step = slurm_step(ctx)
    first, _ = await collected(adapter, step, ctx)
    second, _ = await collected(adapter, step, ctx)
    assert first["outputs"]["job_id"] == second["outputs"]["job_id"] == "321"
    assert len(requests) == 1
    assert all("test-signature" not in path.read_text() for path in (tmp_path / "adapter-journal").iterdir())


@pytest.mark.asyncio
async def test_slurm_uncertain_post_never_retries_and_approved_get_reconciles(tmp_path):
    requests = []
    def handler(request):
        requests.append(request.method)
        if request.method == "POST":
            raise httpx.ReadTimeout("response lost", request=request)
        return httpx.Response(200, json={"jobs": [{"job_id": 456, "comment": "srs:test-plan", "user_name": "srs-runner"}]})
    adapter = slurm_adapter(tmp_path, handler)
    ctx = context(tmp_path)
    step = slurm_step(ctx)
    first, _ = await collected(adapter, step, ctx)
    second, _ = await collected(adapter, step, ctx)
    assert first["unknown"] and second["unknown"]
    assert requests == ["POST"]
    recovered, _ = await collected(adapter, step, {**ctx, "reconcile_only": True})
    assert recovered["outputs"]["job_id"] == "456"
    assert requests == ["POST", "GET"]


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["url", "path", "readonly", "identity"])
async def test_slurm_rejects_unapproved_scope_before_network(tmp_path, change):
    requests = []
    def handler(request):
        requests.append(request)
        return httpx.Response(200, json={})
    adapter = slurm_adapter(tmp_path, handler)
    ctx = context(tmp_path)
    step = slurm_step(ctx)
    if change == "url":
        step["request"]["base_url"] = "http://169.254.169.254"
    elif change == "path":
        step["request"]["path"] = "/anything/else"
    elif change == "readonly":
        step["read_only"] = True
    else:
        step["execution_user"] = "root"
    result, _ = await collected(adapter, step, ctx)
    assert result["exit_code"] == 65 and not requests


@pytest.mark.asyncio
async def test_slurm_reconciliation_without_approved_get_fails_closed(tmp_path):
    adapter = slurm_adapter(tmp_path, lambda request: pytest.fail("Unexpected HTTP request"))
    ctx = context(tmp_path)
    step = slurm_step(ctx)
    del step["reconcile_request"]
    result, _ = await collected(adapter, step, {**ctx, "reconcile_only": True})
    assert result["unknown"] is True


@pytest.mark.asyncio
async def test_slurm_token_cannot_be_root_or_expired(tmp_path):
    ctx = context(tmp_path)
    for raw_token in (token("root"), token(expiration=1)):
        adapter = slurm_adapter(tmp_path, lambda request: pytest.fail("Unexpected HTTP request"), raw_token)
        result, _ = await collected(adapter, slurm_step(ctx), ctx)
        assert result["exit_code"] == 65
