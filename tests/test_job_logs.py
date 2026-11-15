"""Real local probes and fake SSH transport exercise byte cursors without networking."""

import base64
import copy
import hashlib
import json
import os
import subprocess
import sys

import pytest
from srs import job_logs
from srs.adapters.ssh import SSHAdapter
from srs.remote_runner import build_manifest, resolve_runtime_argv
from test_transport import (
    FakeConnection,
    FakeProcess,
    FakeReader,
    collected,
    connector_for,
    context,
    credentials,
    run_wrapper,
    runner_payload,
)

JOB_ID = "registered-job-1"


def log_step(monkeypatch, tmp_path, page_bytes=65536):
    root = tmp_path.resolve() / "results"
    root.mkdir(exist_ok=True)
    monkeypatch.setattr(job_logs, "LOG_ROOT", str(root))
    ctx = context(tmp_path)
    ctx["job_log_offsets"] = {JOB_ID: {"stdout": 0, "stderr": 0}}
    script = job_logs.script_builder(JOB_ID, "123", page_bytes)
    step = {
        "id": "step-1", "host_id": ctx["host"]["id"], "kind": "ssh", "operation": "job_logs",
        "argv": [sys.executable, "-B", "-c", script], "script": script,
        "cwd": str(tmp_path.resolve()), "environment": {}, "execution_user": "admin",
        "requires_sudo": False, "read_only": True, "timeout_seconds": 3, "preconditions": [],
        "job_log_scope": {"job_id": JOB_ID, "slurm_job_id": "123", "streams": ["stdout", "stderr"]},
    }
    step["remote_manifest"] = build_manifest(step, ctx)
    return root, step, ctx


def run_probe(step, ctx, *, check=True):
    result = subprocess.run(resolve_runtime_argv(step, ctx), capture_output=True, text=True, timeout=5)
    if check:
        assert result.returncode == 0, result.stderr
    return [json.loads(line) for line in result.stdout.splitlines()], result


def fake_structured_process(raw, chunk_size=37):
    process = FakeProcess()
    records = [{"type": "ready", "protocol": 1}]
    for index, start in enumerate(range(0, len(raw), chunk_size), 1):
        records.append({"type": "event", "sequence": index, "stream": "stdout", "text": raw[start:start + chunk_size]})
    records.append({"type": "result", "result": {"exit_code": 0, "signal": None,
                    "timed_out": False, "unknown": False, "outputs": {}}})
    process.stdout = FakeReader([json.dumps(record) + "\n" for record in records])
    return process


def test_manifest_discloses_exact_scope_and_restart_policy(monkeypatch, tmp_path):
    _, step, ctx = log_step(monkeypatch, tmp_path)
    before = copy.deepcopy(step)
    manifest = step["remote_manifest"]
    assert manifest["runtime_substitutions"]["scope"] == step["job_log_scope"]
    assert "restart" in manifest["runtime_substitutions"]["missing_value_policy"]
    ctx["job_log_offsets"][JOB_ID] = {"stdout": 12, "stderr": 345}
    resolved = resolve_runtime_argv(step, ctx)
    assert "${job_log_offset:" not in resolved[-1]
    assert "int('12')" in resolved[-1] and "int('345')" in resolved[-1]
    assert step == before
    assert build_manifest(step, ctx) == manifest


@pytest.mark.asyncio
@pytest.mark.parametrize("bad", [None, {}, {JOB_ID: {}}, {JOB_ID: {"stdout": 0}},
                                    {JOB_ID: {"stdout": -1, "stderr": 0}},
                                    {JOB_ID: {"stdout": True, "stderr": 0}},
                                    {JOB_ID: {"stdout": "0", "stderr": 0}},
                                    {JOB_ID: {"stdout": 2**63, "stderr": 0}}])
async def test_missing_or_invalid_persisted_cursor_fails_before_network(monkeypatch, tmp_path, bad):
    _, step, ctx = log_step(monkeypatch, tmp_path)
    ctx["job_log_offsets"] = bad
    calls = []
    adapter = SSHAdapter("remote-slurm", credentials(tmp_path), connector=connector_for(FakeConnection(), calls))
    result, _ = await collected(adapter, step, ctx)
    assert result["exit_code"] == 65 and not calls


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["unknown_job", "malformed", "scope", "mutating"])
async def test_unapproved_placeholder_scope_fails_before_network(monkeypatch, tmp_path, change):
    _, step, ctx = log_step(monkeypatch, tmp_path)
    if change == "unknown_job":
        step["argv"][-1] = step["argv"][-1].replace(JOB_ID + ":stdout", "another-job:stdout")
    elif change == "malformed":
        step["argv"][-1] += "\n# ${job_log_offset:bad:stdin}\n"
    elif change == "scope":
        step["job_log_scope"]["job_id"] = "another-job"
    else:
        step["read_only"] = False
    calls = []
    adapter = SSHAdapter("remote-slurm", credentials(tmp_path), connector=connector_for(FakeConnection(), calls))
    result, _ = await collected(adapter, step, ctx)
    assert result["exit_code"] == 65 and not calls


