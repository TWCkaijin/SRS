"""Real local HTTP/DB/worker integration; transports are explicitly simulated."""
import asyncio
import copy
import hashlib
import json
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from srs.app import create_app
from srs.config import Settings
from srs.models import (
    Artifact,
    Execution,
    ExecutionStep,
    Job,
    JobLogCursor,
    Node,
    Plan,
    Project,
    Schedule,
    iso,
    now,
)
from srs.worker import Worker

PASSWORD = "local-fixture-only-9274"


@pytest.fixture
def platform(tmp_path):
    settings = Settings(_env_file=None, data_dir=tmp_path / "data", database_url=f"sqlite:///{tmp_path / 'db.sqlite3'}",
                        WEB_ADMIN_USERNAME="admin", WEB_ADMIN_PASSWORD=PASSWORD, min_free_bytes=0)
    app = create_app(settings)
    with TestClient(app) as client:
        login = client.post("/api/v1/session", json={"username": "admin", "password": PASSWORD})
        assert login.status_code == 200
        client.headers["X-CSRF-Token"] = login.json()["csrf_token"]
        yield client, Worker(settings, app.state.factory), app
    app.state.engine.dispose()


def make_plan(client, operation="preflight", hosts=None, parameters=None):
    response = client.post("/api/v1/drafts", json={"title": "Local integration", "operation": operation,
        "host_ids": hosts or ["101"], "parameters": parameters or {}})
    assert response.status_code == 201, response.text
    draft = response.json()
    response = client.post(f"/api/v1/drafts/{draft['id']}/plan")
    assert response.status_code == 201, response.text
    return draft, response.json()


def approve(client, plan):
    response = client.post(f"/api/v1/plans/{plan['id']}/approve", json={"content_hash": plan["content_hash"]})
    assert response.status_code == 200, response.text
    return response.json()["execution"]


def test_no_dispatch_until_approval_and_cursor_download(platform):
    client, worker, app = platform
    _, plan = make_plan(client)
    assert asyncio.run(worker.run_once()) is None
    for url in ["nodes", "projects", "jobs", "plans", "schedules"]:
        assert client.get("/api/v1/" + url).status_code == 200
    assert client.get("/api/v1/executions").json()["items"] == []
    run = approve(client, plan)
    assert asyncio.run(worker.run_once()) == run["id"]
    result = client.get(f"/api/v1/executions/{run['id']}").json()
    assert result["status"] == "succeeded" and result["log_complete"]
    assert result["steps"][0]["exit_code"] == 0
    first = client.get(f"/api/v1/executions/{run['id']}/logs?limit=1").json()
    rest = client.get(f"/api/v1/executions/{run['id']}/logs?after={first['next_cursor']}").json()
    assert rest["items"] and rest["items"][0]["id"] > first["items"][0]["id"]
    download = client.get(f"/api/v1/executions/{run['id']}/download")
    assert download.status_code == 200
    lines = [json.loads(line) for line in download.text.splitlines()]
    assert lines[0]["plan_document"] == plan["document"]
    assert any("DEMO" in row.get("text", "") for row in lines)
    assert PASSWORD not in download.text
    assert client.get("/api/v1/nodes").json()["items"][0]["snapshot"]["source"] == "demo"


def test_duplicate_approval_is_one_execution_even_after_finish(platform):
    client, worker, app = platform
    _, plan = make_plan(client)
    first = approve(client, plan)
    second = approve(client, plan)
    assert first["id"] == second["id"]
    asyncio.run(worker.run_once())
    assert approve(client, plan)["id"] == first["id"]
    assert asyncio.run(worker.run_once()) is None
    with app.state.factory() as db:
        assert db.scalar(select(func.count()).select_from(Execution)) == 1


