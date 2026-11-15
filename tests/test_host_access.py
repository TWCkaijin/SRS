from unittest.mock import AsyncMock

import pytest
from srs import host_access as access
from srs.models import SSHHost
from starlette.websockets import WebSocketDisconnect
from test_ssh_hosts import platform as platform
from test_ssh_hosts import save


def trusted(platform):
    client, app, _ = platform
    host = save(client)
    with app.state.factory.begin() as db:
        db.get(SSHHost, host["node_id"]).host_key_sha256 = "SHA256:" + "A" * 43
    return client, app, host


class Connection:
    def __init__(self, result):
        self.run = AsyncMock(return_value=result)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        pass


def test_direct_account_mutation_is_blocked(platform, monkeypatch):
    client, app, host = trusted(platform)
    connector = AsyncMock()
    monkeypatch.setattr(access, "connect", connector)
    path = f"/api/v1/ssh-hosts/{host['node_id']}/accounts"
    payload = {"revision": 1, "action": "create", "username": "researcher", "password": "secret"}
    assert client.post(path, json=payload).status_code == 409
    client.headers.pop("X-CSRF-Token")
    assert client.post(path, json=payload).status_code == 403
    connector.assert_not_awaited()


def test_accounts_failure_never_echoes_secrets(platform, monkeypatch):
    client, _, host = trusted(platform)
    monkeypatch.setattr(access, "connect", AsyncMock(side_effect=RuntimeError("secret-password")))
    response = client.get(f"/api/v1/ssh-hosts/{host['node_id']}/accounts")
    assert response.status_code == 502 and "secret-password" not in response.text


def test_terminal_requires_origin_session_csrf_and_permission(platform, monkeypatch):
    client, app, host = trusted(platform)
    connector = AsyncMock(side_effect=RuntimeError("do not connect"))
    monkeypatch.setattr(access, "connect", connector)
    path = f"/api/v1/ssh-hosts/{host['node_id']}/terminal"
    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect(path, headers={"origin": "https://evil.example"}):
            pass
    with client.websocket_connect(path, headers={"origin": "http://127.0.0.1:8000"}) as ws:
        ws.send_json({"csrf": "wrong", "revision": 1})
        assert ws.receive_json()["type"] == "error"
    app.state.settings.permissions = "read"
    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect(path, headers={"origin": "http://127.0.0.1:8000"}):
            pass
    connector.assert_not_awaited()


