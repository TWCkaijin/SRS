"""Explicit interactive SSH access and Linux account administration."""

import asyncio
import hmac
import json
import shlex

import asyncssh
from fastapi import Depends, HTTPException, Request, WebSocket, WebSocketDisconnect
from pydantic import Field, SecretStr, field_validator

from srs.credential_vault import CredentialVault
from srs.models import Node, SSHHost
from srs.schemas import StrictModel
from srs.security import check_origin
from srs.service import audit
from srs.transport import PinnedSSHClient

LOCAL_ACCOUNT_HELPERS = """def local_records(path):
 with open(path, encoding='utf-8', errors='replace') as file:
  return {row[0]:row for line in file if len(row:=line.rstrip('\\n').split(':'))>=4 and row[2].isdigit()}
"""

LIST_SCRIPT = """import json,pwd,grp,platform
if platform.system() != 'Linux': raise SystemExit('Linux required')
""" + LOCAL_ACCOUNT_HELPERS + """local_users=local_records('/etc/passwd')
local_groups=local_records('/etc/group')
print(json.dumps({
 'items':[{'username':u.pw_name,'name':u.pw_gecos.split(',')[0],'uid':u.pw_uid,'gid':u.pw_gid,'home':u.pw_dir,'shell':u.pw_shell} for u in pwd.getpwall()],
 'local_users':list(local_users),
 'groups':[{'name':g.gr_name,'gid':g.gr_gid,'members':g.gr_mem,
            'local':g.gr_name in local_groups and int(local_groups[g.gr_name][2])==g.gr_gid}
           for g in grp.getgrall()]
}))
"""



class AccountInput(StrictModel):
    revision: int = Field(ge=1)
    action: str = Field(pattern="^(create|update)$")
    username: str = Field(pattern=r"^[a-z_][a-z0-9_-]{0,31}$")
    name: str = Field(default="", max_length=128)
    password: SecretStr | None = Field(default=None, min_length=1, max_length=256)
    sudo_password: SecretStr | None = Field(default=None, max_length=256)

    @field_validator("name", "password", "sudo_password")
    @classmethod
    def safe_text(cls, value):
        raw = value.get_secret_value() if isinstance(value, SecretStr) else value
        if raw and any(ord(c) < 32 or c in ":," for c in raw):
            raise ValueError("Unsupported control characters or delimiters")
        return value


def host_config(factory, node_id, revision=None):
    with factory() as db:
        node, host = db.get(Node, node_id), db.get(SSHHost, node_id)
        if not node or node.facts.get("deleted_at") or not host or not host.enabled:
            raise HTTPException(409, "請先設定並啟用此主機的 SSH 連線")
        if revision is not None and revision != host.revision:
            raise HTTPException(409, "SSH 設定已變更，請重新開啟視窗")
        if not host.host_key_sha256 or host.pending_host_key_sha256:
            raise HTTPException(409, "請先完成硬體連線並確認 SSH 指紋")
        return {
            k: getattr(host, k)
            for k in ("hostname", "port", "username", "password_encrypted", "host_key_sha256", "revision")
        }


async def connect(host, settings):
    return await asyncssh.connect(
        host["hostname"],
        port=host["port"],
        username=host["username"],
        password=CredentialVault(settings.data_dir).decrypt(host["password_encrypted"]),
        known_hosts=[],
        server_host_key_algs="default",
        client_keys=[],
        agent_path=None,
        client_factory=lambda: PinnedSSHClient(host["hostname"], host["port"], host["host_key_sha256"]),
        login_timeout=12,
        connect_timeout=12,
    )


def register_access_routes(app, factory, permission, settings, auth):
    prefix = "/api/v1/ssh-hosts/{node_id}"
    manage_accounts = Depends(permission("hosts.accounts"))
    read_accounts = Depends(permission("read"))

    @app.get(prefix + "/accounts")
    async def accounts(node_id: str, session=read_accounts):
        host = host_config(factory, node_id)
        try:
            async with asyncio.timeout(25):
                async with await connect(host, settings) as conn:
                    result = await conn.run(
                        shlex.join(["python3", "-c", LIST_SCRIPT]), check=True, timeout=20
                    )
                    return json.loads(result.stdout)
        except Exception:
            raise HTTPException(502, "無法讀取帳號。請確認 SSH 連線及主機上的 Linux／Python 3。") from None

    @app.post(prefix + "/accounts")
    async def save_account(
        node_id: str, payload: AccountInput, session=manage_accounts
    ):
        raise HTTPException(409, "請先產生帳號操作計畫，再審核執行")

    from srs.account_plans import register_account_plan_routes
    register_account_plan_routes(app, factory, permission, settings)

    active = set()

    @app.websocket(prefix + "/terminal")
    async def terminal(ws: WebSocket, node_id: str):
        tasks = []
        identity = None
        try:
            if not ws.headers.get("origin"):
                raise HTTPException(403, "Origin required")
            check_origin(ws, settings)
            request = Request({**ws.scope, "type": "http", "method": "GET"})
            session = auth.authenticate(request, "hosts.terminal")
            await ws.accept()
            hello = await asyncio.wait_for(ws.receive_json(), 10)
            if not hmac.compare_digest(str(hello.get("csrf", "")), session.csrf):
                raise HTTPException(403, "請重新登入後再開啟終端")
            host = host_config(factory, node_id, hello.get("revision"))
            if len(active) >= 16 or sum(x[0] == session.username for x in active) >= 4:
                raise HTTPException(429, "同時開啟的終端過多，請先關閉其他終端")
            identity = (session.username, id(ws))
            active.add(identity)
            with factory.begin() as db:
                audit(db, session.username, "terminal.open", node_id)
            async with asyncio.timeout(1800):
                async with await connect(host, settings) as conn:
                    async with conn.create_process(term_type="xterm-256color", term_size=(80, 24)) as proc:
                        await ws.send_json({"type": "ready"})

                        async def output():
                            while chunk := await proc.stdout.read(4096):
                                await ws.send_json({"type": "output", "data": chunk})

                        async def input_stream():
                            while True:
                                message = await ws.receive_text()
                                if len(message) > 32768:
                                    raise ValueError("too large")
                                data = json.loads(message)
                                if data.get("type") == "input":
                                    proc.stdin.write(str(data.get("data", ""))[:8192])
                                    await proc.stdin.drain()
                                elif data.get("type") == "resize":
                                    proc.change_terminal_size(
                                        max(20, min(300, int(data["cols"]))),
                                        max(5, min(120, int(data["rows"]))),
                                    )

                        async def guard():
                            while True:
                                await asyncio.sleep(3)
                                auth.authenticate(request, "hosts.terminal")
                                host_config(factory, node_id, host["revision"])

                        tasks = [asyncio.create_task(f()) for f in (output, input_stream, guard)]
                        done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
                        for task in done:
                            task.result()
        except WebSocketDisconnect:
            pass
        except Exception:
            try:
                await ws.send_json(
                    {
                        "type": "error",
                        "message": "終端連線已中斷或無法建立，請確認登入狀態、SSH 設定與主機指紋。",
                    }
                )
            except Exception:
                pass
        finally:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            if identity:
                active.discard(identity)
                with factory.begin() as db:
                    audit(db, identity[0], "terminal.close", node_id)
            try:
                await ws.close()
            except Exception:
                pass