def test_concurrent_approval_deduplicates(platform):
    client, _, app = platform
    _, plan = make_plan(client)
    with ThreadPoolExecutor(2) as pool:
        responses = list(pool.map(lambda _: client.post(f"/api/v1/plans/{plan['id']}/approve",
            json={"content_hash": plan["content_hash"]}), range(2)))
    assert all(r.status_code == 200 for r in responses), [r.text for r in responses]
    assert len({r.json()["execution"]["id"] for r in responses}) == 1


@pytest.mark.parametrize("mutation", ["hash", "inventory", "expiry", "template", "plan_document"])
def test_changed_or_expired_plan_cannot_dispatch(platform, mutation):
    client, worker, app = platform
    _, plan = make_plan(client)
    run = approve(client, plan)
    with app.state.factory.begin() as db:
        record = db.get(Execution, run["id"])
        plan_record = db.get(Plan, plan["id"])
        if mutation == "hash":
            document = copy.deepcopy(record.plan_document)
            document["steps"][0]["argv"].append("unapproved")
            record.plan_document = document
        elif mutation == "inventory":
            db.get(Node, "101").ssh_username = "other-admin"
        elif mutation == "expiry":
            plan_record.approval_expires_at = iso(datetime.now(UTC) - timedelta(seconds=1))
        elif mutation == "plan_document":
            document = copy.deepcopy(plan_record.document)
            document["steps"][0]["cwd"] = "/etc"
            plan_record.document = document
        else:
            document = copy.deepcopy(record.plan_document)
            document["source"]["template_version"] = "changed"
            record.plan_document = document
    assert asyncio.run(worker.run_once()) is None
    result = client.get(f"/api/v1/executions/{run['id']}").json()
    assert result["status"] in {"invalidated", "expired"}
    assert client.get(f"/api/v1/executions/{run['id']}/logs").json()["items"] == []


def test_draft_change_revokes_queued_approval(platform):
    client, worker, _ = platform
    draft, plan = make_plan(client)
    run = approve(client, plan)
    response = client.patch(f"/api/v1/drafts/{draft['id']}", json={"title": "new", "operation": "preflight",
        "host_ids": ["103"], "parameters": {}})
    assert response.status_code == 200
    assert asyncio.run(worker.run_once()) is None
    assert client.get(f"/api/v1/executions/{run['id']}").json()["status"] == "invalidated"


def test_origin_csrf_session_and_excluded_node(platform):
    client, _, _ = platform
    assert client.post("/api/v1/drafts", headers={"X-CSRF-Token": "wrong"}, json={}).status_code == 403
    assert client.post("/api/v1/session", headers={"Origin": "https://evil.example"}, json={"username": "admin", "password": PASSWORD}).status_code == 403
    assert client.post("/api/v1/drafts", json={"title": "x", "operation": "preflight", "host_ids": ["102"]}).status_code == 422
    assert client.post("/api/v1/drafts", json={"title": "x", "operation": "preflight", "host_ids": ["101"], "parameters": {"password": "x"}}).status_code == 422
    assert client.delete("/api/v1/session").status_code == 200
    assert client.get("/api/v1/nodes").status_code == 401


def test_failure_stops_dependent_steps_and_preserves_streams(platform):
    client, worker, _ = platform
    class Failing:
        calls = 0
        async def run_step(self, step, context, emit):
            self.calls += 1
            await emit("stdout", "normal\n")
            await emit("stderr", "failure\n")
            return {"exit_code": 17, "signal": "SIGTERM", "timed_out": True, "unknown": False, "outputs": {}}
    adapter = Failing()
    worker.adapter = adapter
    _, plan = make_plan(client, hosts=["101", "103"])
    run = approve(client, plan)
    asyncio.run(worker.run_once())
    result = client.get(f"/api/v1/executions/{run['id']}").json()
    assert result["status"] == "failed" and adapter.calls == 1
    assert [s["status"] for s in result["steps"]] == ["failed", "skipped"]
    assert result["steps"][0]["outputs"]["timed_out"]
    logs = client.get(f"/api/v1/executions/{run['id']}/logs").json()["items"]
    assert {r["stream"] for r in logs} == {"stdout", "stderr", "audit"}


