"""Cached telemetry and local alerts. These API reads never start SSH monitoring."""

from datetime import UTC, datetime, timedelta
from typing import Literal

from fastapi import Depends
from sqlalchemy import delete, select

from srs.models import AuditEvent, MonitorAlert, MonitorHistory, Node, SessionRecord, iso, now
from srs.service import row_dict

RETENTION_DAYS = 7


def conditions(result):
    if result.get("status") != "online":
        return {"observation": "主機觀測失敗：" + (result.get("error") or "無法取得資料")}
    snapshot = result.get("snapshot") or {}
    alerts = {}
    if snapshot.get("cpu_percent", 0) >= 95:
        alerts["cpu"] = "CPU 使用率達 95%"
    total = snapshot.get("memory_total_mb") or 0
    if total and (snapshot.get("memory_used_mb") or 0) / total >= 0.9:
        alerts["memory"] = "RAM 使用率達 90%"
    for disk in snapshot.get("storage", []):
        total = disk.get("total_bytes") or 0
        if total and disk.get("available_bytes", total) / total <= 0.1:
            alerts["disk:" + disk["mountpoint"][:240]] = f"磁碟 {disk['mountpoint']} 可用空間低於或等於 10%"
    for gpu in snapshot.get("gpus", []):
        total = gpu.get("memory_total_mb") or 0
        if total and (gpu.get("memory_used_mb") or 0) / total >= 0.95:
            alerts["vram:" + str(gpu["index"])] = f"GPU {gpu['index']} VRAM 使用率達 95%"
    return alerts


def record_sample(db, host_id, result, observer):
    stamp = result.get("checked_at") or now()
    latest = db.scalar(
        select(MonitorHistory)
        .where(MonitorHistory.host_id == host_id)
        .order_by(MonitorHistory.collected_at.desc())
        .limit(1)
    )
    active = {
        a.rule: a
        for a in db.scalars(
            select(MonitorAlert).where(MonitorAlert.host_id == host_id, MonitorAlert.resolved_at.is_(None))
        )
    }
    current = conditions(result)
    changed = False
    snapshot = result.get("snapshot") or {}

    def observed(rule):
        if result.get("status") != "online":
            return False
        if rule == "cpu":
            return snapshot.get("cpu_percent") is not None
        if rule == "memory":
            return bool(snapshot.get("memory_total_mb"))
        if rule.startswith("vram:"):
            return any(
                str(g["index"]) == rule[5:]
                and g.get("memory_total_mb")
                and g.get("memory_used_mb") is not None
                for g in snapshot.get("gpus", [])
            )
        if rule.startswith("disk:"):
            return any(
                g["mountpoint"][:240] == rule[5:] and g.get("total_bytes")
                for g in snapshot.get("storage", [])
            )
        return True

    # Missing telemetry cannot prove resource alerts resolved.
    for rule, alert in active.items():
        if rule in current:
            alert.last_seen_at = stamp
        elif observed(rule):
            alert.resolved_at = stamp
            changed = True
    for rule, message in current.items():
        if rule not in active:
            changed = True
            db.add(
                MonitorAlert(host_id=host_id, rule=rule, message=message, opened_at=stamp, last_seen_at=stamp)
            )
    cutoff = iso(datetime.now(UTC) - timedelta(seconds=30))
    if latest is None or latest.collected_at <= cutoff or changed:
        db.add(MonitorHistory(host_id=host_id, observer=observer, collected_at=stamp, result=result))
        retention = iso(datetime.now(UTC) - timedelta(days=RETENTION_DAYS))
        db.execute(delete(MonitorHistory).where(MonitorHistory.collected_at < retention))
        db.execute(delete(MonitorAlert).where(MonitorAlert.resolved_at < retention))


def register_observability_routes(app, factory, permission, auth):
    read = Depends(permission("read"))

    @app.get("/api/v1/observability")
    def overview(session=read):
        with factory() as db:
            hosts = []
            for node in db.scalars(select(Node)):
                if node.facts.get("deleted_at"):
                    continue
                latest = db.scalar(
                    select(MonitorHistory)
                    .where(MonitorHistory.host_id == node.id)
                    .order_by(MonitorHistory.collected_at.desc())
                    .limit(1)
                )
                hosts.append(
                    {"host_id": node.id, "alias": node.alias, "latest": row_dict(latest) if latest else None}
                )
            alerts = list(db.scalars(select(MonitorAlert).order_by(MonitorAlert.opened_at.desc()).limit(500)))
            sessions = [
                {"username": s.username, "expires_at": s.expires_at}
                for s in db.scalars(
                    select(SessionRecord).where(
                        SessionRecord.expires_at > now(), SessionRecord.auth_revision == auth.revision
                    )
                )
            ]
            events = list(
                db.scalars(
                    select(AuditEvent)
                    .where(
                        AuditEvent.action.in_(
                            [
                                "session.login",
                                "session.logout",
                                "monitor.start",
                                "monitor.stop",
                                "account.plan",
                                "account.approve",
                                "account.success",
                                "account.unconfirmed",
                            ]
                        )
                    )
                    .order_by(AuditEvent.id.desc())
                    .limit(100)
                )
            )
            return {
                "hosts": hosts,
                "alerts": [row_dict(a) for a in alerts],
                "web_sessions": sessions,
                "events": [row_dict(e) for e in events],
                "retention_days": RETENTION_DAYS,
                "sample_interval_seconds": 30,
                "server_time": now(),
            }

    @app.get("/api/v1/monitor/history/{host_id}")
    def history(host_id: str, hours: Literal["1", "24", "168"] = "24", session=read):
        hours = int(hours)
        cutoff = iso(datetime.now(UTC) - timedelta(hours=hours))
        with factory() as db:
            rows = list(
                db.scalars(
                    select(MonitorHistory)
                    .where(MonitorHistory.host_id == host_id, MonitorHistory.collected_at >= cutoff)
                    .order_by(MonitorHistory.collected_at.desc())
                    .limit(25000)
                )
            )
            rows.reverse()
            # Keep extrema in each bucket so brief saturation is visible in the chart.
            stride = max(1, (len(rows) + 479) // 480)
            points = []
            for start in range(0, len(rows), stride):
                bucket = rows[start : start + stride]
                item = row_dict(bucket[-1])
                snapshots = [(r.result.get("snapshot") or {}) for r in bucket]
                item["cpu_peak"] = max((s.get("cpu_percent") or 0 for s in snapshots), default=0)
                item["memory_peak"] = max(
                    (
                        (s.get("memory_used_mb") or 0) / s["memory_total_mb"] * 100
                        for s in snapshots
                        if s.get("memory_total_mb")
                    ),
                    default=0,
                )
                item["sample_count"] = len(bucket)
                points.append(item)
            return {
                "items": points,
                "sample_count": len(rows),
                "hours": hours,
                "retention_days": RETENTION_DAYS,
                "aggregation": "bucket peak; latest snapshot per bucket",
            }
