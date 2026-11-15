"""Independent executor and scheduler. Only persisted approvals can dispatch work."""
import asyncio
import contextlib
import hashlib
import json
import os
import shutil
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from sqlalchemy import or_, select, update

from srs.models import (
    Artifact,
    Execution,
    ExecutionStep,
    Job,
    JobLogCursor,
    LogRecord,
    Node,
    Plan,
    Schedule,
    iso,
    new_id,
    now,
)
from srs.planner import READ_OPERATIONS, content_hash, validate_inventory
from srs.service import TERMINAL, audit, authorization_errors, create_execution, schedule_matches
from srs.transport import make_adapter


class LogPersistenceError(RuntimeError):
    pass


class Worker:
    def __init__(self, settings, factory, *, adapter=None, credentials_file=None):
        self.settings, self.factory = settings, factory
        self.id = str(uuid4())
        self.adapter = adapter or make_adapter(settings.mode, credentials_file=credentials_file)

    def log(self, execution_id, step_id, host_id, stream, text):
        if stream not in {"stdout", "stderr", "audit"}:
            raise LogPersistenceError("Unsupported log stream")
        if shutil.disk_usage(self.settings.data_dir).free < self.settings.min_free_bytes:
            raise LogPersistenceError("Insufficient disk capacity for durable logging")
        try:
            with self.factory.begin() as db:
                # MariaDB TEXT is byte limited. Bound by UTF-8 bytes conservatively.
                for start in range(0, len(text), 8000):
                    db.add(LogRecord(execution_id=execution_id, step_id=step_id,
                                     host_id=host_id, stream=stream, text=text[start:start + 8000]))
        except Exception as exc:
            raise LogPersistenceError("Log transaction failed") from exc

    def trigger_schedules(self):
        """One compare-and-swap per due schedule; missed periods are deliberately skipped."""
        stamp = now()
        with self.factory() as db:
            identifiers = list(db.scalars(select(Schedule.id).where(Schedule.enabled.is_(True))))
        created = []
        for identifier in identifiers:
            with self.factory.begin() as db:
                schedule = db.scalar(select(Schedule).where(Schedule.id == identifier).with_for_update())
                if not schedule or not schedule.enabled:
                    continue
                if schedule.expires_at <= stamp:
                    schedule.enabled, schedule.status = False, "expired"
                    audit(db, "scheduler", "schedule.expired", schedule.id)
                    continue
                doc = schedule.plan_document
                if (schedule.status != "approved" or not doc or schedule.operation not in READ_OPERATIONS
                        or schedule.approved_hash != content_hash(doc) or doc.get("mode") != self.settings.mode
                        or not schedule_matches(schedule)
                        or validate_inventory(db, doc) or any(not s.get("read_only") for s in doc.get("steps", []))):
                    schedule.enabled, schedule.status = False, "invalidated"
                    audit(db, "scheduler", "schedule.invalidated", schedule.id)
                    continue
                if schedule.starts_at > stamp or (schedule.next_run_at and schedule.next_run_at > stamp):
                    continue
                active = db.get(Execution, schedule.active_execution_id) if schedule.active_execution_id else None
                if active and active.status not in TERMINAL:
                    continue
                # Unknown results remain blocked until reconciled; do not overlap an uncertain read.
                if active and active.status == "unknown":
                    continue
                previous = schedule.next_run_at
                next_run = iso(datetime.now(UTC) + timedelta(seconds=schedule.interval_seconds))
                condition = Schedule.next_run_at.is_(None) if previous is None else Schedule.next_run_at == previous
                won = db.execute(update(Schedule).where(Schedule.id == identifier, Schedule.enabled.is_(True),
                    Schedule.revision == schedule.revision, condition).values(next_run_at=next_run))
                if won.rowcount != 1:
                    continue
                run = create_execution(db, document=doc, owner=schedule.owner,
                    approved_hash=schedule.approved_hash, trigger_key=f"schedule:{schedule.id}:{schedule.revision}:{previous or stamp}",
                    schedule_id=schedule.id)
                schedule.active_execution_id = run.id
                audit(db, "scheduler", "schedule.trigger", schedule.id, {"execution_id": run.id, "revision": schedule.revision})
                created.append(run.id)
        return created

    def claim(self):
        stamp = now()
        with self.factory.begin() as db:
            candidates = list(db.scalars(select(Execution).where(or_(Execution.status == "queued",
                (Execution.status == "running") & (Execution.lease_until < stamp))).order_by(Execution.created_at).limit(20)))
            for execution in candidates:
                original = execution.status
                predicate = Execution.status == "queued" if original == "queued" else (
                    (Execution.status == "running") & (Execution.lease_until < stamp))
                won = db.execute(update(Execution).where(Execution.id == execution.id, predicate).values(
                    status="running", worker_id=self.id,
                    lease_until=iso(datetime.now(UTC) + timedelta(seconds=self.settings.lease_seconds))))
                if won.rowcount != 1:
                    continue
                db.refresh(execution)
                if execution.stop_requested:
                    execution.status, execution.finished_at = "cancelled", now()
                    continue
                problems = authorization_errors(db, execution, self.settings, started=bool(execution.started_at))
                if problems:
                    execution.status = "expired" if any("逾期" in x or "到期" in x for x in problems) else "invalidated"
                    execution.error, execution.finished_at = "；".join(problems), now()
                    if execution.plan_id:
                        db.get(Plan, execution.plan_id).status = execution.status
                    audit(db, self.id, "execution.blocked", execution.id, {"reasons": problems})
                    continue
                execution.started_at = execution.started_at or now()
                if execution.plan_id:
                    db.get(Plan, execution.plan_id).status = "running"
                audit(db, self.id, "execution.claim", execution.id, {"recovery": original == "running"})
                return execution.id
        return None

    async def heartbeat(self, execution_id, lost):
        while True:
            await asyncio.sleep(max(0.1, self.settings.lease_seconds / 3))
            try:
                with self.factory.begin() as db:
                    result = db.execute(update(Execution).where(Execution.id == execution_id, Execution.worker_id == self.id,
                        Execution.status == "running").values(lease_until=iso(datetime.now(UTC) + timedelta(seconds=self.settings.lease_seconds))))
                    if result.rowcount != 1:
                        lost.set()
                        return
            except Exception:
                lost.set()
                return

    def finish(self, execution_id, status, error=None, *, log_complete=True):
        with self.factory.begin() as db:
            execution = db.get(Execution, execution_id)
            if execution.worker_id != self.id:
                return
            if status == "succeeded" and self.settings.mode != "demo":
                from infra.evidence import parse_deployment_evidence
                from srs.planner import node_binding
                for step in execution.plan_document.get("steps", []):
                    if not step.get("deployment_probe"):
                        continue
                    record = db.scalar(select(ExecutionStep).where(ExecutionStep.execution_id == execution.id, ExecutionStep.step_id == step["id"]))
                    observations = record.outputs.get("deployment_observations", [])
                    node = db.get(Node, step["host_id"])
                    probe = step["deployment_probe"]
                    try:
                        if len(observations) != 1:
                            raise ValueError("Expected exactly one deployment observation")
                        evidence = parse_deployment_evidence(observations[0], probe["component"],
                            {**node_binding(node), "hardware": node.hardware}, probe["spec"],
                            execution_id=execution.id, observed_at=now())
                        node.facts = {**node.facts, "deploy": {**node.facts.get("deploy", {}), probe["component"]: evidence}}
                    except ValueError as exc:
                        status, error = "failed", "部署預檢未通過：" + str(exc)
            cluster_spec = execution.plan_document.get("source", {}).get("parameters", {}).get("cluster_probe")
            if status == "succeeded" and cluster_spec and self.settings.mode != "demo":
                from srs.cluster_probe import parse_cluster_evidence
                observations = {}
                for record in db.scalars(select(ExecutionStep).where(ExecutionStep.execution_id == execution.id)):
                    for item in record.outputs.get("cluster_observations", []):
                        observations[record.host_id] = item
                try:
                    facts = parse_cluster_evidence(observations, cluster_spec, execution_id=execution.id, validated_at=now())
                    for node_id, evidence in facts.items():
                        node = db.get(Node, node_id)
                        node.facts = {**node.facts, "cluster": evidence}
                except ValueError as exc:
                    status, error = "failed", "跨節點預檢未通過：" + str(exc)
            execution.status, execution.error, execution.finished_at = status, error, now()
            execution.lease_until = None
            execution.log_complete = execution.log_complete and log_complete
            if execution.plan_id:
                db.get(Plan, execution.plan_id).status = status
            for step in db.scalars(select(ExecutionStep).where(ExecutionStep.execution_id == execution_id, ExecutionStep.status == "pending")):
                step.status = "skipped"
            audit(db, self.id, "execution.finish", execution.id, {"status": status, "log_complete": execution.log_complete})

    def apply_outputs(self, db, execution, step, result, objects):
        outputs = result.get("outputs", {})
        node = db.get(Node, step["host_id"])
        if self.settings.mode == "demo" and "snapshot" in outputs:
            node.snapshot = {"status": "online", "source": "demo", "simulation": True,
                "cpu_percent": 12.5, "memory_total_mb": node.hardware.get("memory_gb", 0) * 1024,
                "memory_used_mb": 4096,
                "gpus": [{**gpu, "memory_total_mb": gpu.get("memory_mb", 0), "memory_used_mb": 512,
                          "utilization_percent": 7} for gpu in node.hardware.get("gpus", [])]}
            node.observed_at = now()
        for item in objects:
            if item.get("kind") == "host_snapshot":
                node.snapshot, node.observed_at = item, now()
            elif item.get("kind") == "preflight_evidence":
                node.facts = {**node.facts, "projects": item.get("projects", {}),
                              "files": item.get("files", {}), "preflight_at": now()}
            elif item.get("kind") == "artifact":
                scope = step.get("artifact_scope", {})
                job = db.get(Job, item.get("job_id", ""))
                filename, text = item.get("filename"), item.get("text")
                if (not job or job.owner != execution.owner or job.id != scope.get("job_id")
                        or filename not in scope.get("filenames", []) or not isinstance(text, str)
                        or len(text.encode()) > scope.get("max_bytes", 0)):
                    raise ValueError("Artifact outside approved scope")
                raw = text.encode()
                checksum = hashlib.sha256(raw).hexdigest()
                artifact_root = self.settings.data_dir / "artifacts"
                directory = artifact_root / job.id
                artifact_root.mkdir(exist_ok=True, mode=0o2750)
                directory.mkdir(exist_ok=True, mode=0o2750)
                # Only result storage is group-readable; credential files remain executor-only 0600.
                os.chmod(artifact_root, 0o2750)
                os.chmod(directory, 0o2750)
                artifact_id = new_id()
                relative = f"{job.id}/{artifact_id}-{filename}"
                target = self.settings.data_dir / "artifacts" / relative
                fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o640)
                with os.fdopen(fd, "wb") as destination:
                    os.fchmod(destination.fileno(), 0o640)
                    destination.write(raw)
                    destination.flush()
                    os.fsync(destination.fileno())
                db.add(Artifact(id=artifact_id, job_id=job.id, owner=job.owner, filename=filename,
                                relative_path=relative, sha256=checksum, size=len(raw)))
                results = {k: dict(v) for k, v in node.facts.get("results", {}).items()}
                results.setdefault(job.id, {})[filename] = {"source_sha256": item.get("source_sha256"),
                    "text": text, "observed_at": now(), "execution_id": execution.id}
                node.facts = {**node.facts, "results": results}
            elif item.get("kind") == "job_log":
                scope = step.get("job_log_scope", {})
                job_id, stream = item.get("job_id"), item.get("stream")
                if job_id != scope.get("job_id") or stream not in scope.get("streams", []):
                    execution.log_complete = False
                    continue
                job = db.get(Job, job_id)
                if not job or job.owner != execution.owner:
                    execution.log_complete = False
                    continue
                cursor = db.scalar(select(JobLogCursor).where(JobLogCursor.job_id == job_id, JobLogCursor.stream == stream))
                if cursor is None:
                    cursor = JobLogCursor(job_id=job_id, stream=stream, offset=0, gap=False)
                    db.add(cursor)
                requested, end = item.get("requested_start"), item.get("end")
                if type(requested) is not int or type(end) is not int or requested != cursor.offset or end < 0:
                    execution.log_complete, cursor.gap = False, True
                    continue
                changed = bool(cursor.inode and (str(item.get("inode")) != cursor.inode or str(item.get("device")) != cursor.device))
                cursor.gap = cursor.gap or bool(item.get("gap")) or changed
                if item.get("exists"):
                    cursor.offset, cursor.inode, cursor.device = (0 if changed else end), str(item["inode"]), str(item["device"])
                    cursor.observed_size = int(item["size"])
                cursor.has_more, cursor.observed_at = bool(item.get("has_more")), now()
                if cursor.gap:
                    execution.log_complete = False
                job.snapshot = {**job.snapshot, "log_sync_at": now(), "log_gap": cursor.gap,
                                "log_backlog": cursor.has_more}
        if step.get("operation") == "submit_job" and outputs.get("job_id"):
            source = execution.plan_document["source"]["parameters"]
            if not db.scalar(select(Job.id).where(Job.execution_id == execution.id)):
                job = Job(slurm_job_id=str(outputs["job_id"]), project_id=source["project_id"],
                    profile=source.get("profile", "single"), execution_id=execution.id, owner=execution.owner,
                    status="PENDING", snapshot={"source": self.settings.mode, "observed_at": now()})
                db.add(job)
                db.flush()
                for stream in ("stdout", "stderr"):
                    db.add(JobLogCursor(job_id=job.id, stream=stream, offset=0))
        if step.get("operation") in {"cancel_job", "job_status"}:
            evidence = step.get("evidence", [{}])[0]
            job = db.get(Job, evidence.get("job_id", ""))
            if job and job.owner == execution.owner:
                if step["operation"] == "cancel_job":
                    job.status = "CANCEL_REQUESTED" if self.settings.mode != "demo" else "CANCELLED"
                else:
                    response = outputs.get("response", {})
                    records = response.get("jobs", [])
                    if records:
                        state = records[0].get("job_state", "UNKNOWN")
                        job.status = ",".join(state) if isinstance(state, list) else str(state)
                    job.snapshot = {"source": self.settings.mode, "observed_at": now(), "response": response}

    async def execute(self, execution_id):
        lost = asyncio.Event()
        heartbeat = asyncio.create_task(self.heartbeat(execution_id, lost))
        try:
            with self.factory() as db:
                document = db.get(Execution, execution_id).plan_document
            for step in document["steps"]:
                with self.factory.begin() as db:
                    execution = db.get(Execution, execution_id)
                    if execution.worker_id != self.id or lost.is_set():
                        return
                    record = db.scalar(select(ExecutionStep).where(ExecutionStep.execution_id == execution_id, ExecutionStep.step_id == step["id"]))
                    if record.status == "succeeded":
                        continue
                    if execution.stop_requested:
                        self.finish(execution_id, "cancelled", "已停止派發後續步驟；未發送遠端程序終止指令")
                        return
                    problems = authorization_errors(db, execution, self.settings, started=True)
                    if problems:
                        self.finish(execution_id, "invalidated", "；".join(problems))
                        return
                    recovering = record.status in {"running", "unknown"}
                    record.status, record.started_at = "running", record.started_at or now()
                    host = db.get(Node, step["host_id"])
                    context = {"execution_id": execution.id, "step_id": step["id"], "mode": execution.mode,
                        "host": {**next(n for n in document["inventory"] if n["id"] == host.id), "hardware": host.hardware},
                        "data_dir": str(self.settings.data_dir), "plan_hash": execution.approved_hash,
                        "reconcile_only": recovering}
                    if step.get("job_log_scope"):
                        scope = step["job_log_scope"]
                        job = db.get(Job, scope["job_id"])
                        if not job or job.owner != execution.owner or job.slurm_job_id != scope["slurm_job_id"]:
                            self.finish(execution_id, "invalidated", "工作日誌範圍與登錄資料不一致")
                            return
                        cursors = {c.stream: c.offset for c in db.scalars(select(JobLogCursor).where(JobLogCursor.job_id == job.id))}
                        if any(stream not in cursors for stream in scope["streams"]):
                            raise ValueError("Registered log cursor missing; never assume zero after restart")
                        context["job_log_offsets"] = {job.id: {stream: cursors[stream] for stream in scope["streams"]}}
                self.log(execution_id, step["id"], step["host_id"], "audit", json.dumps({
                    "event": "reconcile" if recovering else "dispatch", "plan_hash": context["plan_hash"],
                    "step_id": step["id"], "at": now(), "mode": self.settings.mode}))
                objects, pending = [], ""
                async def emit(stream, text, _step=step, _objects=objects):
                    nonlocal pending
                    if lost.is_set():
                        raise LogPersistenceError("Worker lease lost; remote result requires reconciliation")
                    self.log(execution_id, _step["id"], _step["host_id"], stream, text)
                    if stream == "stdout":
                        pending += text
                        while "\n" in pending:
                            line, pending = pending.split("\n", 1)
                            if len(line) <= 1024 * 1024:
                                with contextlib.suppress(ValueError):
                                    item = json.loads(line)
                                    if isinstance(item, dict):
                                        _objects.append(item)
                        # Evidence may be bounded; raw output above is always persisted in full.
                        if len(pending) > 1024 * 1024:
                            pending = ""
                try:
                    result = await self.adapter.run_step(step, context, emit)
                except LogPersistenceError:
                    self.finish(execution_id, "unknown", "日誌無法可靠保存；停止派發並等待對帳", log_complete=False)
                    return
                except Exception as exc:
                    # Do not persist arbitrary exception strings that could contain credentials.
                    self.log(execution_id, step["id"], step["host_id"], "stderr", "Executor exception: " + type(exc).__name__)
                    result = {"unknown": True, "exit_code": None, "signal": None, "error": "Executor exception; reconciliation required", "outputs": {}}
                if pending:
                    with contextlib.suppress(ValueError):
                        item = json.loads(pending)
                        if isinstance(item, dict):
                            objects.append(item)
                status = "unknown" if result.get("unknown") else "succeeded" if result.get("exit_code") == 0 else "failed"
                if result.get("precondition_failed"):
                    status = "invalidated"
                if result.get("timed_out"):
                    status = "failed" if not result.get("unknown") else "unknown"
                with self.factory.begin() as db:
                    if lost.is_set():
                        return
                    # Acquire a write fence before accepting a transport result. A stale worker
                    # may have finished its socket call after a replacement claimed the lease.
                    fence = db.execute(update(Execution).where(Execution.id == execution_id,
                        Execution.worker_id == self.id, Execution.status == "running",
                        Execution.lease_until > now()).values(lease_until=Execution.lease_until))
                    if fence.rowcount != 1:
                        return
                    execution = db.get(Execution, execution_id)
                    record = db.scalar(select(ExecutionStep).where(ExecutionStep.execution_id == execution_id, ExecutionStep.step_id == step["id"]))
                    record.status, record.finished_at = status, now()
                    record.exit_code, record.signal = result.get("exit_code"), result.get("signal")
                    record.error = result.get("error")
                    record.outputs = {**result.get("outputs", {}), "timed_out": bool(result.get("timed_out")), "unknown": bool(result.get("unknown"))}
                    cluster_observations = [item for item in objects if item.get("kind") == "cluster_probe"]
                    if cluster_observations:
                        record.outputs = {**record.outputs, "cluster_observations": cluster_observations}
                    deployment_observations = [item for item in objects if item.get("kind") == "deployment_probe"]
                    if deployment_observations:
                        record.outputs = {**record.outputs, "deployment_observations": deployment_observations}
                    if status == "succeeded":
                        self.apply_outputs(db, execution, step, result, objects)
                self.log(execution_id, step["id"], step["host_id"], "audit", json.dumps({"event": "step_finished", "status": status,
                    "exit_code": result.get("exit_code"), "signal": result.get("signal"), "timed_out": result.get("timed_out", False)}))
                if status != "succeeded":
                    self.finish(execution_id, status, result.get("error") or "步驟未成功；後續步驟已停止", log_complete=status != "unknown")
                    return
            self.finish(execution_id, "succeeded")
        except LogPersistenceError:
            self.finish(execution_id, "unknown", "無法可靠保存日誌，未派發後續步驟", log_complete=False)
        except Exception as exc:
            # Preserve a safe error classification and the unresolved state, never raw secrets.
            with contextlib.suppress(LogPersistenceError):
                self.log(execution_id, None, None, "stderr", "Worker processing error: " + type(exc).__name__)
            self.finish(execution_id, "unknown", "執行結果處理失敗；停止後續步驟並等待對帳", log_complete=False)
        finally:
            heartbeat.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await heartbeat

    async def run_once(self):
        identifier = self.claim()
        if identifier:
            await self.execute(identifier)
        return identifier

    async def run(self):
        while True:
            identifier = await self.run_once()
            if not identifier:
                await asyncio.sleep(self.settings.worker_poll_seconds)

    async def schedule(self):
        while True:
            self.trigger_schedules()
            await asyncio.sleep(self.settings.worker_poll_seconds)