def test_disk_full_prevents_any_dispatch(platform):
    client, worker, _ = platform
    worker.settings.min_free_bytes = 10 ** 30
    class Never:
        async def run_step(self, *args):
            pytest.fail("must not dispatch without durable logs")
    worker.adapter = Never()
    _, plan = make_plan(client)
    run = approve(client, plan)
    asyncio.run(worker.run_once())
    result = client.get(f"/api/v1/executions/{run['id']}").json()
    assert result["status"] == "unknown" and result["log_complete"] is False


def test_stop_queued_does_not_dispatch(platform):
    client, worker, _ = platform
    _, plan = make_plan(client)
    run = approve(client, plan)
    assert client.post(f"/api/v1/executions/{run['id']}/stop").status_code == 200
    assert asyncio.run(worker.run_once()) is None


def test_restart_reconciles_running_step_without_replaying_completed(platform):
    client, worker, app = platform
    _, plan = make_plan(client, hosts=["101", "103"])
    run = approve(client, plan)
    with app.state.factory.begin() as db:
        execution = db.get(Execution, run["id"])
        execution.status, execution.started_at = "running", now()
        execution.lease_until = iso(datetime.now(UTC) - timedelta(seconds=2))
        db.get(Plan, plan["id"]).status = "running"
        rows = list(db.scalars(select(ExecutionStep).where(ExecutionStep.execution_id == run["id"]).order_by(ExecutionStep.step_id)))
        rows[0].status, rows[1].status = "succeeded", "running"
    class Reconcile:
        calls = []
        async def run_step(self, step, context, emit):
            self.calls.append((step["host_id"], context["reconcile_only"]))
            return {"exit_code": 0, "signal": None, "unknown": False, "outputs": {}}
    adapter = Reconcile()
    worker.adapter = adapter
    asyncio.run(worker.run_once())
    assert adapter.calls == [("103", True)]


def schedule_plan(client):
    stamp = datetime.now(UTC)
    values = {"title": "Read only", "operation": "host_snapshot", "host_ids": ["101"], "interval_seconds": 5,
              "starts_at": iso(stamp - timedelta(seconds=1)), "expires_at": iso(stamp + timedelta(hours=1)), "scope": {"job_ids": []}}
    response = client.post("/api/v1/schedules", json=values)
    assert response.status_code == 201, response.text
    schedule = client.post(f"/api/v1/schedules/{response.json()['id']}/plan").json()
    return values, schedule


def test_schedule_no_overlap_no_catchup_and_expiry(platform):
    client, worker, app = platform
    _, schedule = schedule_plan(client)
    assert worker.trigger_schedules() == []
    assert client.post(f"/api/v1/schedules/{schedule['id']}/enable").status_code == 409
    assert client.post(f"/api/v1/schedules/{schedule['id']}/approve", json={"content_hash": schedule["content_hash"]}).status_code == 200
    assert client.post(f"/api/v1/schedules/{schedule['id']}/enable").status_code == 200
    runs = worker.trigger_schedules()
    assert len(runs) == 1
    with app.state.factory.begin() as db:
        db.get(Schedule, schedule["id"]).next_run_at = iso(datetime.now(UTC) - timedelta(days=1))
    assert worker.trigger_schedules() == []
    asyncio.run(worker.run_once())
    assert len(worker.trigger_schedules()) == 1
    assert worker.trigger_schedules() == []
    with app.state.factory.begin() as db:
        db.get(Schedule, schedule["id"]).expires_at = iso(datetime.now(UTC) - timedelta(seconds=1))
    assert worker.trigger_schedules() == []
    assert asyncio.run(worker.run_once()) is None
    result = client.get(f"/api/v1/schedules/{schedule['id']}").json()
    assert result["status"] == "expired" and not result["enabled"]


