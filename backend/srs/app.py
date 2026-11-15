"""Web API: cached reads, plan construction, and encrypted SSH settings; never opens SSH."""
import asyncio
import hashlib
import json
from datetime import UTC, datetime, timedelta

from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import delete, select, update
from sqlalchemy.exc import IntegrityError

from srs.config import ROOT, Settings
from srs.database import initialize
from srs.models import (
    Artifact,
    AuditEvent,
    Draft,
    Execution,
    Job,
    LogRecord,
    Node,
    Plan,
    Project,
    Schedule,
    SessionRecord,
    SSHHost,
    iso,
    now,
    parse_time,
)
from srs.planner import (
    build_plan,
    build_schedule_document,
    choose_nodes,
    content_hash,
    validate_inventory,
    validate_project,
    validate_public_parameters,
)
from srs.schemas import ApprovalInput, DraftInput, LoginInput, ProjectInput, RejectionInput, ScheduleInput
from srs.security import COOKIE, Auth, check_origin, digest
from srs.seed import seed
from srs.service import audit, create_execution, execution_dict, invalidate_draft_plans, owned, row_dict
from srs.ssh_hosts import public_host, register_host_routes


def create_app(settings=None, *, initialize_schema=None):
    settings = settings or Settings()
    settings.validate_runtime()
    # Real modes require the explicit Alembic migration command.
    create_schema = settings.mode == "demo" if initialize_schema is None else initialize_schema
    engine, factory = initialize(settings, create_schema=create_schema)
    with factory() as db:
        seed(db, settings)
    auth = Auth(settings, factory)
    app = FastAPI(title="研究運算操作台", version="0.1.0", docs_url="/api/docs", redoc_url=None)
    app.state.settings, app.state.factory, app.state.engine = settings, factory, engine

    @app.exception_handler(RequestValidationError)
    async def validation_error(request, exc):
        # Validation errors must never echo submitted password fields or other input values.
        fields = [".".join(str(part) for part in error["loc"]) for error in exc.errors()]
        return JSONResponse({"detail": "欄位格式不正確：" + ", ".join(fields)}, status_code=422)

    @app.middleware("http")
    async def origin_and_size(request, call_next):
        try:
            if request.method not in {"GET", "HEAD", "OPTIONS"}:
                check_origin(request, settings)
                length = request.headers.get("content-length", "0")
                if not length.isdigit() or int(length) > settings.request_max_bytes:
                    return JSONResponse({"detail": "請求超過大小限制"}, status_code=413)
                body = await request.body()
                if len(body) > settings.request_max_bytes:
                    return JSONResponse({"detail": "請求超過大小限制"}, status_code=413)
            response = await call_next(request)
            response.headers["X-Content-Type-Options"] = "nosniff"
            response.headers["Referrer-Policy"] = "same-origin"
            response.headers["Cache-Control"] = "no-store"
            response.headers["X-Frame-Options"] = "DENY"
            return response
        except HTTPException as exc:
            return JSONResponse({"detail": exc.detail}, status_code=exc.status_code)

    def permission(name="read"):
        def dependency(request: Request):
            return auth.authenticate(request, name)
        return dependency

    read = permission()
    from srs.monitor import register_routes
    register_routes(app, factory, permission)
    register_host_routes(app, factory, permission, settings)
    from srs.ssh_import import register_import_routes
    register_import_routes(app, permission)
    from srs.training_setup import register_training_routes
    register_training_routes(app, factory, permission, settings)
    from srs.slurm_setup import register_slurm_routes
    register_slurm_routes(app, factory, permission, settings)
    from srs.host_access import register_access_routes
    register_access_routes(app, factory, permission, settings, auth)
    from srs.observability import register_observability_routes
    register_observability_routes(app, factory, permission, auth)
    prefix = "/api/v1"

    @app.get(prefix + "/health")
    def health():
        return {"status": "ok", "mode": settings.mode, "version": "0.1.0", "configured": settings.configured}

    @app.post(prefix + "/session")
    def login(payload: LoginInput, request: Request, response: Response):
        token, session = auth.login(payload.username, payload.password, request.client.host if request.client else "local")
        response.set_cookie(COOKIE, token, httponly=True, secure=settings.cookie_secure, samesite="strict",
                            max_age=settings.session_hours * 3600, path="/")
        return {"username": session.username, "csrf_token": session.csrf,
                "permissions": sorted(settings.permission_set), "mode": settings.mode}

    @app.get(prefix + "/session")
    def session_info(session=Depends(read)):
        return {"username": session.username, "csrf_token": session.csrf,
                "permissions": sorted(settings.permission_set), "mode": settings.mode}

    @app.delete(prefix + "/session")
    def logout(request: Request, response: Response, session=Depends(read)):
        with factory.begin() as db:
            audit(db, session.username, "session.logout", session.username)
            db.execute(delete(SessionRecord).where(SessionRecord.token_hash == digest(request.cookies[COOKIE])))
        response.delete_cookie(COOKIE, path="/")
        return {"status": "signed_out"}

    @app.get(prefix + "/nodes")
    def nodes(session=Depends(read)):
        with factory() as db:
            return {"items": [{**{key: value for key, value in row_dict(n).items() if key != "facts"},
                               "ssh_connection": public_host(db.get(SSHHost, n.id))}
                              for n in db.scalars(select(Node).order_by(Node.id)) if not n.facts.get("deleted_at")]}

    @app.get(prefix + "/projects")
    def projects(session=Depends(read)):
        with factory() as db:
            return {"items": [row_dict(p) for p in db.scalars(select(Project).where(Project.owner == session.username))]}

    @app.post(prefix + "/projects", status_code=201)
    def project_create(payload: ProjectInput, session=Depends(permission("project.manage"))):
        validate_project(payload, settings)
        with factory.begin() as db:
            project = Project(**payload.model_dump(), owner=session.username)
            db.add(project)
            db.flush()
            audit(db, session.username, "project.register", project.id)
            return row_dict(project)

    @app.get(prefix + "/drafts")
    def drafts(session=Depends(read)):
        with factory() as db:
            return {"items": [row_dict(d) for d in db.scalars(select(Draft).where(Draft.owner == session.username).order_by(Draft.created_at.desc()))]}

    @app.post(prefix + "/drafts", status_code=201)
    def draft_create(payload: DraftInput, session=Depends(permission("draft.manage"))):
        validate_public_parameters(payload.parameters)
        with factory.begin() as db:
            choose_nodes(db, payload.host_ids)
            draft = Draft(**payload.model_dump(), owner=session.username)
            db.add(draft)
            db.flush()
            audit(db, session.username, "draft.create", draft.id)
            return row_dict(draft)

    @app.get(prefix + "/drafts/{draft_id}")
    def draft_get(draft_id: str, session=Depends(read)):
        with factory() as db:
            return row_dict(owned(db, Draft, draft_id, session.username))

    @app.patch(prefix + "/drafts/{draft_id}")
    def draft_update(draft_id: str, payload: DraftInput, session=Depends(permission("draft.manage"))):
        validate_public_parameters(payload.parameters)
        with factory.begin() as db:
            draft = owned(db, Draft, draft_id, session.username, lock=True)
            choose_nodes(db, payload.host_ids)
            if db.scalar(select(Execution.id).join(Plan, Execution.plan_id == Plan.id).where(
                Plan.draft_id == draft.id, Execution.status.in_(["running", "unknown"])
            )):
                raise HTTPException(409, "此草稿仍有執行中或結果待確認的操作；請先完成對帳或建立新草稿")
            invalidate_draft_plans(db, draft)
            for key, value in payload.model_dump().items():
                setattr(draft, key, value)
            draft.revision += 1
            draft.updated_at = now()
            audit(db, session.username, "draft.update", draft.id, {"revision": draft.revision})
            db.flush()
            return row_dict(draft)

    @app.post(prefix + "/drafts/{draft_id}/plan", status_code=201)
    def plan_create(draft_id: str, session=Depends(permission("draft.manage"))):
        with factory.begin() as db:
            draft = owned(db, Draft, draft_id, session.username, lock=True)
            existing = db.scalar(select(Plan).where(Plan.draft_id == draft.id, Plan.revision == draft.revision,
                                                   Plan.status.in_(["awaiting_approval", "approved", "running", "unknown"])))
            if existing:
                return row_dict(existing)
            plan = build_plan(db, draft, settings)
            db.add(plan)
            db.flush()
            audit(db, session.username, "plan.generate", plan.id, {"hash": plan.content_hash})
            return row_dict(plan)

    @app.get(prefix + "/plans")
    def plans(session=Depends(read)):
        with factory() as db:
            return {"items": [row_dict(p) for p in db.scalars(select(Plan).where(Plan.owner == session.username).order_by(Plan.created_at.desc()))]}

    @app.get(prefix + "/plans/{plan_id}")
    def plan_get(plan_id: str, session=Depends(read)):
        with factory() as db:
            return row_dict(owned(db, Plan, plan_id, session.username))

    @app.post(prefix + "/plans/{plan_id}/approve")
    def plan_approve(plan_id: str, payload: ApprovalInput, session=Depends(permission("plan.approve"))):
        try:
            with factory.begin() as db:
                plan = owned(db, Plan, plan_id, session.username, lock=True)
                if payload.content_hash != plan.content_hash or content_hash(plan.document) != plan.content_hash:
                    raise HTTPException(409, "計畫摘要不符，請重新審閱")
                previous = db.scalar(select(Execution).where(Execution.trigger_key == "plan:" + plan.id))
                if previous:
                    return {"plan": row_dict(plan), "execution": execution_dict(db, previous)}
                if plan.status != "awaiting_approval":
                    raise HTTPException(409, "此計畫無法核准")
                draft = db.get(Draft, plan.draft_id)
                problems = list(plan.issues) + validate_inventory(db, plan.document)
                if not draft or draft.revision != plan.revision:
                    problems.append("草稿版本已變更")
                if plan.document["mode"] != settings.mode:
                    problems.append("執行環境已變更")
                if problems:
                    raise HTTPException(409, "；".join(problems))
                plan.status, plan.approved_by, plan.approved_at = "approved", session.username, now()
                plan.approval_expires_at = iso(datetime.now(UTC) + timedelta(minutes=settings.approval_minutes))
                execution = create_execution(db, document=plan.document, owner=session.username,
                                             approved_hash=plan.content_hash, trigger_key="plan:" + plan.id, plan_id=plan.id)
                audit(db, session.username, "plan.approve", plan.id, {"hash": plan.content_hash, "execution_id": execution.id})
                return {"plan": row_dict(plan), "execution": execution_dict(db, execution)}
        except IntegrityError:
            with factory() as db:
                plan = owned(db, Plan, plan_id, session.username)
                execution = db.scalar(select(Execution).where(Execution.trigger_key == "plan:" + plan.id))
                if not execution:
                    raise HTTPException(409, "計畫正在更新，請重新讀取") from None
                return {"plan": row_dict(plan), "execution": execution_dict(db, execution)}

    @app.post(prefix + "/plans/{plan_id}/reject")
    def plan_reject(plan_id: str, payload: RejectionInput, session=Depends(permission("plan.approve"))):
        with factory.begin() as db:
            plan = owned(db, Plan, plan_id, session.username, lock=True)
            if plan.status != "awaiting_approval":
                raise HTTPException(409, "只能拒絕尚未核准的計畫")
            plan.status, plan.reason = "cancelled", payload.reason
            audit(db, session.username, "plan.reject", plan.id)
            return row_dict(plan)

    @app.get(prefix + "/executions")
    def executions(session=Depends(read)):
        with factory() as db:
            return {"items": [execution_dict(db, e) for e in db.scalars(select(Execution).where(Execution.owner == session.username).order_by(Execution.created_at.desc()))]}

    @app.get(prefix + "/executions/{execution_id}")
    def execution_get(execution_id: str, session=Depends(read)):
        with factory() as db:
            return execution_dict(db, owned(db, Execution, execution_id, session.username))

    @app.post(prefix + "/executions/{execution_id}/stop")
    def execution_stop(execution_id: str, session=Depends(permission("execution.stop"))):
        with factory.begin() as db:
            execution = owned(db, Execution, execution_id, session.username, lock=True)
            execution.stop_requested = True
            if execution.status == "queued":
                execution.status, execution.finished_at = "cancelled", now()
                if execution.plan_id:
                    db.get(Plan, execution.plan_id).status = "cancelled"
            audit(db, session.username, "execution.stop_dispatch", execution.id)
            return execution_dict(db, execution)

    @app.get(prefix + "/executions/{execution_id}/logs")
    def execution_logs(execution_id: str, after: int = 0, limit: int = 200, stream: str | None = None, session=Depends(read)):
        if after < 0 or not 1 <= limit <= 1000 or stream not in {None, "stdout", "stderr", "audit"}:
            raise HTTPException(422, "不正確的日誌篩選條件")
        with factory() as db:
            owned(db, Execution, execution_id, session.username)
            query = select(LogRecord).where(LogRecord.execution_id == execution_id, LogRecord.id > after)
            if stream:
                query = query.where(LogRecord.stream == stream)
            rows = list(db.scalars(query.order_by(LogRecord.id).limit(limit)))
            return {"items": [{**row_dict(r), "sequence": r.id} for r in rows], "next_cursor": rows[-1].id if rows else after}

    @app.post(prefix + "/executions/{execution_id}/reconcile")
    def execution_reconcile(execution_id: str, session=Depends(permission("execution.reconcile"))):
        with factory.begin() as db:
            execution = owned(db, Execution, execution_id, session.username, lock=True)
            if execution.status != "unknown" or execution.authorization_kind != "one_shot":
                raise HTTPException(409, "只可對帳結果待確認的單次操作")
            if not execution_dict(db, execution)["reconciliation_supported"]:
                raise HTTPException(409, "此唯讀 SSH 探測未保存遠端 journal，無法補回中斷輸出；請另建並核准新的預檢草稿")
            if content_hash(execution.plan_document) != execution.approved_hash or validate_inventory(db, execution.plan_document):
                raise HTTPException(409, "計畫或主機身分已變更，不能沿用原授權對帳")
            plan = db.get(Plan, execution.plan_id)
            if not plan or plan.content_hash != execution.approved_hash or content_hash(plan.document) != execution.approved_hash:
                raise HTTPException(409, "原始核准內容已變更")
            execution.status, execution.error = "queued", None
            plan.status = "approved"
            # started_at is retained; unknown steps are reattached, never blindly relaunched.
            audit(db, session.username, "execution.reconcile", execution.id, {"approved_hash": execution.approved_hash})
            return execution_dict(db, execution)

    @app.get(prefix + "/executions/{execution_id}/download")
    def execution_download(execution_id: str, session=Depends(read)):
        with factory() as db:
            execution = owned(db, Execution, execution_id, session.username)
            metadata = execution_dict(db, execution)
            document = execution.plan_document
        def generate():
            yield json.dumps({"type": "execution", "execution": metadata, "plan_document": document}, ensure_ascii=False) + "\n"
            cursor = 0
            while True:
                with factory() as db:
                    rows = list(db.scalars(select(LogRecord).where(LogRecord.execution_id == execution_id, LogRecord.id > cursor).order_by(LogRecord.id).limit(500)))
                    batch = [row_dict(r) for r in rows]
                if not batch:
                    break
                for item in batch:
                    yield json.dumps({"type": "log", **item, "sequence": item["id"]}, ensure_ascii=False) + "\n"
                cursor = batch[-1]["id"]
        return StreamingResponse(generate(), media_type="application/x-ndjson", headers={
            "Content-Disposition": f'attachment; filename="execution-{execution_id}.ndjson"'})

    def schedule_values(payload):
        values = payload.model_dump()
        if payload.expires_at - payload.starts_at > timedelta(days=settings.max_schedule_days):
            raise HTTPException(422, f"排程有效期不可超過 {settings.max_schedule_days} 天")
        if payload.expires_at <= datetime.now(UTC):
            raise HTTPException(422, "到期時間必須在未來")
        values.update(starts_at=iso(payload.starts_at), expires_at=iso(payload.expires_at))
        return values

    @app.get(prefix + "/schedules")
    def schedules(session=Depends(read)):
        with factory() as db:
            return {"items": [row_dict(s) for s in db.scalars(select(Schedule).where(Schedule.owner == session.username, Schedule.status != "deleted").order_by(Schedule.created_at.desc()))]}

    @app.delete(prefix + "/schedules/{schedule_id}", status_code=204)
    def schedule_delete(schedule_id: str, session=Depends(permission("schedule.manage"))):
        with factory.begin() as db:
            schedule = owned(db, Schedule, schedule_id, session.username, lock=True)
            schedule.status, schedule.enabled = "deleted", False
            schedule.next_run_at = schedule.approved_hash = None
            schedule.revision += 1
            db.execute(update(Execution).where(Execution.schedule_id == schedule.id,
                Execution.status.in_(["queued", "running"])).values(stop_requested=True))
            audit(db, session.username, "schedule.delete", schedule.id)

    @app.post(prefix + "/schedules", status_code=201)
    def schedule_create(payload: ScheduleInput, session=Depends(permission("schedule.manage"))):
        values = schedule_values(payload)
        with factory.begin() as db:
            choose_nodes(db, payload.host_ids)
            schedule = Schedule(**values, owner=session.username)
            db.add(schedule)
            db.flush()
            audit(db, session.username, "schedule.create", schedule.id)
            return row_dict(schedule)

    @app.get(prefix + "/schedules/{schedule_id}")
    def schedule_get(schedule_id: str, session=Depends(read)):
        with factory() as db:
            return row_dict(owned(db, Schedule, schedule_id, session.username))

    @app.patch(prefix + "/schedules/{schedule_id}")
    def schedule_update(schedule_id: str, payload: ScheduleInput, session=Depends(permission("schedule.manage"))):
        values = schedule_values(payload)
        with factory.begin() as db:
            schedule = owned(db, Schedule, schedule_id, session.username, lock=True)
            choose_nodes(db, payload.host_ids)
            for key, value in values.items():
                setattr(schedule, key, value)
            schedule.revision += 1
            schedule.status, schedule.enabled = "draft", False
            schedule.plan_document = schedule.content_hash = schedule.approved_hash = None
            schedule.approved_at = schedule.approved_by = schedule.next_run_at = None
            # Existing runs keep their historical version and cannot dispatch additional steps.
            db.execute(update(Execution).where(Execution.schedule_id == schedule.id, Execution.status.in_(["queued", "running"])).values(stop_requested=True))
            audit(db, session.username, "schedule.update", schedule.id, {"revision": schedule.revision})
            return row_dict(schedule)

    @app.post(prefix + "/schedules/{schedule_id}/plan")
    def schedule_plan(schedule_id: str, session=Depends(permission("schedule.manage"))):
        with factory.begin() as db:
            schedule = owned(db, Schedule, schedule_id, session.username, lock=True)
            if schedule.enabled:
                raise HTTPException(409, "請先停用排程再產生新計畫")
            document, issues = build_schedule_document(db, schedule, settings)
            schedule.plan_document, schedule.issues = document, issues
            schedule.content_hash = content_hash(document)
            schedule.status, schedule.approved_hash = "awaiting_approval", None
            audit(db, session.username, "schedule.plan", schedule.id, {"hash": schedule.content_hash})
            return row_dict(schedule)

    @app.post(prefix + "/schedules/{schedule_id}/approve")
    def schedule_approve(schedule_id: str, payload: ApprovalInput, session=Depends(permission("schedule.approve"))):
        with factory.begin() as db:
            schedule = owned(db, Schedule, schedule_id, session.username, lock=True)
            if not schedule.plan_document or payload.content_hash != schedule.content_hash or content_hash(schedule.plan_document) != payload.content_hash:
                raise HTTPException(409, "排程內容摘要不符，請重新審閱")
            if schedule.status not in {"awaiting_approval", "approved"} or schedule.issues or validate_inventory(db, schedule.plan_document):
                raise HTTPException(409, "排程尚未具備可核准的完整計畫")
            if parse_time(schedule.expires_at) <= datetime.now(UTC):
                raise HTTPException(409, "排程已到期")
            schedule.status, schedule.approved_hash = "approved", schedule.content_hash
            schedule.approved_at, schedule.approved_by = now(), session.username
            audit(db, session.username, "schedule.approve", schedule.id, {"hash": schedule.content_hash})
            return row_dict(schedule)

    def toggle_schedule(schedule_id, session, enabled):
        with factory.begin() as db:
            schedule = owned(db, Schedule, schedule_id, session.username, lock=True)
            if enabled and (schedule.status != "approved" or not schedule.approved_hash or not schedule.plan_document
                            or content_hash(schedule.plan_document) != schedule.approved_hash
                            or parse_time(schedule.expires_at) <= datetime.now(UTC)
                            or validate_inventory(db, schedule.plan_document)):
                raise HTTPException(409, "排程必須有尚未到期且內容一致的核准")
            schedule.enabled = enabled
            if enabled:
                schedule.next_run_at = max(now(), schedule.starts_at)
            else:
                db.execute(update(Execution).where(Execution.schedule_id == schedule.id, Execution.status.in_(["queued", "running"])).values(stop_requested=True))
            audit(db, session.username, "schedule.enable" if enabled else "schedule.disable", schedule.id)
            return row_dict(schedule)

    @app.post(prefix + "/schedules/{schedule_id}/enable")
    def schedule_enable(schedule_id: str, session=Depends(permission("schedule.enable"))):
        return toggle_schedule(schedule_id, session, True)

    @app.post(prefix + "/schedules/{schedule_id}/disable")
    def schedule_disable(schedule_id: str, session=Depends(permission("schedule.enable"))):
        return toggle_schedule(schedule_id, session, False)

    @app.get(prefix + "/jobs")
    def jobs(session=Depends(read)):
        with factory() as db:
            return {"items": [row_dict(j) for j in db.scalars(select(Job).where(Job.owner == session.username).order_by(Job.created_at.desc()))]}

    @app.get(prefix + "/jobs/{job_id}")
    def job_get(job_id: str, session=Depends(read)):
        with factory() as db:
            return row_dict(owned(db, Job, job_id, session.username))

    @app.get(prefix + "/jobs/{job_id}/artifacts")
    def artifacts(job_id: str, session=Depends(read)):
        with factory() as db:
            owned(db, Job, job_id, session.username)
            return {"items": [{k: v for k, v in row_dict(a).items() if k != "relative_path"} for a in db.scalars(
                select(Artifact).where(Artifact.job_id == job_id, Artifact.owner == session.username))]}

    @app.get(prefix + "/artifacts/{artifact_id}/download")
    def artifact_download(artifact_id: str, session=Depends(read)):
        with factory() as db:
            artifact = owned(db, Artifact, artifact_id, session.username)
            root = (settings.data_dir / "artifacts").resolve()
            path = root / artifact.relative_path
            if path.is_symlink() or not path.resolve().is_relative_to(root) or not path.is_file():
                raise HTTPException(404, "成果檔不存在或路徑不合法")
            with path.open("rb") as stream:
                actual_hash = hashlib.file_digest(stream, "sha256").hexdigest()
            if actual_hash != artifact.sha256:
                raise HTTPException(409, "成果檔摘要不符")
            return FileResponse(path, filename=artifact.filename, media_type="application/octet-stream")

    @app.get(prefix + "/events")
    async def events(request: Request, session=Depends(read)):
        async def event_stream():
            while not await request.is_disconnected():
                if session.expires_at <= now():
                    yield 'event: session_expired\ndata: {}\n\n'
                    break
                with factory() as db:
                    last_event = db.scalar(select(AuditEvent.id).order_by(AuditEvent.id.desc()).limit(1)) or 0
                    last_log = db.scalar(select(LogRecord.id).join(Execution).where(Execution.owner == session.username).order_by(LogRecord.id.desc()).limit(1)) or 0
                yield f'event: refresh\ndata: {json.dumps({"event_cursor": last_event, "log_cursor": last_log, "at": now()})}\n\n'
                yield ': heartbeat\n\n'
                await asyncio.sleep(3)
        return StreamingResponse(event_stream(), media_type="text/event-stream", headers={"X-Accel-Buffering": "no"})

    dist = ROOT / "frontend" / "dist"
    if dist.is_dir():
        app.mount("/", StaticFiles(directory=dist, html=True), name="frontend")
    return app