def test_all_bytes_and_utf8_survive_many_pages_and_restart(monkeypatch, tmp_path):
    root, step, ctx = log_step(monkeypatch, tmp_path, page_bytes=4096)
    expected = {"stdout": ("中文🙂line\n".encode() * 10000) + b"\xff\x00last\n",
                "stderr": ("stderr測試\n".encode() * 1000)}
    for stream, content in expected.items():
        (root / ("123." + stream)).write_bytes(content)
    collected_bytes = {"stdout": bytearray(), "stderr": bytearray()}
    for _ in range(100):
        records, _ = run_probe(step, ctx)
        for record in records:
            safe = job_logs.sanitize_record(record, step["job_log_scope"], ctx["job_log_offsets"], [])
            collected_bytes[record["stream"]].extend(base64.b64decode(safe["data_b64"]))
            ctx["job_log_offsets"][JOB_ID][record["stream"]] = record["end"]
        # A reconstructed context models executor restart with persisted offsets.
        ctx = json.loads(json.dumps(ctx))
        if not any(record["has_more"] for record in records):
            break
    assert {stream: bytes(value) for stream, value in collected_bytes.items()} == expected
    assert ctx["job_log_offsets"][JOB_ID] == {stream: len(value) for stream, value in expected.items()}


def test_independent_offsets_append_missing_and_truncation(monkeypatch, tmp_path):
    root, step, ctx = log_step(monkeypatch, tmp_path)
    (root / "123.stdout").write_bytes(b"first")
    ctx["job_log_offsets"][JOB_ID] = {"stdout": 5, "stderr": 8}
    records, _ = run_probe(step, ctx)
    assert records[0]["start"] == records[0]["end"] == 5
    assert records[1]["gap"] == "missing" and records[1]["end"] == 8
    (root / "123.stdout").write_bytes(b"first appended")
    records, _ = run_probe(step, ctx)
    safe = job_logs.sanitize_record(records[0], step["job_log_scope"], ctx["job_log_offsets"], [])
    assert safe["text"] == " appended"
    (root / "123.stdout").write_bytes(b"new")
    records, _ = run_probe(step, ctx)
    assert records[0]["gap"] == "truncated" and records[0]["start"] == 0
    assert records[0]["end"] == 3


def test_rotation_identity_is_reported_for_worker_recovery(monkeypatch, tmp_path):
    root, step, ctx = log_step(monkeypatch, tmp_path)
    path = root / "123.stdout"
    path.write_bytes(b"first")
    before, _ = run_probe(step, ctx)
    ctx["job_log_offsets"][JOB_ID]["stdout"] = before[0]["end"]
    path.rename(root / "old-log")
    path.write_bytes(b"entire replacement is longer")
    after, _ = run_probe(step, ctx)
    assert before[0]["inode"] != after[0]["inode"]
    assert after[0]["start"] == 5


@pytest.mark.parametrize("location", ["file", "parent"])
def test_probe_rejects_symlinks_without_reading_target(monkeypatch, tmp_path, location):
    root, step, ctx = log_step(monkeypatch, tmp_path)
    victim = tmp_path.resolve() / "victim"
    victim.write_text("must not read")
    if location == "file":
        (root / "123.stdout").symlink_to(victim)
    else:
        moved = root.with_name("real-results")
        root.rename(moved)
        (moved / "123.stdout").write_text("must not read")
        root.symlink_to(moved, target_is_directory=True)
    records, result = run_probe(step, ctx, check=False)
    assert result.returncode != 0 and not records
    assert "must not read" not in result.stdout


def test_probe_rejects_fifo_without_blocking(monkeypatch, tmp_path):
    root, step, ctx = log_step(monkeypatch, tmp_path)
    os.mkfifo(root / "123.stdout")
    records, result = run_probe(step, ctx, check=False)
    assert result.returncode != 0 and not records


