"""Web-managed observation hosts, encrypted credentials, profiles and telemetry semantics."""
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from srs.app import create_app
from srs.config import Settings
from srs.credential_vault import CredentialVault
from srs.models import Node, SSHHost
from srs.monitor import MonitorWorker
from srs.seed import seed

PASSWORD = 'host-profile-fixture-account'
PROFILE = {'hostname': 'fixture-linux', 'os': 'Linux test', 'kernel': 'test', 'architecture': 'x86_64',
           'cpu_model': 'Fixture CPU', 'logical_cpus': 8, 'physical_cores': 4, 'memory_total_mb': 8192,
           'gpus': [], 'disks': [{'name': 'nvme0n1', 'model': 'Fixture SSD', 'size_bytes': 1000000000,
                               'rotational': False, 'transport': 'nvme', 'mountpoints': ['/']}],
           'interfaces': [], 'disk_error': None, 'gpu_error': None}


@pytest.fixture
def platform(tmp_path):
    settings = Settings(_env_file=None, data_dir=tmp_path / 'data', database_url=f'sqlite:///{tmp_path / "db.sqlite3"}',
                        WEB_ADMIN_PASSWORD=PASSWORD, WEB_ADMIN_USERNAME='admin')
    app = create_app(settings)
    with TestClient(app) as client:
        response = client.post('/api/v1/session', json={'username': 'admin', 'password': PASSWORD})
        client.headers['X-CSRF-Token'] = response.json()['csrf_token']
        yield client, app, MonitorWorker(settings, app.state.factory)
    app.state.engine.dispose()


def save(client, **changes):
    payload = {'display_name': 'GPU workstation', 'hostname': '192.0.2.50', 'port': 22,
               'username': 'observer', 'password': 'private-host-password-not-for-responses', **changes}
    response = client.post('/api/v1/ssh-hosts', json=payload)
    assert response.status_code == 200, response.text
    return response.json()


def result(profile=None):
    return {'status': 'online', 'error': None, 'checked_at': '2026-11-15T00:00:00.000Z',
            'profile': profile or PROFILE, 'host_key_sha256': 'SHA256:' + 'A' * 43,
            'ssh_username': 'observer', 'snapshot': {'source': 'remote', 'cpu_percent': 2}}


@pytest.mark.asyncio
async def test_new_host_needs_no_env_and_profile_survives_reseed(platform):
    client, app, worker = platform
    saved = save(client)
    assert saved['password_configured'] and saved['profile'] is None
    assert 'password' not in saved and 'password_encrypted' not in saved
    with app.state.factory() as db:
        node = db.get(Node, saved['node_id'])
        assert not node.enabled  # Observation registration does not authorize workload changes.
        encrypted = db.get(SSHHost, node.id).password_encrypted
        assert encrypted != 'private-host-password-not-for-responses'
        assert CredentialVault(app.state.settings.data_dir).decrypt(encrypted) == 'private-host-password-not-for-responses'
    assert (app.state.settings.data_dir / 'ssh-credentials.key').stat().st_mode & 0o777 == 0o600
    worker.probe = AsyncMock(return_value=result())
    await worker.run_profiles()
    worker.probe.assert_awaited_once()
    with app.state.factory() as db:
        seed(db, app.state.settings)
    response = client.get('/api/v1/nodes')
    node = next(n for n in response.json()['items'] if n['id'] == saved['node_id'])
    assert node['ssh_connection']['profile']['disks'][0]['transport'] == 'nvme'
    assert node['ssh_connection']['first_seen_at']
    assert 'private-host-password-not-for-responses' not in response.text
    assert encrypted not in response.text
    assert client.post('/api/v1/monitor', json={'host_ids': [saved['node_id']], 'interval_seconds': 1}).status_code == 200


@pytest.mark.asyncio
async def test_226_is_configured_and_observed_like_any_other_host(platform):
    client, _, worker = platform
    saved = save(client, node_id='226')
    worker.probe = AsyncMock(return_value=result())
    await worker.run_profiles()
    response = client.post('/api/v1/monitor', json={'host_ids': ['226'], 'interval_seconds': 1})
    assert response.status_code == 200 and response.json()['interval_seconds'] == 1
    await worker.run_once()
    state = client.get('/api/v1/monitor').json()
    assert state['results']['226']['snapshot']['source'] == 'remote'
    assert saved['node_id'] == '226'


@pytest.mark.asyncio
async def test_empty_password_retains_secret_and_stale_profile_cannot_overwrite_edit(platform):
    client, app, worker = platform
    first = save(client)
    with app.state.factory() as db:
        before = db.get(SSHHost, first['node_id']).password_encrypted
    second = save(client, node_id=first['node_id'], revision=first['revision'], password=None, username='other')
    with app.state.factory() as db:
        assert db.get(SSHHost, first['node_id']).password_encrypted == before
    async def capture(host):
        save(client, node_id=first['node_id'], revision=second['revision'], password=None, hostname='192.0.2.51')
        return result()
    worker.probe = capture
    await worker.run_profiles()
    current = client.get('/api/v1/ssh-hosts').json()['items'][0]
    assert current['hostname'] == '192.0.2.51' and current['profile'] is None


