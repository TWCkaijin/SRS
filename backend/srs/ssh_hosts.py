"""Authenticated SSH settings. The API stores encrypted secrets; it never opens SSH."""
import ipaddress
import re

from fastapi import Depends, HTTPException
from pydantic import Field, SecretStr, field_validator
from sqlalchemy import delete, select, update
from sqlalchemy.exc import IntegrityError

from srs.credential_vault import CredentialVault
from srs.models import Execution, MonitorSession, Node, Schedule, SSHHost, new_id, now
from srs.schemas import StrictModel
from srs.service import audit
from srs.transport import TransportError


class SSHHostInput(StrictModel):
    node_id: str | None = Field(default=None, max_length=64)
    revision: int | None = Field(default=None, ge=1)
    display_name: str = Field(min_length=1, max_length=128)
    hostname: str = Field(min_length=1, max_length=255)
    port: int = Field(default=22, ge=1, le=65535)
    username: str = Field(pattern=r'^[A-Za-z_][A-Za-z0-9_-]{0,63}$')
    password: SecretStr | None = Field(default=None, max_length=4096)
    enabled: bool = True

    @field_validator('hostname')
    @classmethod
    def valid_host(cls, value):
        value = value.strip()
        try:
            ipaddress.ip_address(value)
        except ValueError:
            if not re.fullmatch(r'[A-Za-z0-9](?:[A-Za-z0-9.-]{0,251}[A-Za-z0-9])?', value):
                raise ValueError('請輸入 IP 或主機名稱，不含協定、路徑或使用者名稱') from None
        return value


class TrustInput(StrictModel):
    revision: int = Field(ge=1)
    fingerprint: str = Field(pattern=r'^SHA256:[A-Za-z0-9+/]{43}=?$')


class RevisionInput(StrictModel):
    revision: int = Field(ge=1)


class HostDeleteInput(StrictModel):
    inventory_revision: int = Field(ge=1)
    revision: int | None = Field(default=None, ge=1)


def public_host(host):
    if host is None:
        return None
    return {key: getattr(host, key) for key in (
        'node_id', 'display_name', 'hostname', 'port', 'username', 'enabled', 'revision',
        'host_key_sha256', 'pending_host_key_sha256', 'status', 'error', 'profile',
        'first_seen_at', 'profile_updated_at', 'requested_at')} | {'password_configured': bool(host.password_encrypted)}


def observation_allowed(node, connection):
    if node is None or node.facts.get('deleted_at'):
        return False
    return connection.enabled if connection is not None else True


def observation_revision(node, connection):
    return [node.inventory_revision, connection.revision] if connection is not None else node.inventory_revision


