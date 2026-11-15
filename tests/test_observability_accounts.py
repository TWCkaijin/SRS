import io
import json
from contextlib import redirect_stdout
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import select
from srs import host_access
from srs.account_plans import EXEC_SCRIPT
from srs.models import AccountPlan, AuditEvent, MonitorAlert, MonitorHistory, SSHHost, iso, now
from srs.monitor import Reading
from srs.observability import record_sample
from test_host_access import Connection, trusted
from test_ssh_hosts import platform as platform

USER = {
    "username": "student",
    "uid": 1001,
    "gid": 1001,
    "name": "Student",
    "home": "/home/student",
    "shell": "/bin/bash",
}
ROOT = {**USER, "username": "root", "uid": 0, "gid": 0}


def setup_plan(platform, monkeypatch, action="update", **changes):
    client, app, host = trusted(platform)
    conn = Connection(SimpleNamespace(exit_status=0, stdout=json.dumps({"items": [USER, ROOT]})))
    connector = AsyncMock(return_value=conn)
    monkeypatch.setattr(host_access, "connect", connector)
    path = f"/api/v1/ssh-hosts/{host['node_id']}/account-plans"
    response = client.post(
        path, json={"revision": 1, "action": action, "username": "student", "name": "Researcher", **changes}
    )
    assert response.status_code == 200, response.text
    plan = response.json()
    # Planning reads only; neither sudo nor mutating commands are dispatched.
    command = conn.run.call_args.args[0]
    assert "sudo" not in command and "useradd" not in command and "usermod" not in command
    conn.run.reset_mock()
    conn.run.return_value = SimpleNamespace(exit_status=0, stdout='{"ok":true}')
    return client, app, host, path, plan, conn, connector


def test_review_binding_single_use_and_secret_redaction(platform, monkeypatch):
    client, app, host, path, plan, conn, connector = setup_plan(platform, monkeypatch, change_password=True)
    payload = {
        "content_hash": plan["content_hash"],
        "confirmation": "student",
        "password": "new-private-password",
        "sudo_password": "sudo-private-password",
    }
    assert (
        client.post(
            path + "/" + plan["id"] + "/execute", json={**payload, "confirmation": "wrong"}
        ).status_code
        == 422
    )
    assert (
        client.post(
            path + "/" + plan["id"] + "/execute", json={**payload, "content_hash": "0" * 64}
        ).status_code
        == 409
    )
    conn.run.assert_not_awaited()
    response = client.post(path + "/" + plan["id"] + "/execute", json=payload)
    assert response.status_code == 200, response.text
    assert client.post(path + "/" + plan["id"] + "/execute", json=payload).status_code == 409
    conn.run.assert_awaited_once()
    command = conn.run.call_args.args[0]
    stdin = conn.run.call_args.kwargs["input"]
    assert "new-private-password" not in command and "sudo-private-password" not in command
    assert json.loads(stdin.splitlines()[-1])["password"] == "new-private-password"
    with app.state.factory() as db:
        stored = db.get(AccountPlan, plan["id"])
        assert stored.status == "succeeded"
        serialized = json.dumps(stored.document) + json.dumps(
            [e.details for e in db.scalars(select(AuditEvent))]
        )
        assert "new-private-password" not in serialized and "sudo-private-password" not in serialized
    assert "new-private-password" not in client.get(path).text
    assert "sudo-private-password" not in client.get(path).text


