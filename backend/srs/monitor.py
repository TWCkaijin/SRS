"""Explicit real-only monitoring, isolated from the API and demo executor.

Only the monitor process decrypts credentials and opens SSH. Browser leases expire after 45s.
The SSH settings API accepts connection settings; no arbitrary commands or sudo are accepted.
"""
import asyncio
import fcntl
import re
import shlex
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Literal

import asyncssh
from fastapi import Depends, HTTPException
from pydantic import Field
from sqlalchemy import select, update

from srs.credential_vault import CredentialVault
from srs.host_profile import PROFILE_PROBE, HardwareProfile
from srs.models import MonitorSession, Node, SessionRecord, SSHHost, iso, new_id, now
from srs.schemas import StrictModel
from srs.security import digest
from srs.service import audit, row_dict
from srs.ssh_hosts import observation_allowed, observation_revision
from srs.transport import CredentialStore, HostCredentials, PinnedSSHClient, TransportError

INTERVAL = 2
LEASE = 45
# A fixed, read-only Linux probe: no installation, files, process arguments or sudo.
MONITOR_PROBE = r'''import csv, io, json, os, pathlib, subprocess, time

def cpu():
    return list(map(int, pathlib.Path('/proc/stat').read_text().splitlines()[0].split()[1:9]))
a = cpu()
time.sleep(0.25)
b = cpu()
total = sum(b) - sum(a)
idle = b[3] + b[4] - a[3] - a[4]
mem = {}
for line in pathlib.Path('/proc/meminfo').read_text().splitlines():
    key, value = line.split(':', 1)
    if key in ('MemTotal', 'MemAvailable'):
        mem[key] = int(value.strip().split()[0]) / 1024

def number(value):
    try:
        return float(value)
    except ValueError:
        return None

gpus, gpu_error = [], None
try:
    r = subprocess.run(['nvidia-smi', '--query-gpu=index,name,uuid,memory.total,memory.used,utilization.gpu', '--format=csv,noheader,nounits'], capture_output=True, text=True, timeout=4)
    if r.returncode:
        gpu_error = 'nvidia-smi 執行失敗'
    else:
        for fields in csv.reader(io.StringIO(r.stdout), skipinitialspace=True):
            if len(fields) != 6:
                continue
            gpus.append(dict(index=int(fields[0]), name=fields[1], uuid=fields[2], memory_total_mb=number(fields[3]), memory_used_mb=number(fields[4]), utilization_percent=number(fields[5])))
except (OSError, subprocess.TimeoutExpired):
    gpu_error = 'nvidia-smi 不可用或逾時'
# Only process names are collected: command arguments may contain credentials.
processes, process_error, users, users_error = [], None, [], None
try:
    import pwd
    r = subprocess.run(['nvidia-smi', '--query-compute-apps=gpu_uuid,pid,process_name,used_gpu_memory', '--format=csv,noheader,nounits'], capture_output=True, text=True, timeout=4)
    if r.returncode:
        process_error = 'GPU 程序查詢不可用'
    else:
        for fields in list(csv.reader(io.StringIO(r.stdout), skipinitialspace=True))[:512]:
            if len(fields) != 4 or not fields[1].isdigit(): continue
            pid, owner = int(fields[1]), None
            try: owner = pwd.getpwuid(pathlib.Path('/proc') .joinpath(str(pid)).stat().st_uid).pw_name
            except (OSError, KeyError): pass
            processes.append(dict(gpu_uuid=fields[0], pid=pid, name=pathlib.Path(fields[2]).name[:256], username=owner, memory_mb=number(fields[3])))
except (OSError, subprocess.TimeoutExpired):
    process_error = 'GPU 程序查詢不可用或逾時'
try:
    r = subprocess.run(['who'], capture_output=True, text=True, timeout=2, env={**os.environ, 'LC_ALL':'C'})
    if r.returncode: users_error = '登入工作階段查詢失敗'
    else:
        for line in r.stdout.splitlines()[:256]:
            f = line.split()
            if len(f) >= 4: users.append(dict(username=f[0][:64], terminal=f[1][:64], login_time=' '.join(f[2:4])[:64], origin=' '.join(f[4:])[:256]))
except (OSError, subprocess.TimeoutExpired):
    users_error = 'who 不可用或逾時'
storage = []
try:
    seen = set()
    for line in pathlib.Path('/proc/self/mounts').read_text().splitlines():
        fields = line.split()
        if len(fields) < 3 or not fields[0].startswith('/dev/') or fields[2] in ('squashfs', 'iso9660'):
            continue
        device, mount = fields[0], fields[1].replace(r'\040', ' ')
        if device in seen:
            continue
        seen.add(device)
        try:
            info = os.statvfs(mount)
            storage.append(dict(device=device, mountpoint=mount, filesystem=fields[2], total_bytes=info.f_blocks*info.f_frsize, used_bytes=(info.f_blocks-info.f_bfree)*info.f_frsize, available_bytes=info.f_bavail*info.f_frsize))
        except OSError:
            pass
except OSError:
    pass
network = []
try:
    for line in pathlib.Path('/proc/net/dev').read_text().splitlines()[2:]:
        name, data = line.split(':', 1)
        name = name.strip()
        if name == 'lo':
            continue
        fields = data.split()
        root = pathlib.Path('/sys/class/net') / name
        def attr(name, default=None):
            try:
                return (root / name).read_text().strip()
            except OSError:
                return default
        speed = attr('speed')
        network.append(dict(name=name, rx_bytes=int(fields[0]), tx_bytes=int(fields[8]), state=attr('operstate', 'unknown'), speed_mbps=int(speed) if speed and speed.isdigit() else None))
except OSError:
    pass
print(json.dumps(dict(cpu_percent=round(100 * (total-idle) / total, 1) if total else 0, memory_total_mb=mem['MemTotal'], memory_used_mb=mem['MemTotal']-mem['MemAvailable'], gpus=gpus, gpu_error=gpu_error, gpu_processes=processes, gpu_process_error=process_error, login_users=users, login_users_error=users_error, storage=storage[:128], network=network[:128], sample_time=time.monotonic(), uptime_seconds=float(pathlib.Path('/proc/uptime').read_text().split()[0]))))
'''