def test_schedule_change_revokes_approval_and_permissions_are_separate(platform):
    client, worker, app = platform
    values, schedule = schedule_plan(client)
    app.state.settings.permissions = "read,schedule.manage,schedule.approve,schedule.enable,draft.manage"
    _, one_shot = make_plan(client)
    assert client.post(f"/api/v1/plans/{one_shot['id']}/approve", json={"content_hash": one_shot["content_hash"]}).status_code == 403
    assert client.post(f"/api/v1/schedules/{schedule['id']}/approve", json={"content_hash": schedule["content_hash"]}).status_code == 200
    client.post(f"/api/v1/schedules/{schedule['id']}/enable")
    run = worker.trigger_schedules()[0]
    values["interval_seconds"] = 10
    updated = client.patch(f"/api/v1/schedules/{schedule['id']}", json=values)
    assert updated.status_code == 200 and not updated.json()["enabled"]
    assert asyncio.run(worker.run_once()) is None
    assert client.get(f"/api/v1/executions/{run}").json()["status"] == "cancelled"


def test_job_submission_and_cancellation_are_both_plans(platform):
    client, worker, _ = platform
    project = client.get("/api/v1/projects").json()["items"][0]
    _, plan = make_plan(client, "submit_job", parameters={"project_id": project["id"], "profile": "single"})
    submit = next(s for s in plan["document"]["steps"] if s["kind"] == "slurm_api")
    assert submit["request"]["method"] == "POST" and submit["request"]["body"]["script"] == submit["script"]
    assert client.get("/api/v1/jobs").json()["items"] == []
    approve(client, plan)
    asyncio.run(worker.run_once())
    job = client.get("/api/v1/jobs").json()["items"][0]
    assert job["slurm_job_id"].startswith("demo-")
    _, cancellation = make_plan(client, "cancel_job", parameters={"job_id": job["id"]})
    approve(client, cancellation)
    asyncio.run(worker.run_once())
    assert client.get(f"/api/v1/jobs/{job['id']}").json()["status"] == "CANCELLED"


def test_artifact_only_registered_owned_unchanged_and_inside_root(platform, tmp_path):
    client, _, app = platform
    artifact_root = app.state.settings.data_dir / "artifacts"
    artifact_root.mkdir()
    path = artifact_root / "result.txt"
    path.write_text("local generated result")
    with app.state.factory.begin() as db:
        project = db.scalar(select(Project))
        execution = Execution(trigger_key="artifact-fixture", authorization_kind="one_shot", approved_hash="0"*64,
            plan_document={}, owner="admin", mode="demo")
        db.add(execution)
        db.flush()
        job = Job(slurm_job_id="demo-1", project_id=project.id, profile="cpu", execution_id=execution.id, owner="admin")
        db.add(job)
        db.flush()
        artifact = Artifact(job_id=job.id, owner="admin", filename="result.txt", relative_path="result.txt",
            sha256=hashlib.sha256(path.read_bytes()).hexdigest(), size=path.stat().st_size)
        db.add(artifact)
        db.flush()
        identifier = artifact.id
    assert client.get(f"/api/v1/artifacts/{identifier}/download").text == "local generated result"
    path.write_text("tampered")
    assert client.get(f"/api/v1/artifacts/{identifier}/download").status_code == 409
    with app.state.factory.begin() as db:
        db.get(Artifact, identifier).relative_path = "../../outside"
    assert client.get(f"/api/v1/artifacts/{identifier}/download").status_code == 404


def submit_demo_job(client, worker):
    project = client.get("/api/v1/projects").json()["items"][0]
    _, plan = make_plan(client, "submit_job", parameters={"project_id": project["id"], "profile": "single"})
    approve(client, plan)
    asyncio.run(worker.run_once())
    return client.get("/api/v1/jobs").json()["items"][0]


