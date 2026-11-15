"""Preview and explicitly apply per-node torchrun setup in isolated user directories."""

import asyncio
import json
import secrets
import shlex
import time
from typing import Literal

from fastapi import Depends, HTTPException
from pydantic import Field, model_validator

from srs.host_access import connect, host_config
from srs.models import new_id
from srs.schemas import StrictModel
from srs.service import audit
from srs.ssh_hosts import SSHHostInput

PROBE = """import json,sys,platform,importlib.util
r={'python':sys.version.split()[0],'system':platform.system(),'torch':None,'cuda':None,'gpus':0}
try:
 import torch
 r.update(torch=str(torch.__version__),cuda=torch.version.cuda,gpus=torch.cuda.device_count())
except Exception: pass
print(json.dumps(r))
"""
APPLY = r"""import json,sys,pathlib,subprocess,os
p=json.load(sys.stdin)
root=pathlib.Path.home()/'.local/share/srs/training'/p['directory']
root.mkdir(parents=True,exist_ok=False,mode=0o700)
python=sys.executable
if p['install_version']:
 subprocess.run([python,'-m','venv',str(root/'venv')],check=True,timeout=60)
 python=str(root/'venv/bin/python')
 subprocess.run([python,'-m','pip','--disable-pip-version-check','install','--index-url',p['index'],'torch=='+p['install_version']],check=True,timeout=540,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
check=subprocess.run([python,'-c',p['probe']],check=True,capture_output=True,text=True,timeout=45)
facts=json.loads(check.stdout)
if not facts['torch']: raise RuntimeError('PyTorch unavailable')
if p['device']=='cuda' and facts['gpus']<p['processes']: raise RuntimeError('Insufficient CUDA devices')
config=p['config']
config['python']=python
(root/'config.json').write_text(json.dumps(config,indent=2)+'\n')
(root/'launch.py').write_text(p['launcher'])
os.chmod(root/'launch.py',0o700)
print(json.dumps({'directory':str(root),'torch':facts['torch'],'cuda':facts['cuda'],'gpus':facts['gpus']}))
"""
LAUNCHER = """#!/usr/bin/env python3
import json,os,pathlib,sys
p=json.loads(pathlib.Path(__file__).with_name('config.json').read_text())
if len(sys.argv)<2: raise SystemExit('Usage: python3 launch.py /path/to/train.py [arguments]')
if p['interface']:
 os.environ['GLOO_SOCKET_IFNAME']=p['interface']
 os.environ['NCCL_SOCKET_IFNAME']=p['interface']
if p['device']=='cpu': os.environ['CUDA_VISIBLE_DEVICES']=''
args=[p['python'],'-m','torch.distributed.run','--nnodes='+str(p['nodes']),
 '--nproc-per-node='+str(p['processes']),'--node-rank='+str(p['rank']),
 '--master-addr='+p['master_addr'],'--master-port='+str(p['master_port']),*sys.argv[1:]]
os.execv(p['python'],args)
"""


class SetupInput(StrictModel):
    host_ids: list[str] = Field(min_length=1, max_length=32)
    master_addr: str = Field(min_length=1, max_length=255)
    master_port: int = Field(default=29500, ge=1024, le=65535)
    processes: int = Field(default=1, ge=1, le=128)
    device: Literal["cpu", "cuda"] = "cuda"
    python: str = Field(default="/usr/bin/python3", pattern=r"^/[A-Za-z0-9_./-]+$", max_length=512)
    interface: str = Field(default="", pattern=r"^[A-Za-z0-9_.:-]{0,64}$")
    install_version: str = Field(default="", pattern=r"^(?:[0-9]+\.[0-9]+\.[0-9]+)?$")
    wheel: Literal["cpu", "cu128", "cu130"] = "cpu"

    @model_validator(mode="after")
    def valid_setup(self):
        SSHHostInput.valid_host(self.master_addr)
        if len(set(self.host_ids)) != len(self.host_ids):
            raise ValueError("主機不可重複")
        if self.device == "cuda" and self.install_version and self.wheel == "cpu":
            raise ValueError("GPU 訓練請選 CUDA 套件來源")
        return self


class ApplyInput(StrictModel):
    token: str = Field(min_length=20, max_length=128)
    host_id: str = Field(min_length=1, max_length=64)


