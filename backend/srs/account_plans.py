"""Linux account mutations are bound to an expiring, single-use reviewed plan."""

import asyncio
import json
import shlex
from datetime import UTC, datetime, timedelta
from typing import Annotated, Literal

from fastapi import Depends, HTTPException
from pydantic import Field, SecretStr, field_validator
from sqlalchemy import select, update

from srs import host_access
from srs.models import AccountPlan, iso, now
from srs.planner import content_hash
from srs.schemas import StrictModel
from srs.service import audit, row_dict

# Executes only the exact argv built below. No shell expansion, password on argv, or home deletion.
EXEC_SCRIPT = r"""import json,sys,pwd,grp,subprocess,os,fcntl
""" + host_access.LOCAL_ACCOUNT_HELPERS + r"""
def lookup(fn, value):
 try: return fn(value)
 except KeyError: return None

def execute(d, password):
 username=d['username']
 u=lookup(pwd.getpwnam,username)
 actual={'username':u.pw_name,'uid':u.pw_uid,'gid':u.pw_gid,'name':u.pw_gecos.split(',')[0],'home':u.pw_dir,'shell':u.pw_shell} if u else None
 if actual != d['expected']: raise RuntimeError('account changed since planning')
 if actual and not 1000 <= actual['uid'] < 65534: raise RuntimeError('protected account')
 if identity:=d.get('identity'):
  number=identity['uid']
  if lookup(pwd.getpwuid,number) or lookup(grp.getgrgid,number) or lookup(grp.getgrnam,username) or os.path.lexists(identity['home']):
   raise RuntimeError('UID, GID, private group or home already exists')
 if expected_group:=d.get('expected_group'):
  group=lookup(grp.getgrnam,expected_group['name'])
  local_group=local_records('/etc/group').get(expected_group['name'])
  local_user=local_records('/etc/passwd').get(username)
  if (not local_user or int(local_user[2])!=u.pw_uid or not group or not local_group
      or group.gr_gid != expected_group['gid'] or int(local_group[2])!=group.gr_gid
      or group.gr_gid in (0,u.pw_gid)
      or (username in group.gr_mem) != expected_group['member']
      or (username in local_group[3].split(',')) != expected_group['member']):
   raise RuntimeError('account or group membership changed since planning')
 if d['action']=='delete':
  r=subprocess.run(['pgrep','-u',str(actual['uid'])],capture_output=True,timeout=10)
  if r.returncode != 1: raise RuntimeError('user has processes or process check failed')
 for argv in d['commands']:
  data=username+':'+password+'\n' if argv==['chpasswd'] else None
  r=subprocess.run(argv,input=data,text=True,capture_output=True,timeout=20)
  if r.returncode: raise RuntimeError('account command failed')
 if identity:
  created=pwd.getpwnam(username)
  if (created.pw_uid,created.pw_gid)!=(identity['uid'],identity['gid']):
   raise RuntimeError('unexpected account identity')
 if expected_group:
  members=grp.getgrnam(expected_group['name']).gr_mem
  if (username in members) != (d['membership']=='add'):
   raise RuntimeError('unexpected group membership')

try:
 if os.geteuid()!=0: raise RuntimeError('root required')
 p=json.loads(sys.stdin.read().splitlines()[-1])
 with open('/run/srs-account-plans.lock','a') as lock:
  fcntl.flock(lock,fcntl.LOCK_EX)
  execute(p['document'],p['password'])
 print(json.dumps({'ok':True}))
except Exception:
 print(json.dumps({'ok':False}))
 sys.exit(1)
"""



AccountName = Annotated[str, Field(pattern=r"^[a-z_][a-z0-9_-]{0,31}$")]
ADMIN_GROUPS = {"sudo", "wheel"}


class AccountIdentityInput(StrictModel):
    username: AccountName
    name: str = Field(default="", max_length=128)

    @field_validator("name")
    @classmethod
    def safe_name(cls, value):
        if any(ord(c) < 32 or c in ":," for c in value):
            raise ValueError("Unsupported delimiters")
        return value


class PlanInput(AccountIdentityInput):
    revision: int = Field(ge=1)
    action: Literal["create", "update", "delete", "lock", "unlock", "groups", "permissions"]
    change_password: bool = False
    group: AccountName | None = None
    membership: Literal["add", "remove"] | None = None


class BatchCreateInput(AccountIdentityInput):
    hosts: dict[Annotated[str, Field(min_length=1, max_length=64)], Annotated[int, Field(ge=1)]] = Field(
        min_length=1, max_length=32
    )
    uid: int | None = Field(default=None, ge=1000, le=59999)


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


def user_guard(host, username):
    if username in (host["username"], "root"):
        raise HTTPException(422, "此介面不修改 root 或目前 SSH 登入帳號")


async def read_facts(host, settings):
    try:
        async with asyncio.timeout(25):
            async with await host_access.connect(host, settings) as conn:
                result = await conn.run(
                    shlex.join(["python3", "-c", host_access.LIST_SCRIPT]), check=True, timeout=20
                )
                return json.loads(result.stdout)
    except Exception:
        raise HTTPException(502, "無法讀取帳號前置狀態，請確認 SSH 後重新規劃") from None


def create_document(host, username, action, expected, commands, change_password, impact, **fields):
    if change_password:
        commands.append(["chpasswd"])
    return {
        "host": binding(host),
        "action": action,
        "username": username,
        "expected": expected,
        "commands": commands,
        "change_password": change_password,
        "runner_hash": content_hash({"script": EXEC_SCRIPT}),
        "runner_script": EXEC_SCRIPT,
        "impact": impact,
        "failure_policy": "遇錯即停止；可能部分完成，不自動重試，請重新讀取帳號確認。",
        **fields,
    }