class MonitorInput(StrictModel):
    host_ids: list[str] = Field(min_length=1, max_length=32)
    interval_seconds: Literal[1, 2, 5, 10] = 2


class GenerationInput(StrictModel):
    generation: str = Field(min_length=1, max_length=36)


class GpuReading(StrictModel):
    index: int = Field(ge=0)
    name: str = Field(max_length=200)
    uuid: str = Field(max_length=100)
    memory_total_mb: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    memory_used_mb: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    utilization_percent: float | None = Field(default=None, ge=0, le=100, allow_inf_nan=False)


class StorageReading(StrictModel):
    device: str = Field(max_length=256)
    mountpoint: str = Field(max_length=1024)
    filesystem: str = Field(max_length=64)
    total_bytes: int = Field(ge=0)
    used_bytes: int = Field(ge=0)
    available_bytes: int = Field(ge=0)


class NetworkReading(StrictModel):
    name: str = Field(max_length=128)
    rx_bytes: int = Field(ge=0)
    tx_bytes: int = Field(ge=0)
    state: str = Field(max_length=32)
    speed_mbps: int | None = Field(default=None, ge=0)


class GpuProcess(StrictModel):
    gpu_uuid: str = Field(max_length=100)
    pid: int = Field(gt=0)
    name: str = Field(max_length=256)
    username: str | None = Field(default=None, max_length=64)
    memory_mb: float | None = Field(default=None, ge=0, allow_inf_nan=False)


class LoginUser(StrictModel):
    username: str = Field(max_length=64)
    terminal: str = Field(max_length=64)
    login_time: str = Field(max_length=64)
    origin: str = Field(max_length=256)


