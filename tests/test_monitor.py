"""Monitor HTTP/DB lifecycle and an actual loopback SSH transport (synthetic telemetry)."""
import json
from unittest.mock import AsyncMock

import asyncssh
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from srs.app import create_app
from srs.config import Settings
from srs.models import MonitorSession, Node
from srs.monitor import MONITOR_PROBE, MonitorWorker

PASSWORD = 'monitor-fixture-password'
READING = {'cpu_percent': 23.5, 'memory_used_mb': 1024, 'memory_total_mb': 4096,
           'gpus': [], 'gpu_error': None}


@pytest.fixture
def platform(tmp_path, monkeypatch):
    ping = AsyncMock(return_value={"status": "ok", "ms": 1.25})
    monkeypatch.setattr("srs.ping.ping_host", ping)
    settings = Settings(_env_file=None, data_dir=tmp_path, database_url=f'sqlite:///{tmp_path / "db.sqlite3"}',
                        WEB_ADMIN_PASSWORD=PASSWORD, WEB_ADMIN_USERNAME='admin')
    app = create_app(settings)
    with TestClient(app) as client:
        response = client.post('/api/v1/session', json={'username': 'admin', 'password': PASSWORD})
        client.headers['X-CSRF-Token'] = response.json()['csrf_token']
        yield client, app, MonitorWorker(settings, app.state.factory, tmp_path / 'missing.env')
    app.state.engine.dispose()


def start(client, ids=None):
    result = client.post('/api/v1/monitor', json={'host_ids': ids or ['101', '103']})
    assert result.status_code == 200, result.text
    return result.json()


def due(app):
    with app.state.factory.begin() as db:
        for row in db.scalars(select(MonitorSession)):
            row.next_poll_at = '2000-01-01T00:00:00.000Z'


@pytest.mark.asyncio
async def test_explicit_start_stop_and_no_demo_fallback(platform):
    client, app, worker = platform
    worker.probe = AsyncMock(return_value={'status': 'error', 'error': 'unreachable', 'snapshot': None})
    assert not client.get('/api/v1/monitor').json()['active']
    await worker.run_once()
    worker.probe.assert_not_called()
    assert client.post('/api/v1/monitor', json={'host_ids': ['does-not-exist']}).status_code == 422
    state = start(client)
    assert state['active'] and state['results'] == {}
    await worker.run_once()
    assert worker.probe.await_count == 2
    observed = client.get('/api/v1/monitor').json()
    assert all(r['snapshot'] is None for r in observed['results'].values())
    assert all(r['ping']['ms'] == 1.25 for r in observed['results'].values())
    assert all(n['snapshot'] is None for n in client.get('/api/v1/nodes').json()['items'])
    stopped = client.post('/api/v1/monitor/stop', json={'generation': state['generation']})
    assert not stopped.json()['active']
    due(app)
    await worker.run_once()
    assert worker.probe.await_count == 2
    assert client.post('/api/v1/monitor/heartbeat', json={'generation': state['generation']}).status_code == 409


@pytest.mark.asyncio
@pytest.mark.parametrize('invalidate', ['expiry', 'logout', 'inventory', 'permission'])
async def test_authorization_invalidations_block_dispatch(platform, invalidate):
    client, app, worker = platform
    worker.probe = AsyncMock()
    start(client, ['101'])
    with app.state.factory.begin() as db:
        if invalidate == 'expiry':
            db.scalar(select(MonitorSession)).expires_at = '2000-01-01T00:00:00.000Z'
        elif invalidate == 'inventory':
            db.get(Node, '101').inventory_revision += 1
    if invalidate == 'logout':
        client.delete('/api/v1/session')
    if invalidate == 'permission':
        worker.settings.permissions = 'read'
    await worker.run_once()
    worker.probe.assert_not_called()


@pytest.mark.asyncio
async def test_stop_or_reselection_during_sample_cannot_publish_old_data(platform):
    client, app, worker = platform
    first = start(client, ['101'])
    async def probe(host):
        client.post('/api/v1/monitor/stop', json={'generation': first['generation']})
        start(client, ['103'])
        return {'status': 'online', 'snapshot': READING}
    worker.probe = probe
    await worker.run_once()
    state = client.get('/api/v1/monitor').json()
    assert state['host_ids'] == ['103'] and state['results'] == {}
    client.post('/api/v1/monitor/stop', json={'generation': first['generation']})
    assert client.get('/api/v1/monitor').json()['active']