def save_plan(db, node_id, username, document):
    row = AccountPlan(
        host_id=node_id, owner=username, document=document, content_hash=content_hash(document),
        expires_at=iso(datetime.now(UTC) + timedelta(minutes=10)),
    )
    db.add(row)
    db.flush()
    audit(db, username, "account.plan", row.id, {
        "host_id": node_id, "action": document["action"], "username": document["username"],
    })
    return row_dict(row)


def membership_document(host, payload, expected, facts):
    if not payload.group or not payload.membership:
        raise HTTPException(422, "請選擇群組及加入或移除")
    admin_group = payload.group in ADMIN_GROUPS
    if admin_group != (payload.action == "permissions"):
        raise HTTPException(422, "請使用符合群組類型的管理入口")
    if payload.username not in facts.get("local_users", []):
        raise HTTPException(422, "僅支援本機 Linux 帳號的群組變更")
    group = next((g for g in facts.get("groups", []) if g["name"] == payload.group), None)
    if not group or not group["local"] or group["gid"] in (0, expected["gid"]):
        raise HTTPException(422, "此群組不是可修改的本機附加群組")
    member = payload.username in group["members"]
    if member == (payload.membership == "add"):
        raise HTTPException(409, "目前已是所選狀態，請重新載入群組")
    command = "加入" if payload.membership == "add" else "移除"
    impact = f"將 {payload.username} {command}群組 {payload.group}。新登入的工作階段會取得更新後的群組。"
    if admin_group:
        impact += "此為管理員群組，實際 sudo 權限取決於主機的 sudoers 設定。"
    return create_document(
        host, payload.username, payload.action, expected,
        [["gpasswd", "-a" if payload.membership == "add" else "-d", payload.username, "--", payload.group]],
        False, impact,
        expected_group={"name": group["name"], "gid": group["gid"], "member": member},
        membership=payload.membership,
    )


def register_account_plan_routes(app, factory, permission, settings):
    manage = Depends(permission("hosts.accounts"))
    approve = Depends(permission("plan.approve"))
    prefix = "/api/v1/ssh-hosts/{node_id}/account-plans"

    @app.post("/api/v1/accounts/batch-create/plan")
    async def batch_plan(payload: BatchCreateInput, session=manage):
        semaphore = asyncio.Semaphore(4)

        async def inspect_target(node_id, revision):
            try:
                host = host_access.host_config(factory, node_id, revision)
                user_guard(host, payload.username)
                async with semaphore:
                    facts = await read_facts(host, settings)
                if "groups" not in facts:
                    raise HTTPException(502, "無法確認主機群組清單")
                if any(user["username"] == payload.username for user in facts["items"]):
                    raise HTTPException(409, "帳號已存在")
                if any(group["name"] == payload.username for group in facts["groups"]):
                    raise HTTPException(409, "已有同名群組")
                occupied = {u[key] for u in facts["items"] for key in ("uid", "gid")}
                occupied.update(g["gid"] for g in facts["groups"])
                return host, occupied
            except HTTPException as error:
                return error

        checks = await asyncio.gather(*(inspect_target(node, revision) for node, revision in payload.hosts.items()))
        failures = [
            f"{node}：{check.detail}" for node, check in zip(payload.hosts, checks, strict=True)
            if isinstance(check, HTTPException)
        ]
        if failures:
            raise HTTPException(409, "無法建立批次計畫。 " + "；".join(failures))
        occupied = set().union(*(entry[1] for entry in checks))
        number = payload.uid if payload.uid is not None else next(
            (candidate for candidate in range(1000, 60000) if candidate not in occupied), None
        )
        if number is None or number in occupied:
            conflict = [node for node, (_, ids) in zip(payload.hosts, checks, strict=True) if number in ids]
            suffix = "：" + "、".join(conflict) if conflict else ""
            raise HTTPException(409, "指定的 UID／GID 已被使用，或找不到共同可用的編號" + suffix)
        username = payload.username
        home = f"/home/{username}"
        identity = {"uid": number, "gid": number, "home": home}
        items = []
        with factory.begin() as db:
            for node, (host, _) in zip(payload.hosts, checks, strict=True):
                commands = [
                    ["groupadd", "-g", str(number), "--", username],
                    ["useradd", "-m", "-d", home, "-s", "/bin/bash", "-c", payload.name,
                     "-u", str(number), "-g", str(number), "-N", "--", username],
                ]
                document = create_document(
                    host, username, "create", None, commands, True,
                    f"建立 {username} 與同名私人群組，UID 和 GID 均為 {number}，並建立家目錄 {home}。",
                    identity=identity,
                )
                items.append(save_plan(db, node, session.username, document))
        return {"uid": number, "gid": number, "items": items}

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
        user_guard(host, payload.username)
        facts = await read_facts(host, settings)
        expected = next((user for user in facts["items"] if user["username"] == payload.username), None)
        if payload.action == "create" and expected is not None:
            raise HTTPException(409, "帳號已存在，請使用編輯功能")
        if payload.action != "create" and (expected is None or not 1000 <= expected["uid"] < 65534):
            raise HTTPException(422, "找不到一般使用者，或此帳號受保護")
        username = payload.username
        if payload.action in ("groups", "permissions"):
            document = membership_document(host, payload, expected, facts)
        else:
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
            document = create_document(
                host, username, payload.action, expected, commands, change_password,
                "刪除保留家目錄；有執行中程序時拒絕刪除。密碼鎖定不會中止工作階段或禁止 SSH 金鑰登入。",
            )
        with factory.begin() as db:
            return save_plan(db, node_id, session.username, document)

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