@pytest.mark.parametrize("invalidate", ["expiry", "revision", "tamper", "permission", "csrf", "pin"])
def test_invalidated_plan_never_dispatches(platform, monkeypatch, invalidate):
    client, app, host, path, plan, conn, _ = setup_plan(platform, monkeypatch)
    with app.state.factory.begin() as db:
        if invalidate == "expiry":
            db.get(AccountPlan, plan["id"]).expires_at = "2000-01-01T00:00:00.000Z"
        if invalidate == "revision":
            db.get(SSHHost, host["node_id"]).revision += 1
        if invalidate == "pin":
            db.get(SSHHost, host["node_id"]).host_key_sha256 = "SHA256:changed"
        if invalidate == "tamper":
            db.get(AccountPlan, plan["id"]).document = {**plan["document"], "commands": [["id"]]}
    if invalidate == "permission":
        app.state.settings.permissions = "read,hosts.accounts"
    if invalidate == "csrf":
        client.headers.pop("X-CSRF-Token")
    response = client.post(
        path + "/" + plan["id"] + "/execute",
        json={"content_hash": plan["content_hash"], "confirmation": "student"},
    )
    assert response.status_code in (403, 409), response.text
    conn.run.assert_not_awaited()


@pytest.mark.parametrize(
    "action,username",
    [("delete", "root"), ("lock", "observer"), ("update", "missing"), ("create", "student")],
)
def test_protected_and_conflicting_accounts(platform, monkeypatch, action, username):
    client, _, host = trusted(platform)
    conn = Connection(SimpleNamespace(exit_status=0, stdout=json.dumps({"items": [USER, ROOT]})))
    monkeypatch.setattr(host_access, "connect", AsyncMock(return_value=conn))
    response = client.post(
        f"/api/v1/ssh-hosts/{host['node_id']}/account-plans",
        json={"revision": 1, "action": action, "username": username},
    )
    assert response.status_code in (422, 409)


def test_execution_failure_is_not_retried(platform, monkeypatch):
    client, app, _, path, plan, conn, _ = setup_plan(platform, monkeypatch)
    conn.run.side_effect = RuntimeError("secret-should-not-escape")
    route = path + "/" + plan["id"] + "/execute"
    payload = {"content_hash": plan["content_hash"], "confirmation": "student"}
    response = client.post(route, json=payload)
    assert response.status_code == 502 and "secret-should-not-escape" not in response.text
    assert client.post(route, json=payload).status_code == 409
    with app.state.factory() as db:
        assert db.get(AccountPlan, plan["id"]).status == "unconfirmed"


def test_remote_preconditions_and_delete_process_guard(monkeypatch):
    import os
    import pwd
    import subprocess
    import sys

    monkeypatch.setattr(os, "geteuid", lambda: 0)
    monkeypatch.setattr(
        pwd,
        "getpwnam",
        lambda _: SimpleNamespace(
            pw_name="student",
            pw_uid=1001,
            pw_gid=1001,
            pw_gecos="Student",
            pw_dir="/home/student",
            pw_shell="/bin/bash",
        ),
    )
    calls = []

    def runner(argv, **kwargs):
        calls.append(argv)
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(subprocess, "run", runner)
    document = {
        "username": "student",
        "action": "delete",
        "expected": USER,
        "commands": [["userdel", "--", "student"]],
    }
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps({"document": document, "password": ""})))
    with redirect_stdout(io.StringIO()), pytest.raises(SystemExit):
        exec(EXEC_SCRIPT, {})
    assert calls == [["pgrep", "-u", "1001"]]
    calls.clear()
    document["expected"] = {**USER, "uid": 1002}
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps({"document": document, "password": ""})))
    with redirect_stdout(io.StringIO()), pytest.raises(SystemExit):
        exec(EXEC_SCRIPT, {})
    assert calls == []


def sample(cpu=20, **changes):
    return {
        "status": "online",
        "checked_at": now(),
        "error": None,
        "snapshot": {
            "cpu_percent": cpu,
            "memory_total_mb": 100,
            "memory_used_mb": 30,
            "gpus": [],
            "login_users": [],
            "gpu_processes": [],
            **changes,
        },
    }