class Reading(StrictModel):
    gpu_processes: list[GpuProcess] = Field(default_factory=list, max_length=512)
    gpu_process_error: str | None = Field(default=None, max_length=200)
    login_users: list[LoginUser] = Field(default_factory=list, max_length=256)
    login_users_error: str | None = Field(default=None, max_length=200)
    cpu_percent: float = Field(ge=0, le=100, allow_inf_nan=False)
    memory_total_mb: float = Field(gt=0, allow_inf_nan=False)
    memory_used_mb: float = Field(ge=0, allow_inf_nan=False)
    gpus: list[GpuReading] = Field(max_length=128)
    storage: list[StorageReading] = Field(default_factory=list, max_length=128)
    network: list[NetworkReading] = Field(default_factory=list, max_length=128)
    sample_time: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    uptime_seconds: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    gpu_error: str | None = Field(default=None, max_length=200)


def expiry():
    return iso(datetime.now(UTC) + timedelta(seconds=LEASE))


def view(row):
    if row is None:
        return {"active": False, "host_ids": [], "results": {}, "generation": "", "interval_seconds": INTERVAL}
    return {**{k: v for k, v in row_dict(row).items() if k not in {"id", "inventory"}},
            "active": bool(row.host_ids) and row.expires_at > now(), "interval_seconds": row.interval_seconds}


def register_routes(app, factory, permission):
    prefix = "/api/v1/monitor"
    read = Depends(permission("read"))
    manage = Depends(permission("monitor.manage"))

    @app.get(prefix)
    def state(session=read):
        with factory() as db:
            return view(db.get(MonitorSession, session.token_hash))

    @app.post(prefix)
    def start(payload: MonitorInput, session=manage):
        ids = sorted(set(payload.host_ids))
        with factory.begin() as db:
            nodes = [db.get(Node, host_id) for host_id in ids]
            connections = {node.id: db.get(SSHHost, node.id) for node in nodes if node is not None}
            if any(node is None or not observation_allowed(node, connections.get(node.id)) for node in nodes):
                raise HTTPException(422, "請選擇已登錄且未停用的 SSH 主機")
            row = db.get(MonitorSession, session.token_hash)
            if row is None:
                row = MonitorSession(id=session.token_hash)
                db.add(row)
            continuing = bool(row.generation and row.expires_at > now())
            previous_results = row.results or {}
            previous_inventory = row.inventory or {}
            if not continuing:
                row.generation = new_id()
            row.host_ids = ids
            row.inventory = {node.id: observation_revision(node, connections.get(node.id)) for node in nodes}
            row.inventory = {**row.inventory, '_tokens': {
                key: previous_inventory.get('_tokens', {}).get(key, new_id())
                if continuing and previous_inventory.get(key) == row.inventory.get(key) else new_id()
                for key in ids}}
            row.interval_seconds = payload.interval_seconds
            row.results = {key: value for key, value in previous_results.items()
                           if continuing and key in ids and previous_inventory.get(key) == row.inventory.get(key)}
            row.expires_at, row.next_poll_at = expiry(), now()
            if not continuing:
                row.checked_at = None
            db.flush()
            audit(db, session.username, "monitor.start", row.generation, {"host_ids": ids, "source": "remote"})
            return view(row)

    @app.post(prefix + "/heartbeat")
    def heartbeat(payload: GenerationInput, session=manage):
        with factory.begin() as db:
            result = db.execute(update(MonitorSession).where(
                MonitorSession.id == session.token_hash, MonitorSession.generation == payload.generation,
                MonitorSession.expires_at > now()).values(expires_at=expiry()))
            if not result.rowcount:
                raise HTTPException(409, "監控已停止或到期，請重新開始")
            return view(db.get(MonitorSession, session.token_hash))

    @app.post(prefix + "/stop")
    def stop(payload: GenerationInput, session=manage):
        with factory.begin() as db:
            db.execute(update(MonitorSession).where(MonitorSession.id == session.token_hash,
                MonitorSession.generation == payload.generation).values(expires_at=now()))
            audit(db, session.username, "monitor.stop", payload.generation)
            return view(db.get(MonitorSession, session.token_hash))