def test_secrets_crossing_pages_and_partial_eof_never_escape(monkeypatch, tmp_path):
    root, step, ctx = log_step(monkeypatch, tmp_path, page_bytes=16)
    secret = "ssh-secret-225"
    path = root / "123.stdout"
    path.write_text("prefix12345678" + secret + "\nend\n")
    output = []
    while True:
        records, _ = run_probe(step, ctx)
        record = records[0]
        safe = job_logs.sanitize_record(record, step["job_log_scope"], ctx["job_log_offsets"], [secret])
        output.append(safe["text"])
        ctx["job_log_offsets"][JOB_ID]["stdout"] = record["end"]
        if not record["has_more"]:
            break
    assert "".join(output).replace("[REDACTED]", "") == "prefix12345678\nend\n"
    ctx["job_log_offsets"][JOB_ID]["stdout"] = 0
    path.write_text("prefix\nssh-secret-")
    for _ in range(2):
        records, _ = run_probe(step, ctx)
        safe = job_logs.sanitize_record(records[0], step["job_log_scope"], ctx["job_log_offsets"], [secret])
        assert "ssh" not in safe["text"] and "secret" not in safe["text"]
        ctx["job_log_offsets"][JOB_ID]["stdout"] = records[0]["end"]
    with path.open("a") as target:
        target.write("225\n")
    records, _ = run_probe(step, ctx)
    safe = job_logs.sanitize_record(records[0], step["job_log_scope"], ctx["job_log_offsets"], [secret])
    assert "225" not in safe["text"] and "[REDACTED]" in safe["text"]


@pytest.mark.asyncio
async def test_ssh_parses_chunked_log_records_before_masking(monkeypatch, tmp_path):
    root, step, ctx = log_step(monkeypatch, tmp_path)
    (root / "123.stdout").write_text('a "quoted" ssh-secret-225\n')
    records, result = run_probe(step, ctx)
    process = fake_structured_process(result.stdout)
    adapter = SSHAdapter("remote-slurm", credentials(tmp_path), connector=connector_for(FakeConnection(process), []))
    result, logs = await collected(adapter, step, ctx)
    assert result["exit_code"] == 0
    decoded = [json.loads(text) for stream, text in logs if stream == "stdout"]
    assert decoded[0]["text"] == 'a "quoted" [REDACTED]\n'
    assert decoded[0]["end"] == records[0]["end"]
    assert "window_start" not in decoded[0]
    assert b"ssh-secret-225" not in base64.b64decode(decoded[0]["data_b64"])
    payload = json.loads(process.stdin.writes[0])
    assert payload["structured_stdout"] == "job_log"
    assert "${job_log_offset:" not in payload["argv"][-1]


@pytest.mark.asyncio
async def test_long_secret_fails_before_network(monkeypatch, tmp_path):
    _, step, ctx = log_step(monkeypatch, tmp_path)
    calls = []
    secret_file = credentials(tmp_path, "HOST_AI_LAB_225_SSH_PASSWORD=" + "s" * 4097 + "\n")
    adapter = SSHAdapter("remote-slurm", secret_file, connector=connector_for(FakeConnection(), calls))
    result, _ = await collected(adapter, step, ctx)
    assert result["exit_code"] == 65 and not calls


@pytest.mark.asyncio
async def test_artifact_nested_json_is_masked_after_parsing(tmp_path):
    ctx = context(tmp_path)
    secret = 'quote"slash\\secret'
    text = json.dumps({"rank": 0, "value": secret}, ensure_ascii=True)
    record = {"kind": "artifact", "job_id": JOB_ID, "filename": "rank-0.json",
              "source_sha256": hashlib.sha256(text.encode()).hexdigest(), "text": text}
    step = {"id": "step-1", "host_id": ctx["host"]["id"], "kind": "ssh", "operation": "collect_results",
            "argv": ["/usr/bin/true"], "cwd": "/", "environment": {}, "execution_user": "admin",
            "read_only": True, "requires_sudo": False, "preconditions": [], "timeout_seconds": 3,
            "evidence": [{"job_id": JOB_ID}]}
    step["remote_manifest"] = build_manifest(step, ctx)
    process = fake_structured_process(json.dumps(record) + "\n")
    secret_file = credentials(tmp_path, "HOST_AI_LAB_225_SSH_PASSWORD='quote\"slash\\\\secret'\n")
    adapter = SSHAdapter("remote-slurm", secret_file, connector=connector_for(FakeConnection(process), []))
    result, logs = await collected(adapter, step, ctx)
    assert result["exit_code"] == 0
    safe = json.loads(next(text for stream, text in logs if stream == "stdout"))
    assert json.loads(safe["text"])["value"] == "[REDACTED]"
    assert safe["source_sha256"] == record["source_sha256"]


def test_wrapper_preserves_structured_json_for_memory_only_sanitization(tmp_path):
    secret = '"'
    script = "import json; print(json.dumps({'kind':'artifact','text':'a quote \\\" value'}))"
    payload = runner_payload(tmp_path, [sys.executable, "-c", script], read_only=True)
    payload.update(structured_stdout="artifact", redactions=[secret])
    items, _ = run_wrapper(payload)
    assert items[-1]["result"]["exit_code"] == 0
    raw = "".join(item["text"] for item in items if item.get("stream") == "stdout")
    assert json.loads(raw)["text"] == 'a quote " value'
    assert not os.path.exists(payload["state_root"])