def register_host_routes(app, factory, permission, settings):
    read, manage = Depends(permission('read')), Depends(permission('hosts.manage'))
    vault = CredentialVault(settings.data_dir)
    prefix = '/api/v1/ssh-hosts'

    @app.delete(prefix + '/{node_id}', status_code=204)
    def remove(node_id: str, payload: HostDeleteInput, session=manage):
        with factory.begin() as db:
            node = db.get(Node, node_id)
            if node is None or node.facts.get('deleted_at'):
                raise HTTPException(404, '找不到此主機')
            connection = db.get(SSHHost, node_id)
            if (node.inventory_revision != payload.inventory_revision
                or (connection.revision if connection else None) != payload.revision):
                raise HTTPException(409, '主機設定已變更，請重新整理後再刪除')
            changed = db.execute(update(Node).where(Node.id == node_id,
                Node.inventory_revision == payload.inventory_revision).values(
                enabled=False, inventory_revision=Node.inventory_revision + 1,
                hardware={}, snapshot=None, observed_at=None, facts={'deleted_at': now()}))
            if not changed.rowcount:
                raise HTTPException(409, '主機設定已變更，請重新整理後再刪除')
            if connection:
                removed = db.execute(delete(SSHHost).where(SSHHost.node_id == node_id,
                    SSHHost.revision == payload.revision))
                if not removed.rowcount:
                    raise HTTPException(409, '主機設定已變更，請重新整理後再刪除')
            for monitor in db.scalars(select(MonitorSession)):
                if node_id in monitor.host_ids:
                    monitor.host_ids = [identifier for identifier in monitor.host_ids if identifier != node_id]
                    monitor.inventory = {k: v for k, v in monitor.inventory.items() if k != node_id}
                    monitor.results = {k: v for k, v in monitor.results.items() if k != node_id}
                    if not monitor.host_ids:
                        monitor.expires_at = now()
            for schedule in db.scalars(select(Schedule).where(Schedule.status != 'deleted')):
                if node_id in schedule.host_ids:
                    schedule.enabled = False
                    schedule.approved_hash = schedule.next_run_at = None
                    schedule.status = 'draft'
                    schedule.revision += 1
            for execution in db.scalars(select(Execution).where(Execution.status.in_(['queued', 'running']))):
                document = execution.plan_document or {}
                if node_id in document.get('impact', {}).get('host_ids', []) or any(
                    step.get('host_id') == node_id for step in document.get('steps', [])
                ):
                    execution.stop_requested = True
            audit(db, session.username, 'ssh_host.delete', node_id)

    @app.get(prefix)
    def hosts(session=read):
        with factory() as db:
            return {'items': [public_host(h) for h in db.scalars(select(SSHHost).order_by(SSHHost.display_name))]}

    @app.post(prefix)
    def save(payload: SSHHostInput, session=manage):
        try:
            with factory.begin() as db:
                node = db.get(Node, payload.node_id) if payload.node_id else None
                if payload.node_id and (node is None or node.facts.get('deleted_at')):
                    raise HTTPException(404, '找不到指定的主機')
                if node is not None:
                    locked = db.execute(update(Node).where(Node.id == node.id,
                        Node.inventory_revision == node.inventory_revision).values(inventory_revision=Node.inventory_revision))
                    if not locked.rowcount:
                        raise HTTPException(409, '主機已變更或刪除，請重新整理')
                connection = db.get(SSHHost, node.id) if node else None
                if connection and connection.revision != payload.revision:
                    raise HTTPException(409, '主機設定已變更，請重新開啟表單')
                password = payload.password.get_secret_value() if payload.password else ''
                if not password and connection is None:
                    raise HTTPException(422, '首次設定請提供 SSH 密碼')
                encrypted = vault.encrypt(password) if password else connection.password_encrypted
                if node is None:
                    identifier = new_id()
                    node = Node(id=identifier, alias='SSH_' + identifier.replace('-', '')[:12],
                                display_name=payload.display_name, hostname=payload.hostname,
                                port=payload.port, ssh_username=payload.username, enabled=False,
                                hardware={}, facts={'source': 'web observation registration'})
                    db.add(node)
                    db.flush()
                values = {field: getattr(payload, field) for field in ('display_name', 'hostname', 'port', 'username', 'enabled')}
                values.update(password_encrypted=encrypted, status='queued' if payload.enabled else 'disabled',
                              error=None, requested_at=now() if payload.enabled else None, claim_until=now())
                if connection is None:
                    connection = SSHHost(node_id=node.id, revision=1,
                        host_key_sha256=node.host_key_sha256 if node.hostname == payload.hostname and node.port == payload.port else None, **values)
                    db.add(connection)
                else:
                    if (connection.hostname, connection.port) != (payload.hostname, payload.port):
                        values.update(host_key_sha256=None, pending_host_key_sha256=None,
                                      profile=None, first_seen_at=None, profile_updated_at=None)
                    result = db.execute(update(SSHHost).where(SSHHost.node_id == node.id,
                        SSHHost.revision == payload.revision).values(**values, revision=SSHHost.revision + 1))
                    if not result.rowcount:
                        raise HTTPException(409, '主機設定已變更，請重新開啟表單')
                    db.refresh(connection)
                db.flush()
                audit(db, session.username, 'ssh_host.save', node.id, {'revision': connection.revision})
                return public_host(connection)
        except TransportError as exc:
            raise HTTPException(503, str(exc)) from None
        except IntegrityError:
            raise HTTPException(409, '主機設定已由另一個請求建立，請重新整理') from None

    @app.post(prefix + '/{node_id}/profile')
    def refresh_profile(node_id: str, payload: RevisionInput, session=manage):
        with factory.begin() as db:
            result = db.execute(update(SSHHost).where(SSHHost.node_id == node_id,
                SSHHost.revision == payload.revision, SSHHost.enabled.is_(True)).values(
                revision=SSHHost.revision + 1, requested_at=now(), claim_until=now(), status='queued', error=None))
            if not result.rowcount:
                raise HTTPException(409, '主機已停用或設定變更，請重新整理')
            audit(db, session.username, 'ssh_host.profile', node_id)
            return public_host(db.get(SSHHost, node_id))

    @app.post(prefix + '/{node_id}/trust')
    def trust(node_id: str, payload: TrustInput, session=manage):
        with factory.begin() as db:
            result = db.execute(update(SSHHost).where(SSHHost.node_id == node_id,
                SSHHost.revision == payload.revision, SSHHost.pending_host_key_sha256 == payload.fingerprint,
                SSHHost.enabled.is_(True)).values(host_key_sha256=payload.fingerprint,
                pending_host_key_sha256=None, revision=SSHHost.revision + 1, requested_at=now(),
                claim_until=now(), status='queued', error=None))
            if not result.rowcount:
                raise HTTPException(409, '待確認的主機金鑰或設定已變更，請重新整理')
            audit(db, session.username, 'ssh_host.trust', node_id, {'fingerprint': payload.fingerprint})
            return public_host(db.get(SSHHost, node_id))