class MonitorWorker:
    def __init__(self, settings, factory, credentials_file=None, *, connector=None):
        self.settings, self.factory = settings, factory
        self.credentials = CredentialStore(credentials_file)
        self.vault = CredentialVault(settings.data_dir)
        self.connector = connector or asyncssh.connect
        self.limit = asyncio.Semaphore(4)
        self.connections = {}

    def import_legacy_settings(self):
        """One-time local conversion; subsequent GUI edits never read .env.hosts again."""
        try:
            values = self.credentials.read()
        except TransportError:
            return
        with self.factory.begin() as db:
            for node in db.scalars(select(Node)):
                if node.facts.get('deleted_at'):
                    continue
                if db.get(SSHHost, node.id) is not None:
                    continue
                prefix = self.credentials.prefix(row_dict(node))
                username, password = values.get(prefix + '_USERNAME', ''), values.get(prefix + '_SSH_PASSWORD', '')
                if not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_-]{0,63}', username) or not password or password.startswith('<'):
                    continue
                locked = db.execute(update(Node).where(Node.id == node.id,
                    Node.inventory_revision == node.inventory_revision).values(inventory_revision=Node.inventory_revision))
                if not locked.rowcount:
                    continue
                db.add(SSHHost(node_id=node.id, display_name=node.alias, hostname=node.hostname,
                    port=node.port, username=username, password_encrypted=self.vault.encrypt(password),
                    host_key_sha256=node.host_key_sha256, enabled=True, status='configured'))

    @staticmethod
    def host_data(node, connection):
        host = row_dict(node)
        if connection is not None:
            host.update(hostname=connection.hostname, port=connection.port, ssh_username=connection.username,
                        host_key_sha256=connection.host_key_sha256, _connection=connection)
        return host

    async def probe(self, host):
        connection_setting = host.get('_connection')
        capture_profile = host.get('_capture_profile', False) or (connection_setting is not None and connection_setting.profile is None)
        cache_key = None
        pending_key = None
        try:
            if connection_setting is not None:
                credential = HostCredentials(connection_setting.username, self.vault.decrypt(connection_setting.password_encrypted), None)
            else:
                values = self.credentials.read()
                username = values.get(self.credentials.prefix(host) + '_USERNAME', '')
                if not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_-]{0,63}', username):
                    raise TransportError('請在「SSH 主機設定」填寫此主機的登入帳號與密碼')
                credential = self.credentials.for_host({**host, 'ssh_username': username})
                if credential.password.startswith('<'):
                    raise TransportError('SSH 密碼仍為範例值，請在「SSH 主機設定」更新')
            options = dict(port=host['port'], username=credential.username, password=credential.password,
                client_keys=[], agent_path=None, config=[], public_key_auth=False, kbdint_auth=False,
                preferred_auth=['password'], connect_timeout=6, login_timeout=6,
                keepalive_interval=15, keepalive_count_max=2)
            pin = host.get('host_key_sha256')
            if pin:
                options.update(known_hosts=[], server_host_key_algs='default', client_factory=lambda:
                    PinnedSSHClient(host['hostname'], host['port'], pin))
            else:
                known_hosts = Path.home() / '.ssh' / 'known_hosts'
                options['known_hosts'] = str(known_hosts) if known_hosts.is_file() else []
            cache_key = (host['hostname'], host['port'], credential.username, pin, digest(credential.password))
            async with asyncio.timeout(20 if capture_profile else 12):
                cached = self.connections.get(cache_key)
                conn = cached[0] if cached and not cached[0].is_closed() else None
                if conn is None:
                    conn = await self.connector(host['hostname'], **options)
                self.connections[cache_key] = (conn, asyncio.get_running_loop().time())
                script = MONITOR_PROBE + (PROFILE_PROBE if capture_profile else '')
                async with conn.create_process(shlex.join(['/usr/bin/python3', '-B', '-c', script])) as process:
                    async def limited(stream):
                        output = ''
                        while chunk := await stream.read(4096):
                            output += chunk
                            if len(output) > 262144:
                                raise ValueError('Probe output too large')
                        return output
                    stdout, _ = await asyncio.gather(limited(process.stdout), limited(process.stderr))
                    await process.wait_closed()
                    if process.exit_status != 0:
                        raise ValueError('Probe failed')
                records = stdout.splitlines()
                if len(records) != (2 if capture_profile else 1):
                    raise ValueError('Unexpected probe records')
                reading = Reading.model_validate_json(records[0]).model_dump()
                profile = HardwareProfile.model_validate_json(records[1]).model_dump() if capture_profile else None
                if profile:
                    profile['gpus'] = [GpuReading.model_validate(gpu).model_dump() for gpu in profile['gpus']]
                key = conn.get_server_host_key()
                fingerprint = key.get_fingerprint('sha256') if key else None
            collected = now()
            from srs.redaction import redact_value
            return redact_value({"status": "online", "error": None, "checked_at": collected,
                "snapshot": {**reading, "status": "online", "source": "remote", "collected_at": collected},
                "profile": profile, "host_key_sha256": fingerprint, "ssh_username": credential.username}, [credential.password])
        except TransportError as exc:
            error = str(exc)
        except asyncssh.HostKeyNotVerifiable:
            error = '首次連線或主機金鑰變更，請在 SSH 設定核對並確認指紋'
            try:
                async with asyncio.timeout(6):
                    key = await asyncssh.get_server_host_key(host['hostname'], port=host['port'], config=[])
                    pending_key = key.get_fingerprint('sha256') if key else None
            except Exception:
                pass
        except asyncssh.PermissionDenied:
            error = 'SSH 登入失敗，請在「SSH 主機設定」檢查帳號與密碼'
        except (TimeoutError, OSError):
            error = '連線逾時或主機無法連線，請確認網路／VPN'
        except Exception:
            error = '觀測未完成，請確認遠端 Python 3 與系統讀取權限'
        if cache_key in self.connections:
            connection, _ = self.connections.pop(cache_key)
            connection.close()
        return {"status": "error", "error": error, "checked_at": now(), "snapshot": None,
                "pending_host_key_sha256": pending_key}

    async def monitored_probe(self, host):
        from srs.ping import ping_host
        result, ping = await asyncio.gather(self.probe(host), ping_host(host['hostname']))
        return {**result, 'ping': {**ping, 'checked_at': now()}}

    def save_profile_result(self, host, result):
        connection = host.get('_connection')
        if connection is None:
            return
        with self.factory.begin() as db:
            current = db.get(SSHHost, connection.node_id)
            if current is None or current.revision != connection.revision or not current.enabled:
                return
            values = dict(error=result.get('error'), status='ready' if result['status'] == 'online' else 'error', requested_at=None)
            if result.get('pending_host_key_sha256'):
                values.update(status='awaiting_trust', pending_host_key_sha256=result['pending_host_key_sha256'])
            if result.get('profile'):
                values.update(profile=result['profile'], profile_updated_at=result['checked_at'],
                              first_seen_at=current.first_seen_at or result['checked_at'])
            if result['status'] == 'online' and result.get('host_key_sha256'):
                values.update(host_key_sha256=result['host_key_sha256'], pending_host_key_sha256=None)
            db.execute(update(SSHHost).where(SSHHost.node_id == connection.node_id,
                SSHHost.revision == connection.revision, SSHHost.enabled.is_(True)).values(**values))

    async def run_profiles(self):
        with self.factory() as db:
            requested = list(db.scalars(select(SSHHost).where(SSHHost.requested_at.is_not(None),
                SSHHost.enabled.is_(True), SSHHost.claim_until <= now())))
        async def capture(connection):
            async with self.limit:
                with self.factory.begin() as db:
                    claimed = db.execute(update(SSHHost).where(SSHHost.node_id == connection.node_id,
                        SSHHost.revision == connection.revision, SSHHost.enabled.is_(True),
                        SSHHost.requested_at.is_not(None), SSHHost.claim_until <= now()).values(
                        claim_until=iso(datetime.now(UTC) + timedelta(seconds=35)), status='connecting'))
                    if not claimed.rowcount:
                        return
                    host = self.host_data(db.get(Node, connection.node_id), connection)
                    host['_capture_profile'] = True
                result = await self.probe(host)
                self.save_profile_result(host, result)
        await asyncio.gather(*(capture(connection) for connection in requested))

    def valid_session(self, db, row):
        session = db.get(SessionRecord, row.id)
        revision = digest(self.settings.web_admin_username + '\x00' + self.settings.web_admin_password.get_secret_value())
        return (session is not None and session.expires_at > now() and session.auth_revision == revision
                and 'monitor.manage' in self.settings.permission_set and row.expires_at > now())

    @staticmethod
    def network_rates(reading, previous):
        current = reading.get('snapshot') or {}
        previous = (previous or {}).get('snapshot') or {}
        elapsed = (current.get('sample_time') or 0) - (previous.get('sample_time') or 0)
        old = {item['name']: item for item in previous.get('network', [])}
        for item in current.get('network', []):
            before = old.get(item['name'])
            for direction in ('rx', 'tx'):
                delta = item[direction + '_bytes'] - before[direction + '_bytes'] if before else -1
                item[direction + '_bytes_per_second'] = round(delta / elapsed, 1) if previous and elapsed > 0 and delta >= 0 else None

    async def run_once(self):
        with self.factory() as db:
            candidates = list(db.scalars(select(MonitorSession).where(
                MonitorSession.expires_at > now(), MonitorSession.next_poll_at <= now())))
        for candidate in candidates:
            started = datetime.now(UTC)
            with self.factory.begin() as db:
                row = db.get(MonitorSession, candidate.id)
                if not row or not self.valid_session(db, row):
                    continue
                generation = row.generation
                nodes = [db.get(Node, host_id) for host_id in row.host_ids]
                connections = {n.id: db.get(SSHHost, n.id) for n in nodes if n is not None}
                if any(n is None or not observation_allowed(n, connections.get(n.id))
                       or row.inventory.get(n.id) != observation_revision(n, connections.get(n.id)) for n in nodes):
                    row.expires_at = now()
                    continue
                claimed = db.execute(update(MonitorSession).where(MonitorSession.id == row.id,
                    MonitorSession.generation == generation, MonitorSession.next_poll_at <= now(),
                    MonitorSession.expires_at > now()).values(
                    next_poll_at=iso(started + timedelta(seconds=35))))
                if not claimed.rowcount:
                    continue
                host_data = [self.host_data(n, connections.get(n.id)) for n in nodes]
                interval, previous = row.interval_seconds, row.results
            async def sample(host, session_id=candidate.id, expected_generation=generation):
                async with self.limit:
                    with self.factory() as db:
                        latest = db.get(MonitorSession, session_id)
                        node, connection = db.get(Node, host['id']), db.get(SSHHost, host['id'])
                        if (not latest or latest.generation != expected_generation or not self.valid_session(db, latest)
                            or not observation_allowed(node, connection)
                            or latest.inventory.get(node.id) != observation_revision(node, connection)):
                            return host['id'], None
                    result = await self.monitored_probe(host)
                    self.save_profile_result(host, result)
                    return host['id'], result
            readings = dict(await asyncio.gather(*(sample(host) for host in host_data)))
            for identifier, result in readings.items():
                if result:
                    self.network_rates(result, previous.get(identifier))
                    result.pop('profile', None)
            with self.factory.begin() as db:
                row = db.get(MonitorSession, candidate.id)
                if row and row.generation == generation and self.valid_session(db, row):
                    # Configuration edits invalidate pending samples as well as new dispatches.
                    valid = all(row.inventory.get(n.id) == observation_revision(n, db.get(SSHHost, n.id)) for n in
                                (db.get(Node, identifier) for identifier in row.host_ids))
                    if not valid:
                        row.expires_at = now()
                        continue
                    from srs.observability import record_sample
                    for host_id, result in readings.items():
                        if result and host_id in row.host_ids:
                            record_sample(db, host_id, result, db.get(SessionRecord, row.id).username)
                    db.execute(update(MonitorSession).where(MonitorSession.id == row.id,
                        MonitorSession.generation == generation, MonitorSession.expires_at > now()).values(
                        results={k: v for k, v in readings.items() if v is not None and k in row.host_ids}, checked_at=now(),
                        next_poll_at=iso(max(datetime.now(UTC), started + timedelta(seconds=interval)))))

    async def close_connections(self):
        for connection, _ in self.connections.values():
            connection.close()
        await asyncio.gather(*(connection.wait_closed() for connection, _ in self.connections.values()))
        self.connections.clear()

    async def watch_host(self, session_id, generation, host_id, host_token=None):
        """Independent sampling clocks keep a slow/offline host from delaying its peers."""
        while True:
            started = asyncio.get_running_loop().time()
            with self.factory() as db:
                row = db.get(MonitorSession, session_id)
                node, connection = db.get(Node, host_id), db.get(SSHHost, host_id)
                if (row is None or row.generation != generation or host_id not in row.host_ids
                    or (host_token is not None and row.inventory.get('_tokens', {}).get(host_id) != host_token)
                    or not self.valid_session(db, row) or node is None
                    or not observation_allowed(node, connection)
                    or row.inventory.get(host_id) != observation_revision(node, connection)):
                    return
                host = self.host_data(node, connection)
                interval = row.interval_seconds
            async with self.limit:
                result = await self.monitored_probe(host)
            self.save_profile_result(host, result)
            result.pop('profile', None)
            with self.factory.begin() as db:
                row = db.get(MonitorSession, session_id)
                node, connection = db.get(Node, host_id), db.get(SSHHost, host_id)
                if (row is None or row.generation != generation or not self.valid_session(db, row)
                    or node is None or host_id not in row.host_ids
                    or (host_token is not None and row.inventory.get('_tokens', {}).get(host_id) != host_token)
                    or row.inventory.get(host_id) != observation_revision(node, connection)):
                    return
                self.network_rates(result, row.results.get(host_id))
                from srs.observability import record_sample
                record_sample(db, host_id, result, db.get(SessionRecord, row.id).username)
                db.execute(update(MonitorSession).where(MonitorSession.id == session_id,
                    MonitorSession.generation == generation, MonitorSession.expires_at > now()).values(
                    results={**row.results, host_id: result}, checked_at=now()))
            await asyncio.sleep(max(0.1, interval - (asyncio.get_running_loop().time() - started)))

    async def run(self):
        # One sampler per database/data directory prevents duplicate SSH polling processes.
        lock_path = self.settings.data_dir / 'monitor.lock'
        with lock_path.open('a') as lock:
            try:
                fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise SystemExit('A monitoring process is already running for this data directory') from None
            self.import_legacy_settings()
            tasks = {}
            profiles = None
            try:
                while True:
                    desired = set()
                    with self.factory.begin() as db:
                        for row in db.scalars(select(MonitorSession).where(MonitorSession.expires_at > now())):
                            if not self.valid_session(db, row):
                                continue
                            for host_id in row.host_ids:
                                node, connection = db.get(Node, host_id), db.get(SSHHost, host_id)
                                if (node is None or not observation_allowed(node, connection)
                                    or row.inventory.get(host_id) != observation_revision(node, connection)):
                                    row.expires_at = now()
                                    break
                            else:
                                desired.update((row.id, row.generation, host_id, row.inventory.get('_tokens', {}).get(host_id)) for host_id in row.host_ids)
                    for key in list(tasks):
                        if key not in desired or tasks[key].done():
                            task = tasks.pop(key)
                            task.cancel()
                            await asyncio.gather(task, return_exceptions=True)
                    for key in desired - tasks.keys():
                        tasks[key] = asyncio.create_task(self.watch_host(*key))
                    if profiles is None or profiles.done():
                        if profiles is not None:
                            await profiles
                        profiles = asyncio.create_task(self.run_profiles())
                    for key, (connection, used) in list(self.connections.items()):
                        if asyncio.get_running_loop().time() - used > 30:
                            connection.close()
                            self.connections.pop(key)
                    await asyncio.sleep(0.2)
            finally:
                pending = [*tasks.values(), *([profiles] if profiles else [])]
                for task in pending:
                    task.cancel()
                await asyncio.gather(*pending, return_exceptions=True)
                await self.close_connections()
