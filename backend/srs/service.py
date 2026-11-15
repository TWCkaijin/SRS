"""Transactional authorization helpers shared by the API and executor."""
import copy
from datetime import UTC, datetime

from fastapi import HTTPException
from sqlalchemy import select

from srs.models import AuditEvent, Draft, Execution, ExecutionStep, Plan, Schedule, now, parse_time
from srs.planner import TEMPLATE_VERSION, content_hash, validate_inventory

TERMINAL = {"succeeded", "failed", "cancelled", "expired", "invalidated", "unknown"}


def schedule_matches(schedule):
    if not schedule.plan_document:
        return False
    approved = schedule.plan_document.get("schedule", {})
    return all(approved.get(key) == getattr(schedule, key) for key in (
        "id", "revision", "operation", "interval_seconds", "starts_at", "expires_at", "scope"
    )) and schedule.host_ids == schedule.plan_document.get("impact", {}).get("host_ids")


def audit(db, actor, action, subject_id, details=None):
    db.add(AuditEvent(actor=actor, action=action, subject_id=subject_id, details=details or {}))


def owned(db, model, identifier, username, *, lock=False):
    statement = select(model).where(model.id == identifier, model.owner == username)
    if lock:
        statement = statement.with_for_update()
    result = db.scalar(statement)
    if result is None or getattr(result, "status", None) == "deleted":
        raise HTTPException(404, "找不到此筆資料")
    return result


def row_dict(row):
    return {c.key: getattr(row, c.key) for c in row.__table__.columns}


def execution_dict(db, execution):
    result = row_dict(execution)
    # The canonical plan remains available from its separate review endpoint.
    result.pop("plan_document", None)
    result.pop("worker_id", None)
    result.pop("lease_until", None)
    result["steps"] = [row_dict(step) for step in db.scalars(
        select(ExecutionStep).where(ExecutionStep.execution_id == execution.id).order_by(ExecutionStep.step_id)
    )]
    unresolved = {step["step_id"] for step in result["steps"] if step["status"] in {"running", "unknown"}}
    result["reconciliation_supported"] = execution.authorization_kind == "one_shot" and not any(
        step["id"] in unresolved and step["kind"] == "ssh" and step.get("read_only")
        for step in execution.plan_document.get("steps", [])
    )
    return result


def authorization_errors(db, execution, settings, *, started=False):
    doc = execution.plan_document
    errors = []
    if execution.mode != settings.mode or doc.get("mode") != settings.mode:
        errors.append("執行環境已變更")
    if content_hash(doc) != execution.approved_hash:
        errors.append("核准內容摘要不一致")
    if doc.get("source", {}).get("template_version") != TEMPLATE_VERSION:
        errors.append("範本版本已變更")
    errors += validate_inventory(db, doc)
    if execution.authorization_kind == "one_shot":
        plan = db.get(Plan, execution.plan_id)
        if not plan or plan.content_hash != execution.approved_hash or content_hash(plan.document) != execution.approved_hash:
            errors.append("核准計畫不存在或已被修改")
        elif plan.status not in {"approved", "running", "unknown"}:
            errors.append("計畫不再具有執行授權")
        elif not started and (not plan.approval_expires_at or parse_time(plan.approval_expires_at) <= datetime.now(UTC)):
            errors.append("核准已逾期")
        if plan and plan.draft_id:
            draft = db.get(Draft, plan.draft_id)
            if not draft or draft.revision != plan.revision:
                errors.append("草稿版本已變更")
    elif execution.authorization_kind == "schedule":
        schedule = db.get(Schedule, execution.schedule_id)
        if not schedule or not schedule.enabled or schedule.status != "approved":
            errors.append("排程已停止或撤銷")
        elif (schedule.approved_hash != execution.approved_hash or content_hash(schedule.plan_document) != execution.approved_hash
              or not schedule_matches(schedule)):
            errors.append("排程版本或內容已變更")
        elif parse_time(schedule.expires_at) <= datetime.now(UTC):
            errors.append("排程已到期")
        if any(not s.get("read_only") for s in doc.get("steps", [])):
            errors.append("排程包含非唯讀操作")
    else:
        errors.append("未知授權來源")
    return errors


def create_execution(db, *, document, owner, approved_hash, trigger_key, plan_id=None, schedule_id=None):
    execution = Execution(
        plan_id=plan_id, schedule_id=schedule_id, trigger_key=trigger_key,
        authorization_kind="schedule" if schedule_id else "one_shot",
        approved_hash=approved_hash, plan_document=copy.deepcopy(document), owner=owner,
        mode=document["mode"], status="queued",
    )
    db.add(execution)
    db.flush()
    for step in document["steps"]:
        db.add(ExecutionStep(execution_id=execution.id, step_id=step["id"],
                             title=step["title"], host_id=step["host_id"]))
    db.flush()
    return execution


def invalidate_draft_plans(db, draft):
    for plan in db.scalars(select(Plan).where(Plan.draft_id == draft.id, Plan.status.in_(["awaiting_approval", "approved"]))):
        plan.status = "invalidated"
        plan.reason = "草稿已修改，必須重新產生計畫"
        for execution in db.scalars(select(Execution).where(Execution.plan_id == plan.id, Execution.status == "queued")):
            execution.status = "invalidated"
            execution.error = plan.reason
            execution.finished_at = now()