def register_training_routes(app, factory, permission, settings):
    manage = Depends(permission("hosts.terminal"))
    plans = {}
    limit = asyncio.Semaphore(4)

    @app.post("/api/v1/training/preview")
    async def preview(payload: SetupInput, session=manage):
        for token in list(plans):
            if plans[token]["expires"] < time.monotonic():
                plans.pop(token)
        if len(plans) >= 128:
            raise HTTPException(429, "預覽過多，請稍後重試")
        hosts = {identifier: host_config(factory, identifier) for identifier in payload.host_ids}

        async def check(identifier):
            async with limit:
                try:
                    async with asyncio.timeout(55):
                        async with await connect(hosts[identifier], settings) as conn:
                            result = await conn.run(
                                shlex.join([payload.python, "-c", PROBE]), check=True, timeout=45
                            )
                            facts = json.loads(result.stdout)
                            ready = facts.get("system") == "Linux" and (
                                bool(payload.install_version)
                                or bool(facts.get("torch"))
                                and (payload.device == "cpu" or facts.get("gpus", 0) >= payload.processes)
                            )
                            return {
                                "host_id": identifier,
                                "ready": ready,
                                "facts": facts,
                                "error": None
                                if ready
                                else "需要 Linux、PyTorch 或足夠的 CUDA GPU；可指定版本建立獨立環境。",
                            }
                except Exception:
                    return {
                        "host_id": identifier,
                        "ready": False,
                        "facts": {},
                        "error": "檢查失敗，請確認 SSH、Python 路徑及網路。",
                    }

        checks = await asyncio.gather(*(check(identifier) for identifier in payload.host_ids))
        versions = {row["facts"].get("torch") for row in checks if row["ready"]}
        if not payload.install_version and len(versions) > 1:
            for row in checks:
                row.update(ready=False, error="所選節點 PyTorch 版本不一致，請指定統一版本建立獨立環境。")
        identifier, token = new_id(), secrets.token_urlsafe(32)
        rows = []
        for rank, checked in enumerate(checks):
            config = {
                "rank": rank,
                "nodes": len(checks),
                "master_addr": payload.master_addr,
                "master_port": payload.master_port,
                "processes": payload.processes,
                "device": payload.device,
                "interface": payload.interface,
            }
            rows.append({**checked, "config": config, "directory": identifier})
        plans[token] = {
            "owner": session.token_hash,
            "expires": time.monotonic() + 600,
            "payload": payload,
            "hosts": hosts,
            "rows": rows,
            "used": set(),
            "id": identifier,
        }
        with factory.begin() as db:
            audit(db, session.username, "training.preview", identifier, {"host_ids": payload.host_ids})
        return {
            "token": token,
            "rows": rows,
            "ready": all(row["ready"] for row in rows),
            "directory": "~/.local/share/srs/training/" + identifier,
            "launcher": LAUNCHER,
            "expires_seconds": 600,
        }

    @app.post("/api/v1/training/apply")
    async def apply(payload: ApplyInput, session=manage):
        plan = plans.get(payload.token)
        if not plan or plan["owner"] != session.token_hash or plan["expires"] < time.monotonic():
            raise HTTPException(409, "預覽已到期，請重新檢查")
        if not all(row["ready"] for row in plan["rows"]):
            raise HTTPException(409, "請先處理未通過的主機")
        row = next((row for row in plan["rows"] if row["host_id"] == payload.host_id), None)
        if not row or payload.host_id in plan["used"]:
            raise HTTPException(409, "此主機不在計畫中或已套用；如需重試請重新預覽")
        # Any group connection change invalidates the preview before the next write.
        for identifier, expected in plan["hosts"].items():
            if host_config(factory, identifier, expected["revision"]) != expected:
                raise HTTPException(409, "主機身分已變更，請重新預覽")
        plan["used"].add(payload.host_id)
        setup = plan["payload"]
        with factory.begin() as db:
            audit(db, session.username, "training.apply_requested", plan["id"], {"host_id": payload.host_id})
        try:
            async with limit, asyncio.timeout(660):
                async with await connect(plan["hosts"][payload.host_id], settings) as conn:
                    result = await conn.run(
                        shlex.join([setup.python, "-c", APPLY]),
                        input=json.dumps(
                            {
                                "directory": row["directory"],
                                "install_version": setup.install_version,
                                "index": "https://download.pytorch.org/whl/" + setup.wheel,
                                "probe": PROBE,
                                "device": setup.device,
                                "processes": setup.processes,
                                "config": row["config"],
                                "launcher": LAUNCHER,
                            }
                        ),
                        timeout=650,
                    )
                    if result.exit_status:
                        raise ValueError("remote failure")
                    output = json.loads(result.stdout)
            status = "success"
        except Exception:
            output = {"error": "未確認完整完成；可能已建立部分目錄或安裝套件。請檢查後重新預覽。"}
            status = "unconfirmed"
        with factory.begin() as db:
            audit(db, session.username, "training." + status, plan["id"], {"host_id": payload.host_id})
        return {"host_id": payload.host_id, "status": status, **output}