def test_history_retention_alert_recovery_and_secrets(platform):
    client, app, _ = platform
    with app.state.factory.begin() as db:
        db.add(
            MonitorHistory(
                host_id="225",
                collected_at=iso(datetime.now(UTC) - timedelta(days=8)),
                observer="admin",
                result=sample(),
            )
        )
        record_sample(db, "225", sample(97), "admin")
    with app.state.factory.begin() as db:
        record_sample(
            db,
            "225",
            {"status": "error", "checked_at": now(), "error": "unreachable", "snapshot": None},
            "admin",
        )
    with app.state.factory() as db:
        assert len(list(db.scalars(select(MonitorHistory)))) == 2
        assert len(list(db.scalars(select(MonitorAlert).where(MonitorAlert.resolved_at.is_(None))))) == 2
    with app.state.factory.begin() as db:
        record_sample(db, "225", sample(), "admin")
    response = client.get("/api/v1/observability")
    assert response.status_code == 200
    result = response.json()
    assert all(a["resolved_at"] and a["resolved_by"] is None for a in result["alerts"])
    assert result["web_sessions"][0]["username"] == "admin"
    assert (
        "token_hash" not in response.text
        and "csrf" not in response.text
        and "auth_revision" not in response.text
    )
    assert any(e["action"] == "session.login" for e in result["events"])
    history = client.get("/api/v1/monitor/history/225?hours=168").json()
    assert history["sample_count"] == 3 and history["items"][0]["observer"] == "admin"
    assert client.get("/api/v1/monitor/history/225?hours=2").status_code == 422


def test_manual_alert_resolution_is_durable_and_repeated_anomalies_raise_new_alerts(platform):
    client, app, _ = platform
    with app.state.factory.begin() as db:
        record_sample(db, "225", sample(97), "admin")
    alert = client.get("/api/v1/observability").json()["alerts"][0]
    url = f"/api/v1/observability/alerts/{alert['id']}/resolve"
    assert client.post(url, headers={"X-CSRF-Token": ""}).status_code == 403

    response = client.post(url)
    assert response.status_code == 200, response.text
    closed = response.json()
    assert closed["resolved_at"] and closed["resolved_by"] == "admin"
    assert closed["last_seen_at"] == alert["last_seen_at"]
    assert client.post(url).json() == closed

    with app.state.factory.begin() as db:
        record_sample(db, "225", sample(98), "admin")
    overview = client.get("/api/v1/observability").json()
    assert closed in overview["alerts"]
    pending = [item for item in overview["alerts"] if item["resolved_at"] is None]
    assert len(pending) == 1 and pending[0]["id"] != alert["id"]
    assert pending[0]["rule"] == alert["rule"] and pending[0]["resolved_by"] is None
    events = [event for event in overview["events"] if event["action"] == "monitor.alert.resolve"]
    assert len(events) == 1 and events[0]["subject_id"] == alert["id"]
    assert client.post("/api/v1/observability/alerts/missing/resolve").status_code == 404


def test_missing_gpu_data_does_not_resolve_vram_alert(platform):
    _, app, _ = platform
    with app.state.factory.begin() as db:
        record_sample(
            db, "225", sample(gpus=[{"index": 0, "memory_total_mb": 100, "memory_used_mb": 98}]), "admin"
        )
    with app.state.factory.begin() as db:
        record_sample(db, "225", sample(gpu_error="unavailable"), "admin")
    with app.state.factory() as db:
        assert db.scalar(select(MonitorAlert)).resolved_at is None


def test_telemetry_validates_process_and_login_records():
    reading = Reading.model_validate(
        {
            "cpu_percent": 10,
            "memory_total_mb": 100,
            "memory_used_mb": 30,
            "gpus": [],
            "gpu_processes": [
                {"gpu_uuid": "GPU-1", "pid": 12, "name": "python", "username": "student", "memory_mb": None}
            ],
            "login_users": [
                {
                    "username": "student",
                    "terminal": "pts/0",
                    "login_time": "2026-11-15 10:00",
                    "origin": "(192.0.2.1)",
                }
            ],
        }
    )
    assert reading.gpu_processes[0].memory_mb is None
    with pytest.raises(ValueError):
        Reading.model_validate({**reading.model_dump(), "gpu_processes": [{"pid": -1}]})
