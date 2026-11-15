"""Deletion stops future work, keeps execution history, and survives inventory seeding."""
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select
from srs.models import Execution, MonitorSession, Node, Schedule, SSHHost, new_id
from srs.seed import seed
from srs.worker import Worker
from test_ssh_hosts import platform as platform
from test_ssh_hosts import result, save


def schedule(client):
    response = client.post('/api/v1/schedules', json={'title': 'Delete test', 'operation': 'host_snapshot',
        'host_ids': ['225'], 'interval_seconds': 30, 'starts_at': datetime.now(UTC).isoformat(),
        'expires_at': (datetime.now(UTC) + timedelta(hours=1)).isoformat(), 'scope': {'job_ids': []}})
    assert response.status_code == 201, response.text
    return response.json()


def test_delete_active_schedule_preserves_history_and_revokes_pending_runs(platform):
    client, app, _ = platform
    item = schedule(client)
    with app.state.factory.begin() as db:
        db.get(Schedule, item['id']).enabled = True
        for state in ['queued', 'succeeded']:
            db.add(Execution(schedule_id=item['id'], status=state, trigger_key=new_id(),
                authorization_kind='schedule', approved_hash='fixture', plan_document={}, owner='admin', mode='demo'))
    assert client.delete('/api/v1/schedules/' + item['id']).status_code == 204
    assert client.get('/api/v1/schedules').json()['items'] == []
    assert client.get('/api/v1/schedules/' + item['id']).status_code == 404
    assert client.post('/api/v1/schedules/' + item['id'] + '/enable').status_code == 404
    with app.state.factory() as db:
        deleted = db.get(Schedule, item['id'])
        assert deleted.status == 'deleted' and not deleted.enabled
        runs = list(db.scalars(select(Execution)))
        assert len(runs) == 2
        assert next(r for r in runs if r.status == 'queued').stop_requested
        assert not next(r for r in runs if r.status == 'succeeded').stop_requested
    Worker(app.state.settings, app.state.factory).trigger_schedules()
    assert len(client.get('/api/v1/executions').json()['items']) == 2


@pytest.mark.parametrize('node_id', ['225', '226', None])
def test_host_delete_clears_credentials_profile_and_does_not_reappear(platform, node_id):
    client, app, worker = platform
    host = save(client, node_id=node_id)
    identifier = host['node_id']
    start = client.post('/api/v1/monitor', json={'host_ids': [identifier, '250']})
    assert start.status_code == 200
    response = client.request('DELETE', '/api/v1/ssh-hosts/' + identifier,
        json={'inventory_revision': 1, 'revision': host['revision']})
    assert response.status_code == 204, response.text
    with app.state.factory() as db:
        assert db.get(SSHHost, identifier) is None
        assert db.get(Node, identifier).facts['deleted_at']
        monitor = db.scalar(select(MonitorSession))
        assert monitor.host_ids == ['250']
        seed(db, app.state.settings)
    worker.import_legacy_settings()
    assert identifier not in {n['id'] for n in client.get('/api/v1/nodes').json()['items']}
    assert client.post('/api/v1/monitor', json={'host_ids': [identifier]}).status_code == 422
    assert client.post('/api/v1/ssh-hosts', json={'node_id': identifier, 'display_name': 'restore',
        'hostname': 'host', 'username': 'user', 'password': 'fixture-only-password'}).status_code == 404


def test_delete_unconfigured_seed_host_and_stale_edits(platform):
    client, app, _ = platform
    assert client.request('DELETE', '/api/v1/ssh-hosts/226', json={'inventory_revision': 1}).status_code == 204
    host = save(client, node_id='225')
    stale = client.request('DELETE', '/api/v1/ssh-hosts/225', json={'inventory_revision': 1, 'revision': host['revision'] + 1})
    assert stale.status_code == 409
    with app.state.factory() as db:
        assert not db.get(Node, '225').facts.get('deleted_at')
        assert db.get(SSHHost, '225') is not None


@pytest.mark.asyncio
async def test_inflight_probe_cannot_restore_a_deleted_host(platform):
    client, app, worker = platform
    host = save(client, node_id='225')
    client.post('/api/v1/monitor', json={'host_ids': ['225']})
    async def probe(_):
        response = client.request('DELETE', '/api/v1/ssh-hosts/225', json={'inventory_revision': 1, 'revision': host['revision']})
        assert response.status_code == 204
        return result()
    worker.probe = probe
    await worker.run_once()
    assert client.get('/api/v1/monitor').json()['results'] == {}
    with app.state.factory() as db:
        assert db.get(SSHHost, '225') is None


def test_delete_requires_csrf_and_correct_permission(platform):
    client, app, _ = platform
    item = schedule(client)
    csrf = client.headers.pop('X-CSRF-Token')
    assert client.delete('/api/v1/schedules/' + item['id']).status_code == 403
    assert client.request('DELETE', '/api/v1/ssh-hosts/226', json={'inventory_revision': 1}).status_code == 403
    client.headers['X-CSRF-Token'] = csrf
    app.state.settings.permissions = 'read'
    assert client.delete('/api/v1/schedules/' + item['id']).status_code == 403
    assert client.request('DELETE', '/api/v1/ssh-hosts/226', json={'inventory_revision': 1}).status_code == 403
