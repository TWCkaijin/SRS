"""Linux account mutations are bound to an expiring, single-use reviewed plan."""

import asyncio
import json
import shlex
from datetime import UTC, datetime, timedelta
from typing import Literal

from fastapi import Depends, HTTPException
from pydantic import Field, SecretStr, field_validator
from sqlalchemy import select, update

from srs import host_access
from srs.models import AccountPlan, iso, now
from srs.planner import content_hash
from srs.schemas import StrictModel
from srs.service import audit, row_dict

# Executes only the exact argv built below. No shell expansion, password on argv, or home deletion.
EXEC_SCRIPT = r"""import json,sys,pwd,subprocess,os
p=json.loads(sys.stdin.read().splitlines()[-1])
try:
 if os.geteuid()!=0: raise RuntimeError('root required')
 d=p['document']
 try:
  u=pwd.getpwnam(d['username'])
  actual={'username':u.pw_name,'uid':u.pw_uid,'gid':u.pw_gid,'name':u.pw_gecos.split(',')[0],'home':u.pw_dir,'shell':u.pw_shell}
 except KeyError: actual=None
 if actual != d['expected']: raise RuntimeError('account changed since planning')
 if actual and not 1000 <= actual['uid'] < 65534: raise RuntimeError('protected account')
 if d['action']=='delete':
  r=subprocess.run(['pgrep','-u',str(actual['uid'])],capture_output=True,timeout=10)
  if r.returncode != 1: raise RuntimeError('user has processes or process check failed')
 for argv in d['commands']:
  data=d['username']+':'+p['password']+'\n' if argv==['chpasswd'] else None
  r=subprocess.run(argv,input=data,text=True,capture_output=True,timeout=20)
  if r.returncode: raise RuntimeError('account command failed')
 print(json.dumps({'ok':True}))
except Exception:
 print(json.dumps({'ok':False}))
 sys.exit(1)
"""


class PlanInput(StrictModel):
    revision: int = Field(ge=1)
    action: Literal["create", "update", "delete", "lock", "unlock"]
    username: str = Field(pattern=r"^[a-z_][a-z0-9_-]{0,31}$")
    name: str = Field(default="", max_length=128)
    change_password: bool = False

    @field_validator("name")
    @classmethod
    def safe_name(cls, value):
        if any(ord(c) < 32 or c in ":," for c in value):
            raise ValueError("Unsupported delimiters")
        return value


class ExecuteInput(StrictModel):
    content_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    confirmation: str = Field(max_length=32)
    password: SecretStr | None = Field(default=None, min_length=1, max_length=256)
    sudo_password: SecretStr | None = Field(default=None, max_length=256)

    @field_validator("password", "sudo_password")
    @classmethod
    def safe_secret(cls, value):
        if value and any(ord(c) < 32 or c == ":" for c in value.get_secret_value()):
            raise ValueError("Unsupported delimiters")
        return value


def binding(host):
    return {key: host[key] for key in ("hostname", "port", "username", "host_key_sha256", "revision")}