def test_monitor_csrf_and_permissions(platform):
    client, app, _ = platform
    client.headers.pop('X-CSRF-Token')
    assert client.post('/api/v1/monitor', json={'host_ids': ['101']}).status_code == 403
    app.state.settings.permissions = 'read'
    assert client.post('/api/v1/monitor', json={'host_ids': ['101']}).status_code == 403


@pytest.mark.asyncio
async def test_placeholder_credentials_never_connect(platform, tmp_path):
    _, _, worker = platform
    path = tmp_path / 'hosts.env'
    path.write_text('HOST_TEST_NODE_101_USERNAME=admin\nHOST_TEST_NODE_101_SSH_PASSWORD=<replace>\n')
    path.chmod(0o600)
    worker.credentials.path = path
    worker.connector = lambda *a, **k: pytest.fail('Unexpected network access')
    result = await worker.probe({'alias': 'Test_Node_101', 'ssh_username': 'admin'})
    assert result['status'] == 'error' and result['snapshot'] is None
    assert '範例' in result['error']


@pytest.mark.asyncio
async def test_real_loopback_ssh_with_pin_and_fixed_read_only_command(platform, tmp_path):
    _, _, worker = platform
    key = asyncssh.generate_private_key('ssh-ed25519')
    commands = []
    class Server(asyncssh.SSHServer):
        def begin_auth(self, username):
            return True
        def password_auth_supported(self):
            return True
        def validate_password(self, username, password):
            return username == 'admin' and password == 'loopback-only-secret'
    async def process_handler(process):
        commands.append(process.command)
        process.stdout.write(json.dumps(READING))
        process.exit(0)
    server = await asyncssh.create_server(Server, '127.0.0.1', 0, server_host_keys=[key], process_factory=process_handler)
    path = tmp_path / 'hosts.env'
    path.write_text('HOST_TEST_USERNAME=admin\nHOST_TEST_SSH_PASSWORD=loopback-only-secret\n')
    path.chmod(0o600)
    worker.credentials.path = path
    host = {'alias': 'TEST', 'hostname': '127.0.0.1', 'port': server.get_port(),
            'ssh_username': 'execution-user', 'host_key_sha256': key.get_fingerprint('sha256')}
    try:
        result = await worker.probe(host)
        assert result['status'] == 'online', result
        assert result['snapshot']['source'] == 'remote'
        assert result['ssh_username'] == 'admin'
        assert result['snapshot']['cpu_percent'] == 23.5
        import shlex
        assert shlex.split(commands[0]) == ['/usr/bin/python3', '-B', '-c', MONITOR_PROBE]
        host['host_key_sha256'] = asyncssh.generate_private_key('ssh-ed25519').get_fingerprint('sha256')
        rejected = await worker.probe(host)
        assert rejected['status'] == 'error' and '金鑰' in rejected['error']
        assert len(commands) == 1
        assert 'loopback-only-secret' not in json.dumps([result, rejected])
    finally:
        await worker.close_connections()
        server.close()
        await server.wait_closed()


def test_selection_preserves_other_hosts_and_rotates_only_removed_host(platform):
    client, app, _ = platform
    first = start(client)
    with app.state.factory.begin() as db:
        row = db.scalar(select(MonitorSession))
        tokens = row.inventory['_tokens'].copy()
        row.results = {'101': {'status': 'online'}, '103': {'status': 'online'}}
    changed = start(client, ['101'])
    assert changed['generation'] == first['generation']
    assert changed['results'] == {'101': {'status': 'online'}}
    start(client, ['101', '103'])
    with app.state.factory() as db:
        row = db.scalar(select(MonitorSession))
        assert row.inventory['_tokens']['101'] == tokens['101']
        assert row.inventory['_tokens']['103'] != tokens['103']


@pytest.mark.asyncio
async def test_late_removed_and_readded_host_sample_is_discarded(platform):
    client, app, worker = platform
    state = start(client)
    with app.state.factory() as db:
        row = db.scalar(select(MonitorSession))
        session_id, token = row.id, row.inventory['_tokens']['103']
    async def probe(host):
        start(client, ['101'])
        start(client, ['101', '103'])
        return {'status': 'online', 'snapshot': READING}
    worker.probe = probe
    await worker.watch_host(session_id, state['generation'], '103', token)
    assert client.get('/api/v1/monitor').json()['results'] == {}
