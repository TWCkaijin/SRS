"""Parse SSH connection fields without executing SSH config directives."""

import fnmatch
import getpass
import shlex
from pathlib import Path

from fastapi import Depends, HTTPException
from pydantic import Field

from srs.schemas import StrictModel
from srs.ssh_hosts import SSHHostInput


class ConfigInput(StrictModel):
    text: str = Field(max_length=262144)


def parse_config(text):
    blocks, aliases, warnings = [], [], set()
    current = {"patterns": ["*"], "values": {}}
    blocks.append(current)
    for line in text.splitlines():
        try:
            parts = shlex.split(line, comments=True)
        except ValueError:
            raise HTTPException(422, "SSH config 引號格式不完整") from None
        if not parts:
            continue
        key, *values = parts
        if "=" in key:
            key, value = key.split("=", 1)
            values.insert(0, value)
        if values and values[0] == "=":
            values.pop(0)
        key = key.lower()
        if key in ("host", "match"):
            current = {"patterns": values if key == "host" else [], "values": {}}
            blocks.append(current)
            if key == "match":
                warnings.add("Match 條件區段未匯入，請手動確認設定。")
            else:
                aliases.extend(v for v in values if not any(c in v for c in "*?!["))
        elif key == "include":
            warnings.add("Include 檔案未展開；請將所需 Host 區段貼入後預覽。")
        elif key in ("hostname", "user", "port", "proxyjump", "proxycommand") and values:
            current["values"].setdefault(key, values[0])
    items = []
    for alias in dict.fromkeys(aliases):
        values = {}
        for block in blocks:
            patterns = block["patterns"]
            matches = any(not p.startswith("!") and fnmatch.fnmatchcase(alias, p) for p in patterns)
            excluded = any(p.startswith("!") and fnmatch.fnmatchcase(alias, p[1:]) for p in patterns)
            if matches and not excluded:
                for key, value in block["values"].items():
                    values.setdefault(key, value)
        problem = None
        if any(values.get(k, "none").lower() != "none" for k in ("proxyjump", "proxycommand")):
            problem = "需要跳板機／ProxyCommand，目前不支援直接匯入連線。"
        try:
            host = SSHHostInput(
                display_name=alias,
                hostname=values.get("hostname", alias).replace("%h", alias),
                username=values.get("user", getpass.getuser()),
                port=int(values.get("port", 22)),
            )
        except (ValueError, TypeError):
            warnings.add(f"{alias[:64]}：連線欄位不完整或含不支援的變數，請手動設定。")
            continue
        items.append({**host.model_dump(exclude={"password", "revision", "node_id"}), "problem": problem})
    return {"items": items[:128], "warnings": sorted(warnings), "source": "SSH config"}


def register_import_routes(app, permission):
    manage = Depends(permission("hosts.manage"))

    @app.get("/api/v1/ssh-import/local")
    def local(session=manage):
        path = Path.home() / ".ssh" / "config"
        try:
            if path.stat().st_size > 262144:
                raise HTTPException(422, "SSH config 過大，請改貼入所需區段")
            return parse_config(path.read_text())
        except OSError:
            raise HTTPException(404, "找不到伺服器使用者的 ~/.ssh/config，可貼入或選擇 config 檔案") from None

    @app.post("/api/v1/ssh-import/preview")
    def preview(payload: ConfigInput, session=manage):
        return parse_config(payload.text)