def register_account_plan_routes(app, factory, permission, settings):
    manage = Depends(permission("hosts.accounts"))
    approve = Depends(permission("plan.approve"))
    prefix = "/api/v1/ssh-hosts/{node_id}/account-plans"

    @app.get(prefix)
    def plans(node_id: str, session=manage):
        with factory() as db:
            return {
                "items": [
                    row_dict(p)
                    for p in db.scalars(
                        select(AccountPlan)
                        .where(AccountPlan.host_id == node_id, AccountPlan.owner == session.username)
                        .order_by(AccountPlan.created_at.desc())
                        .limit(30)
                    )
                ]
            }

    @app.post(prefix)
    async def plan(node_id: str, payload: PlanInput, session=manage):
        host = host_access.host_config(factory, node_id, payload.revision)
        if payload.username in (host["username"], "root"):
            raise HTTPException(422, "此介面不修改 root 或目前 SSH 登入帳號")
        try:
            async with asyncio.timeout(25):
                async with await host_access.connect(host, settings) as conn:
                    result = await conn.run(
                        shlex.join(["python3", "-c", host_access.LIST_SCRIPT]), check=True, timeout=20
                    )
                    users = json.loads(result.stdout)["items"]
        except Exception:
            raise HTTPException(502, "無法讀取帳號前置狀態，請確認 SSH 後重新規劃") from None
        expected = next((u for u in users if u["username"] == payload.username), None)
        if payload.action == "create" and expected is not None:
            raise HTTPException(409, "帳號已存在，請使用編輯功能")
        if payload.action != "create" and (expected is None or not 1000 <= expected["uid"] < 65534):
            raise HTTPException(422, "找不到一般使用者，或此帳號受保護")
        username = payload.username
        commands = {
            "create": [["useradd", "-m", "-s", "/bin/bash", "-c", payload.name, "--", username]],
            "update": [["usermod", "-c", payload.name, "--", username]],
            "delete": [["userdel", "--", username]],
            "lock": [["usermod", "-L", "--", username]],
            "unlock": [["usermod", "-U", "--", username]],
        }[payload.action]
        change_password = payload.action == "create" or (
            payload.action == "update" and payload.change_password
        )
        if change_password:
            commands.append(["chpasswd"])
        document = {
            "host": binding(host),
            "action": payload.action,
            "username": username,
            "expected": expected,
            "commands": commands,
            "change_password": change_password,
            "runner_hash": content_hash({"script": EXEC_SCRIPT}),
            "runner_script": EXEC_SCRIPT,
            "impact": "刪除保留家目錄；有執行中程序時拒絕刪除。密碼鎖定不會中止工作階段或禁止 SSH 金鑰登入。",
            "failure_policy": "遇錯即停止；可能部分完成，不自動重試，請重新讀取帳號確認。",
        }
        with factory.begin() as db:
            row = AccountPlan(
                host_id=node_id,
                owner=session.username,
                document=document,
                content_hash=content_hash(document),
                expires_at=iso(datetime.now(UTC) + timedelta(minutes=10)),
            )
            db.add(row)
            db.flush()
            audit(
                db,
                session.username,
                "account.plan",
                row.id,
                {"host_id": node_id, "action": payload.action, "username": username},
            )
            return row_dict(row)

    @app.post(prefix + "/{plan_id}/execute")
    async def execute(node_id: str, plan_id: str, payload: ExecuteInput, session=manage, reviewer=approve):
        with factory.begin() as db:
            row = db.get(AccountPlan, plan_id)
            if not row or row.host_id != node_id or row.owner != session.username:
                raise HTTPException(404, "找不到帳號計畫")
            document = row.document
            if (
                row.status != "planned"
                or row.expires_at <= now()
                or payload.content_hash != row.content_hash
                or content_hash(document) != row.content_hash
                or document["runner_hash"] != content_hash({"script": EXEC_SCRIPT})
            ):
                raise HTTPException(409, "計畫已執行、過期或內容變更，請重新規劃")
            if payload.confirmation != document["username"]:
                raise HTTPException(422, "請輸入計畫中的帳號名稱確認")
            if bool(payload.password) != document["change_password"]:
                raise HTTPException(422, "密碼輸入必須符合審核計畫")
            host = host_access.host_config(factory, node_id, document["host"]["revision"])
            if binding(host) != document["host"]:
                raise HTTPException(409, "主機連線設定已變更，請重新規劃")
            claimed = db.execute(
                update(AccountPlan)
                .where(
                    AccountPlan.id == row.id, AccountPlan.status == "planned", AccountPlan.expires_at > now()
                )
                .values(status="executing")
            )
            if not claimed.rowcount:
                raise HTTPException(409, "計畫已被執行")
            audit(db, reviewer.username, "account.approve", row.id, {"content_hash": row.content_hash})
        success = False
        try:
            async with asyncio.timeout(60):
                # Verify again immediately before dispatch; execution is never automatically retried.
                current = host_access.host_config(factory, node_id, document["host"]["revision"])
                if binding(current) != document["host"]:
                    raise ValueError("host changed")
                async with await host_access.connect(current, settings) as conn:
                    command = ["python3", "-c", EXEC_SCRIPT]
                    if host["username"] != "root":
                        command = ["sudo", "-S", "-k", "-p", "", "--", *command]
                    sudo = payload.sudo_password.get_secret_value() if payload.sudo_password else ""
                    password = payload.password.get_secret_value() if payload.password else ""
                    result = await conn.run(
                        shlex.join(command),
                        input=sudo + "\n" + json.dumps({"document": document, "password": password}) + "\n",
                        timeout=55,
                    )
                    success = result.exit_status == 0 and json.loads(result.stdout).get("ok") is True
        except Exception:
            success = False
        with factory.begin() as db:
            db.execute(
                update(AccountPlan)
                .where(AccountPlan.id == plan_id)
                .values(status="succeeded" if success else "unconfirmed", finished_at=now())
            )
            audit(
                db,
                session.username,
                "account.success" if success else "account.unconfirmed",
                plan_id,
                {"host_id": node_id, "username": document["username"]},
            )
        if not success:
            raise HTTPException(502, "操作未確認完成，可能部分執行。請重新讀取帳號；此計畫不會重試。")
        return {"ok": True, "plan_id": plan_id}