def test_collected_demo_result_is_downloadable_and_explicitly_simulated(platform):
    client, worker, _ = platform
    job = submit_demo_job(client, worker)
    _, plan = make_plan(client, "collect_results", parameters={"job_id": job["id"]})
    assert client.get(f"/api/v1/jobs/{job['id']}/artifacts").json()["items"] == []
    run = approve(client, plan)
    asyncio.run(worker.run_once())
    assert client.get(f"/api/v1/executions/{run['id']}").json()["status"] == "succeeded"
    artifact = client.get(f"/api/v1/jobs/{job['id']}/artifacts").json()["items"][0]
    data = client.get(f"/api/v1/artifacts/{artifact['id']}/download").json()
    assert data["simulation"] is True and data["source"] == "demo"


def test_job_log_schedule_persists_source_byte_cursor(platform):
    client, worker, app = platform
    job = submit_demo_job(client, worker)
    stamp = datetime.now(UTC)
    schedule = client.post("/api/v1/schedules", json={"title": "Job logs", "operation": "job_logs", "host_ids": ["101"],
        "interval_seconds": 5, "starts_at": iso(stamp-timedelta(seconds=1)), "expires_at": iso(stamp+timedelta(hours=1)),
        "scope": {"job_ids": [job["id"]]}}).json()
    schedule = client.post(f"/api/v1/schedules/{schedule['id']}/plan").json()
    assert "${job_log_offset:" in json.dumps(schedule["plan_document"])
    assert client.post(f"/api/v1/schedules/{schedule['id']}/approve", json={"content_hash": schedule["content_hash"]}).status_code == 200
    client.post(f"/api/v1/schedules/{schedule['id']}/enable")
    first = worker.trigger_schedules()[0]
    asyncio.run(worker.run_once())
    assert client.get(f"/api/v1/executions/{first}").json()["status"] == "succeeded"
    with app.state.factory.begin() as db:
        cursor = db.scalar(select(JobLogCursor).where(JobLogCursor.job_id == job["id"], JobLogCursor.stream == "stdout"))
        assert cursor.offset == len(b"[DEMO] Simulated training completed.\n")
        db.get(Schedule, schedule["id"]).next_run_at = iso(datetime.now(UTC)-timedelta(seconds=1))
    second = worker.trigger_schedules()[0]
    asyncio.run(worker.run_once())
    logs = client.get(f"/api/v1/executions/{second}/logs").json()["items"]
    records = [json.loads(item["text"]) for item in logs if item["stream"] == "stdout"]
    assert all(item["text"] == "" for item in records)


def test_unknown_requires_reconciliation_and_never_blind_resubmit(platform):
    client, worker, _ = platform
    class Uncertain:
        contexts = []
        async def run_step(self, step, context, emit):
            self.contexts.append(context["reconcile_only"])
            if not context["reconcile_only"]:
                return {"exit_code": None, "signal": None, "unknown": True, "outputs": {}, "error": "Connection interrupted"}
            return {"exit_code": 0, "signal": None, "unknown": False, "outputs": {"reconciled": True}}
    worker.adapter = Uncertain()
    _, plan = make_plan(client, "write_config", parameters={"content": {"fixture": True}})
    run = approve(client, plan)
    asyncio.run(worker.run_once())
    assert asyncio.run(worker.run_once()) is None
    assert client.get(f"/api/v1/executions/{run['id']}").json()["status"] == "unknown"
    assert client.post(f"/api/v1/executions/{run['id']}/reconcile").status_code == 200
    asyncio.run(worker.run_once())
    assert worker.adapter.contexts == [False, True]
    result = client.get(f"/api/v1/executions/{run['id']}").json()
    assert result["status"] == "succeeded" and result["log_complete"] is False


def test_validation_never_echoes_password(platform):
    client, _, _ = platform
    secret = "unexpected-secret" * 100
    response = client.post("/api/v1/session", json={"username": "admin", "password": secret})
    assert response.status_code == 422 and secret not in response.text