@pytest.mark.asyncio
async def test_unknown_key_requires_matching_explicit_confirmation(platform):
    client, _, worker = platform
    saved = save(client)
    pin = 'SHA256:' + 'B' * 43
    worker.probe = AsyncMock(return_value={'status': 'error', 'error': 'verify host key', 'snapshot': None,
                                         'pending_host_key_sha256': pin})
    await worker.run_profiles()
    host = client.get('/api/v1/ssh-hosts').json()['items'][0]
    assert host['status'] == 'awaiting_trust' and host['host_key_sha256'] is None
    path = f'/api/v1/ssh-hosts/{saved["node_id"]}/trust'
    assert client.post(path, json={'revision': host['revision'], 'fingerprint': 'SHA256:' + 'C' * 43}).status_code == 409
    trusted = client.post(path, json={'revision': host['revision'], 'fingerprint': pin})
    assert trusted.status_code == 200 and trusted.json()['host_key_sha256'] == pin
    assert trusted.json()['status'] == 'queued'


def test_host_settings_permissions_validation_and_secret_redaction(platform):
    client, app, _ = platform
    secret = 'never-echo-this-input-password'
    response = client.post('/api/v1/ssh-hosts', json={'password': secret, 'hostname': 'http://example.com/secret'})
    assert response.status_code == 422 and secret not in response.text
    client.headers.pop('X-CSRF-Token')
    assert client.post('/api/v1/ssh-hosts', json={'display_name': 'x', 'hostname': 'host', 'username': 'user', 'password': secret}).status_code == 403
    app.state.settings.permissions = 'read,monitor.manage'
    assert client.post('/api/v1/ssh-hosts', json={'display_name': 'x', 'hostname': 'host', 'username': 'user', 'password': secret}).status_code == 403


def test_legacy_import_never_overwrites_gui_settings(platform, tmp_path):
    client, app, worker = platform
    save(client, node_id='225', username='gui-user')
    path = tmp_path / 'legacy.env'
    path.write_text('HOST_AI_LAB_225_USERNAME=legacy-user\nHOST_AI_LAB_225_SSH_PASSWORD=legacy-secret\n')
    path.chmod(0o600)
    worker.credentials.path = path
    worker.import_legacy_settings()
    with app.state.factory() as db:
        assert db.get(SSHHost, '225').username == 'gui-user'
        assert len(list(db.scalars(select(SSHHost)))) == 1


def test_network_rates_require_two_samples_and_reject_counter_resets():
    previous = {'snapshot': {'sample_time': 10, 'network': [{'name': 'eth0', 'rx_bytes': 100, 'tx_bytes': 200}]}}
    current = {'snapshot': {'sample_time': 12, 'network': [{'name': 'eth0', 'rx_bytes': 500, 'tx_bytes': 300}]}}
    MonitorWorker.network_rates(current, previous)
    assert current['snapshot']['network'][0]['rx_bytes_per_second'] == 200
    assert current['snapshot']['network'][0]['tx_bytes_per_second'] == 50
    MonitorWorker.network_rates(current, None)
    assert current['snapshot']['network'][0]['rx_bytes_per_second'] is None
    previous['snapshot']['sample_time'] = 20
    MonitorWorker.network_rates(current, previous)
    assert current['snapshot']['network'][0]['rx_bytes_per_second'] is None

@pytest.mark.asyncio
async def test_slow_host_does_not_delay_other_host_sampling(platform):
    import asyncio
    client, app, worker = platform
    state = client.post('/api/v1/monitor', json={'host_ids': ['225', '226'], 'interval_seconds': 1}).json()
    from srs.models import MonitorSession
    with app.state.factory() as db:
        session_id = db.scalar(select(MonitorSession.id))
    slow_started = asyncio.Event()
    release_slow = asyncio.Event()
    async def probe(host):
        if host['id'] == '226':
            slow_started.set()
            await release_slow.wait()
        return result()
    worker.probe = probe
    tasks = [asyncio.create_task(worker.watch_host(session_id, state['generation'], host)) for host in ['225', '226']]
    try:
        await asyncio.wait_for(slow_started.wait(), timeout=2)
        await asyncio.sleep(.05)
        observed = client.get('/api/v1/monitor').json()['results']
        assert '225' in observed and '226' not in observed
        client.post('/api/v1/monitor/stop', json={'generation': state['generation']})
        release_slow.set()
        await asyncio.wait_for(asyncio.gather(*tasks), timeout=2)
        assert '226' not in client.get('/api/v1/monitor').json()['results']
    finally:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
