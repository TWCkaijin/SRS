"""Reviewed batch identity and local group management using synthetic SSH hosts."""
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

from sqlalchemy import select
from srs import host_access
from srs.models import AccountPlan, AuditEvent, SSHHost
from test_host_access import trusted
from test_ssh_hosts import platform as platform
from test_ssh_hosts import save


def user(username, uid, gid):
    return {
        "username": username, "uid": uid, "gid": gid, "name": "",
        "home": f"/home/{username}", "shell": "/bin/bash",
    }


def group(name, gid, members=(), local=True):
    return {"name": name, "gid": gid, "members": list(members), "local": local}


def ssh_fixture(monkeypatch, inventories):
    commands = []

    class Connection:
        def __init__(self, host):
            self.host = host

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

        async def run(self, command, **kwargs):
            if kwargs.get("check"):
                return SimpleNamespace(stdout=json.dumps(inventories[self.host]))
            commands.append((self.host, json.loads(kwargs["input"].splitlines()[-1])))
            return SimpleNamespace(exit_status=0, stdout='{"ok":true}')

    monkeypatch.setattr(host_access, "connect", AsyncMock(side_effect=lambda host, _: Connection(host["hostname"])))
    return commands


def test_batch_checks_union_of_uids_and_gids_then_creates_reviewed_passwordless_plans(platform, monkeypatch):
    client, app, first = trusted(platform)
    second = save(client, node_id="226", hostname="192.0.2.51")
    with app.state.factory.begin() as db:
        db.get(SSHHost, second["node_id"]).host_key_sha256 = "SHA256:" + "B" * 43
    inventories = {
        "192.0.2.50": {"items": [user("taken", 1000, 1001)], "groups": [group("team", 1001)]},
        "192.0.2.51": {"items": [], "groups": [group("occupied", 1002)]},
    }
    operations = ssh_fixture(monkeypatch, inventories)
    payload = {"username": "researcher", "hosts": {first["node_id"]: 1, second["node_id"]: 1}}
    url = "/api/v1/accounts/batch-create/plan"
    conflict = client.post(url, json={**payload, "uid": 1002})
    assert conflict.status_code == 409 and second["node_id"] in conflict.text
    with app.state.factory() as db:
        assert list(db.scalars(select(AccountPlan))) == []
    response = client.post(url, json=payload)
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["uid"] == result["gid"] == 1003
    assert len(result["items"]) == 2 and not operations
    for plan in result["items"]:
        document = plan["document"]
        assert document["identity"] == {"uid": 1003, "gid": 1003, "home": "/home/researcher"}
        assert document["commands"][0] == ["groupadd", "-g", "1003", "--", "researcher"]
        assert "1003" == document["commands"][1][document["commands"][1].index("-u") + 1]
        endpoint = f"/api/v1/ssh-hosts/{plan['host_id']}/account-plans/{plan['id']}/execute"
        approval = {"content_hash": plan["content_hash"], "confirmation": "researcher", "password": "fixture-pass"}
        assert client.post(endpoint, json={**approval, "confirmation": "other"}).status_code == 422
        assert client.post(endpoint, json=approval).status_code == 200
        assert client.post(endpoint, json=approval).status_code == 409
    assert [body["password"] for _, body in operations] == ["fixture-pass", "fixture-pass"]
    with app.state.factory() as db:
        stored = list(db.scalars(select(AccountPlan))) + list(db.scalars(select(AuditEvent)))
        assert all("fixture-pass" not in repr(row.__dict__) for row in stored)


def test_group_and_permissions_changes_are_local_and_require_review(platform, monkeypatch):
    client, app, host = trusted(platform)
    target = user("researcher", 1024, 1024)
    facts = {"items": [target], "local_users": ["researcher"], "groups": [
        group("researcher", 1024), group("root", 0), group("lab", 1030), group("sudo", 27, ["researcher"]),
        group("external", 2030, local=False),
    ]}
    operations = ssh_fixture(monkeypatch, {"192.0.2.50": facts})
    url = f"/api/v1/ssh-hosts/{host['node_id']}/account-plans"
    payload = {"username": "researcher", "revision": 1, "membership": "add", "action": "groups"}
    for denied in ("root", "external", "researcher", "sudo"):
        assert client.post(url, json={**payload, "group": denied}).status_code == 422
    assert client.post(url, json={**payload, "username": "observer", "group": "lab"}).status_code == 422
    group_plan = client.post(url, json={**payload, "group": "lab"}).json()
    admin_plan = client.post(url, json={**payload, "action": "permissions", "group": "sudo", "membership": "remove"}).json()
    assert group_plan["document"]["expected_group"] == {"name": "lab", "gid": 1030, "member": False}
    assert group_plan["document"]["commands"] == [["gpasswd", "-a", "researcher", "--", "lab"]]
    assert admin_plan["document"]["commands"] == [["gpasswd", "-d", "researcher", "--", "sudo"]]
    assert not operations
    approval = {"content_hash": admin_plan["content_hash"], "confirmation": "researcher"}
    endpoint = f"{url}/{admin_plan['id']}/execute"
    app.state.settings.permissions = "read,hosts.accounts"
    assert client.post(endpoint, json=approval).status_code == 403
    assert not operations
    app.state.settings.permissions += ",plan.approve"
    assert client.post(endpoint, json=approval).status_code == 200
    assert operations[0][1]["document"]["expected_group"]["member"] is True


def test_runner_refuses_group_with_stale_membership_before_command(tmp_path, monkeypatch):
    import grp
    import io
    import os
    import pwd
    import subprocess
    import sys
    from unittest.mock import Mock

    import pytest
    from srs.account_plans import EXEC_SCRIPT

    target = user("researcher", 1024, 1024)
    monkeypatch.setattr(os, "geteuid", lambda: 0)
    monkeypatch.setattr(pwd, "getpwnam", lambda _: SimpleNamespace(
        pw_name="researcher", pw_uid=1024, pw_gid=1024, pw_gecos="",
        pw_dir="/home/researcher", pw_shell="/bin/bash",
    ))
    monkeypatch.setattr(grp, "getgrnam", lambda _: SimpleNamespace(gr_gid=1030, gr_mem=["researcher"]))
    run = Mock()
    monkeypatch.setattr(subprocess, "run", run)
    document = {
        "username": "researcher", "action": "groups", "expected": target,
        "expected_group": {"name": "team", "gid": 1030, "member": False}, "membership": "add",
        "commands": [["gpasswd", "-a", "researcher", "--", "team"]],
    }
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps({"document": document, "password": ""})))

    def open_fixture(path, mode="r", **_):
        if path == "/etc/group":
            return io.StringIO("team:x:1030:researcher\n")
        if path == "/etc/passwd":
            return io.StringIO("researcher:x:1024:1024::/home/researcher:/bin/bash\n")
        return (tmp_path / "runner.lock").open(mode)

    with pytest.raises(SystemExit) as error:
        exec(EXEC_SCRIPT, {"open": open_fixture})
    assert error.value.code == 1
    run.assert_not_called()
