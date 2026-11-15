import io
import json
from contextlib import redirect_stdout
from types import SimpleNamespace
from unittest.mock import AsyncMock

from srs import training_setup as setup
from srs.models import SSHHost
from srs.ssh_import import parse_config
from test_host_access import Connection, trusted
from test_ssh_hosts import platform as platform


def test_ssh_parser_defaults_exclusions_no_execution():
    parsed = parse_config("""Host node1 node2
 HostName 192.0.2.1
 User researcher
Host * !node2
 Port 2222
Host node2
 ProxyCommand touch /tmp/do-not-execute
Host *
 Port 22
Include secret-file
Match exec "touch /tmp/do-not-execute"
 User root
""")
    a, b = parsed["items"]
    assert a["port"] == 2222 and a["username"] == "researcher"
    assert b["port"] == 22 and b["problem"]
    assert len(parsed["warnings"]) == 2
    assert (
        parse_config("Host alpha\n HostName=host.example\n User = user")["items"][0]["hostname"]
        == "host.example"
    )


def test_import_permission_and_preview_no_write(platform):
    client, app, _ = platform
    body = {"text": "Host node\n HostName 192.0.2.1\n User researcher"}
    assert client.post("/api/v1/ssh-import/preview", json=body).json()["items"][0]["username"] == "researcher"
    assert client.get("/api/v1/ssh-hosts").json()["items"] == []
    app.state.settings.permissions = "read"
    assert client.post("/api/v1/ssh-import/preview", json=body).status_code == 403


def test_training_preview_apply_revision_and_once(platform, monkeypatch):
    client, app, host = trusted(platform)
    facts = {"system": "Linux", "python": "3.11.1", "torch": "2.9.0", "gpus": 2, "cuda": "12.8"}
    connection = Connection(SimpleNamespace(exit_status=0, stdout=json.dumps(facts)))
    monkeypatch.setattr(setup, "connect", AsyncMock(return_value=connection))
    payload = {"host_ids": [host["node_id"]], "master_addr": "192.0.2.50", "device": "cuda", "processes": 2}
    preview = client.post("/api/v1/training/preview", json=payload)
    assert preview.status_code == 200, preview.text
    plan = preview.json()
    assert plan["ready"]
    command = connection.run.call_args.args[0]
    assert "mkdir" not in command and "pip" not in command
    apply = {"token": plan["token"], "host_id": host["node_id"]}
    with app.state.factory.begin() as db:
        db.get(SSHHost, host["node_id"]).revision = 2
    assert client.post("/api/v1/training/apply", json=apply).status_code == 409
    plan = client.post("/api/v1/training/preview", json=payload).json()
    apply["token"] = plan["token"]
    connection.run.return_value = SimpleNamespace(
        exit_status=0, stdout='{"directory":"/home/user/.local/share/srs/training/test","torch":"2.9.0"}'
    )
    assert client.post("/api/v1/training/apply", json=apply).json()["status"] == "success"
    stdin = json.loads(connection.run.call_args.kwargs["input"])
    assert stdin["config"]["rank"] == 0 and stdin["config"]["nodes"] == 1
    assert client.post("/api/v1/training/apply", json=apply).status_code == 409
    assert (
        client.post("/api/v1/training/preview", json={**payload, "python": "/bin/python;id"}).status_code
        == 422
    )
    client.headers.pop("X-CSRF-Token")
    assert client.post("/api/v1/training/preview", json=payload).status_code == 403


def test_training_failure_redacted_and_gpu_not_ready(platform, monkeypatch):
    client, app, host = trusted(platform)
    connection = Connection(
        SimpleNamespace(exit_status=0, stdout=json.dumps({"system": "Linux", "torch": "2.9.0", "gpus": 0}))
    )
    monkeypatch.setattr(setup, "connect", AsyncMock(return_value=connection))
    body = {"host_ids": [host["node_id"]], "master_addr": "node.example"}
    plan = client.post("/api/v1/training/preview", json=body).json()
    assert not plan["ready"]
    assert (
        client.post(
            "/api/v1/training/apply", json={"token": plan["token"], "host_id": host["node_id"]}
        ).status_code
        == 409
    )
    monkeypatch.setattr(setup, "connect", AsyncMock(side_effect=RuntimeError("private-password")))
    result = client.post("/api/v1/training/preview", json=body)
    assert "private-password" not in result.text
    app.state.settings.permissions = "read,hosts.manage"
    assert client.post("/api/v1/training/preview", json=body).status_code == 403


def test_remote_writer_creates_only_isolated_setup(tmp_path, monkeypatch):
    import pathlib
    import subprocess
    import sys

    monkeypatch.setattr(pathlib.Path, "home", lambda: tmp_path)
    calls = []

    def run(args, **kwargs):
        calls.append(args)
        return SimpleNamespace(stdout=json.dumps({"torch": "2.9.0", "cuda": None, "gpus": 0}))

    monkeypatch.setattr(subprocess, "run", run)
    payload = {
        "directory": "plan-unique",
        "install_version": "",
        "probe": setup.PROBE,
        "device": "cpu",
        "processes": 1,
        "config": {"rank": 1},
        "launcher": setup.LAUNCHER,
    }
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(payload)))
    with redirect_stdout(io.StringIO()):
        exec(setup.APPLY, {})
    root = tmp_path / ".local/share/srs/training/plan-unique"
    assert json.loads((root / "config.json").read_text())["rank"] == 1
    assert (root / "launch.py").read_text() == setup.LAUNCHER
    assert len(calls) == 1 and "-m" not in calls[0]
