"""Offline regression cases from the independent approval/executor safety review."""

import asyncio
import json
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select
from srs.adapters.ssh import SSHAdapter
from srs.models import Execution, ExecutionStep, Node, Plan, iso
from srs.remote_runner import build_manifest
from test_control_plane import approve, make_plan
from test_control_plane import platform as platform
from test_job_logs import fake_structured_process
from test_transport import FakeConnection, collected, connector_for, context, credentials, ssh_step


def test_nodes_endpoint_does_not_expose_other_owners_result_content(platform):
    client, _, app = platform
    marker = "OTHER-OWNER-PRIVATE-ARTIFACT-CONTENT"
    with app.state.factory.begin() as db:
        node = db.get(Node, "225")
        node.facts = {**node.facts, "results": {"another-owners-job": {"rank-0.json": {"text": marker}}}}
    response = client.get("/api/v1/nodes")
    assert response.status_code == 200
    assert marker not in response.text


def test_worker_that_lost_lease_cannot_commit_step_result(platform):
    client, worker, app = platform
    _, plan = make_plan(client)
    execution = approve(client, plan)

    class LeaseTakenDuringDispatch:
        async def run_step(self, step, context, emit):
            with app.state.factory.begin() as db:
                row = db.get(Execution, context["execution_id"])
                row.worker_id = "replacement-worker"
                row.lease_until = iso(datetime.now(UTC) + timedelta(minutes=5))
            return {"exit_code": 0, "signal": None, "unknown": False,
                    "outputs": {"snapshot": {"stale_worker_result": True}}}

    worker.adapter = LeaseTakenDuringDispatch()
    asyncio.run(worker.run_once())
    with app.state.factory() as db:
        step = db.scalar(select(ExecutionStep).where(ExecutionStep.execution_id == execution["id"]))
        assert step.status == "running", "Stale worker overwrote the replacement worker's step state"
        assert db.get(Node, "225").snapshot is None


def test_started_plan_may_finish_approved_steps_after_start_deadline(platform):
    client, worker, app = platform
    _, plan = make_plan(client, hosts=["225", "250"])
    approve(client, plan)

    class ExpiresAfterFirst:
        calls = 0

        async def run_step(self, step, context, emit):
            self.calls += 1
            with app.state.factory.begin() as db:
                db.get(Plan, plan["id"]).approval_expires_at = iso(datetime.now(UTC) - timedelta(seconds=1))
            return {"exit_code": 0, "signal": None, "unknown": False, "outputs": {}}

    adapter = ExpiresAfterFirst()
    worker.adapter = adapter
    asyncio.run(worker.run_once())
    assert adapter.calls == 2, "Approval expiry limits starting the plan, not completing its approved steps"


@pytest.mark.asyncio
async def test_preflight_nested_json_cannot_persist_escaped_known_secret(tmp_path):
    ctx = context(tmp_path)
    secret = 'quote"slash\\secret'
    raw_file = json.dumps({"x": secret})
    record = {"kind": "preflight_evidence", "files": {
        "/etc/srs/approved-config.json": {"exists": True, "content": raw_file}}}
    process = fake_structured_process(json.dumps(record) + "\n")
    step = ssh_step(ctx)
    step["operation"] = "preflight"
    step["remote_manifest"] = build_manifest(step, ctx)
    secret_file = credentials(tmp_path, "HOST_AI_LAB_225_SSH_PASSWORD='quote\"slash\\\\secret'\n")
    adapter = SSHAdapter("remote-slurm", secret_file, connector=connector_for(FakeConnection(process), []))
    result, logs = await collected(adapter, step, ctx)
    assert result["exit_code"] == 0
    output = json.loads("".join(text for stream, text in logs if stream == "stdout"))
    content = json.loads(output["files"]["/etc/srs/approved-config.json"]["content"])
    assert content["x"] == "[REDACTED]"
