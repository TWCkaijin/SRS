"""Structured fixed probes never persist escaped credentials or malformed JSON."""

import json

import pytest
from srs.adapters.ssh import SSHAdapter, _redact_nested_json
from srs.remote_runner import build_manifest
from test_job_logs import fake_structured_process
from test_transport import FakeConnection, collected, connector_for, context, credentials, ssh_step


@pytest.mark.asyncio
@pytest.mark.parametrize("operation,kind", [
    ("preflight", "host_snapshot"), ("preflight", "preflight_evidence"),
    ("host_snapshot", "host_snapshot"), ("cluster_probe", "cluster_probe"),
    ("deployment_probe", "deployment_probe"),
])
async def test_all_fixed_json_probes_mask_nested_escaped_secrets(tmp_path, operation, kind):
    ctx = context(tmp_path)
    secret = 'quote"slash\\secret'
    nested = json.dumps({"inner": json.dumps({secret: secret})})
    record = {"kind": kind, "source": "remote", "nested": nested, "value": secret}
    process = fake_structured_process(json.dumps(record) + "\n", chunk_size=11)
    step = ssh_step(ctx)
    step["operation"] = operation
    step["remote_manifest"] = build_manifest(step, ctx)
    assert step["remote_manifest"]["structured_stdout"] == "probe_json"
    assert kind in step["remote_manifest"]["structured_record_kinds"]
    secret_file = credentials(tmp_path, "HOST_TEST_NODE_101_SSH_PASSWORD='quote\"slash\\\\secret'\n")
    adapter = SSHAdapter("remote-slurm", secret_file, connector=connector_for(FakeConnection(process), []))
    result, logs = await collected(adapter, step, ctx)
    assert result["exit_code"] == 0
    output = json.loads("".join(text for stream, text in logs if stream == "stdout"))
    assert output["value"] == "[REDACTED]"
    inner = json.loads(json.loads(output["nested"])["inner"])
    assert inner == {"[REDACTED]": "[REDACTED]"}


@pytest.mark.asyncio
async def test_unapproved_record_kind_is_not_persisted(tmp_path):
    ctx = context(tmp_path)
    process = fake_structured_process(json.dumps({"kind": "artifact", "text": "unapproved raw text"}) + "\n")
    step = ssh_step(ctx)
    step["operation"] = "host_snapshot"
    step["remote_manifest"] = build_manifest(step, ctx)
    adapter = SSHAdapter("remote-slurm", credentials(tmp_path), connector=connector_for(FakeConnection(process), []))
    result, logs = await collected(adapter, step, ctx)
    assert result["unknown"]
    assert not [text for stream, text in logs if stream == "stdout"]


def test_evidence_text_keeps_exact_original_format_without_secret():
    raw = '{\n  "valid": true, "unicode": "中文"\n}\n'
    item = {"content": raw, "sha256": "a" * 64}
    assert _redact_nested_json(item, ["different-known-credential"]) == item


def test_masking_json_syntax_character_preserves_valid_json():
    nested = json.dumps({"value": 'a "quote"'})
    masked = _redact_nested_json({"content": nested}, ['"'])
    assert json.loads(masked["content"])["value"] == "a [REDACTED]quote[REDACTED]"


def test_nested_unicode_escape_is_decoded_before_masking():
    secret = "中文密碼"
    nested = json.dumps({"value": json.dumps({"password": secret}, ensure_ascii=True)}, ensure_ascii=True)
    safe = _redact_nested_json(nested, [secret])
    assert json.loads(json.loads(safe)["value"])["password"] == "[REDACTED]"
