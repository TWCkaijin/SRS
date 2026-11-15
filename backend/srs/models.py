"""Persistent approval, execution and schedule records, portable to MariaDB."""

from datetime import UTC, datetime
from uuid import uuid4

from sqlalchemy import JSON, BigInteger, Boolean, ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


def new_id() -> str:
    return str(uuid4())


def now() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def iso(value: datetime) -> str:
    return value.astimezone(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def parse_time(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(UTC)


class Base(DeclarativeBase):
    pass


class SessionRecord(Base):
    __tablename__ = "web_sessions"
    token_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    username: Mapped[str] = mapped_column(String(64))
    csrf: Mapped[str] = mapped_column(String(96))
    auth_revision: Mapped[str] = mapped_column(String(64))
    expires_at: Mapped[str] = mapped_column(String(32), index=True)


class LoginAttempt(Base):
    __tablename__ = "login_attempts"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    client: Mapped[str] = mapped_column(String(128), index=True)
    created_at: Mapped[str] = mapped_column(String(32), default=now, index=True)


class MonitorSession(Base):
    __tablename__ = "monitor_sessions"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    generation: Mapped[str] = mapped_column(String(36), default=new_id)
    host_ids: Mapped[list] = mapped_column(JSON, default=list)
    inventory: Mapped[dict] = mapped_column(JSON, default=dict)
    results: Mapped[dict] = mapped_column(JSON, default=dict)
    expires_at: Mapped[str] = mapped_column(String(32), index=True)
    next_poll_at: Mapped[str] = mapped_column(String(32))
    checked_at: Mapped[str | None] = mapped_column(String(32), nullable=True)
    interval_seconds: Mapped[int] = mapped_column(Integer, default=2)


class Node(Base):
    __tablename__ = "nodes"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    alias: Mapped[str] = mapped_column(String(64), unique=True)
    display_name: Mapped[str] = mapped_column(String(128))
    hostname: Mapped[str] = mapped_column(String(255))
    port: Mapped[int] = mapped_column(Integer, default=22)
    ssh_username: Mapped[str] = mapped_column(String(64), default="admin")
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    inventory_revision: Mapped[int] = mapped_column(Integer, default=1)
    host_key_sha256: Mapped[str | None] = mapped_column(String(128), nullable=True)
    hardware: Mapped[dict] = mapped_column(JSON, default=dict)
    snapshot: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    observed_at: Mapped[str | None] = mapped_column(String(32), nullable=True)
    facts: Mapped[dict] = mapped_column(JSON, default=dict)


class SSHHost(Base):
    """Observation settings and hardware profile, separate from execution inventory."""
    __tablename__ = "ssh_hosts"
    node_id: Mapped[str] = mapped_column(ForeignKey("nodes.id"), primary_key=True)
    display_name: Mapped[str] = mapped_column(String(128))
    hostname: Mapped[str] = mapped_column(String(255))
    port: Mapped[int] = mapped_column(Integer, default=22)
    username: Mapped[str] = mapped_column(String(64))
    password_encrypted: Mapped[str] = mapped_column(Text)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    revision: Mapped[int] = mapped_column(Integer, default=1)
    host_key_sha256: Mapped[str | None] = mapped_column(String(128), nullable=True)
    pending_host_key_sha256: Mapped[str | None] = mapped_column(String(128), nullable=True)
    status: Mapped[str] = mapped_column(String(32), default="configured")
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    profile: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    first_seen_at: Mapped[str | None] = mapped_column(String(32), nullable=True)
    profile_updated_at: Mapped[str | None] = mapped_column(String(32), nullable=True)
    requested_at: Mapped[str | None] = mapped_column(String(32), nullable=True)
    claim_until: Mapped[str] = mapped_column(String(32), default=now)


class Project(Base):
    __tablename__ = "projects"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    name: Mapped[str] = mapped_column(String(128))
    path: Mapped[str] = mapped_column(String(1024))
    entrypoint: Mapped[str] = mapped_column(String(512))
    modes: Mapped[list] = mapped_column(JSON, default=list)
    container: Mapped[str] = mapped_column(String(1024))
    revision: Mapped[str] = mapped_column(String(128))
    owner: Mapped[str] = mapped_column(String(64), default="admin")
    created_at: Mapped[str] = mapped_column(String(32), default=now)


class Draft(Base):
    __tablename__ = "drafts"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    title: Mapped[str] = mapped_column(String(200))
    operation: Mapped[str] = mapped_column(String(32))
    host_ids: Mapped[list] = mapped_column(JSON, default=list)
    parameters: Mapped[dict] = mapped_column(JSON, default=dict)
    revision: Mapped[int] = mapped_column(Integer, default=1)
    owner: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[str] = mapped_column(String(32), default=now)
    updated_at: Mapped[str] = mapped_column(String(32), default=now)


class Plan(Base):
    __tablename__ = "plans"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    draft_id: Mapped[str | None] = mapped_column(ForeignKey("drafts.id"), nullable=True)
    revision: Mapped[int] = mapped_column(Integer)
    title: Mapped[str] = mapped_column(String(200))
    status: Mapped[str] = mapped_column(String(32), default="awaiting_approval", index=True)
    content_hash: Mapped[str] = mapped_column(String(64))
    document: Mapped[dict] = mapped_column(JSON)
    issues: Mapped[list] = mapped_column(JSON, default=list)
    owner: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[str] = mapped_column(String(32), default=now)
    approved_at: Mapped[str | None] = mapped_column(String(32), nullable=True)
    approval_expires_at: Mapped[str | None] = mapped_column(String(32), nullable=True)
    approved_by: Mapped[str | None] = mapped_column(String(64), nullable=True)
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)


class Schedule(Base):
    __tablename__ = "schedules"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    title: Mapped[str] = mapped_column(String(200))
    operation: Mapped[str] = mapped_column(String(32))
    host_ids: Mapped[list] = mapped_column(JSON)
    interval_seconds: Mapped[int] = mapped_column(Integer)
    starts_at: Mapped[str] = mapped_column(String(32))
    expires_at: Mapped[str] = mapped_column(String(32), index=True)
    scope: Mapped[dict] = mapped_column(JSON, default=dict)
    revision: Mapped[int] = mapped_column(Integer, default=1)
    status: Mapped[str] = mapped_column(String(32), default="draft")
    enabled: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    plan_document: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    content_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    approved_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    approved_by: Mapped[str | None] = mapped_column(String(64), nullable=True)
    approved_at: Mapped[str | None] = mapped_column(String(32), nullable=True)
    owner: Mapped[str] = mapped_column(String(64))
    next_run_at: Mapped[str | None] = mapped_column(String(32), nullable=True)
    active_execution_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    issues: Mapped[list] = mapped_column(JSON, default=list)
    created_at: Mapped[str] = mapped_column(String(32), default=now)


class Execution(Base):
    __tablename__ = "executions"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    plan_id: Mapped[str | None] = mapped_column(ForeignKey("plans.id"), nullable=True)
    schedule_id: Mapped[str | None] = mapped_column(ForeignKey("schedules.id"), nullable=True)
    trigger_key: Mapped[str] = mapped_column(String(160), unique=True)
    authorization_kind: Mapped[str] = mapped_column(String(16))
    approved_hash: Mapped[str] = mapped_column(String(64))
    plan_document: Mapped[dict] = mapped_column(JSON)
    owner: Mapped[str] = mapped_column(String(64))
    mode: Mapped[str] = mapped_column(String(32))
    status: Mapped[str] = mapped_column(String(32), default="queued", index=True)
    created_at: Mapped[str] = mapped_column(String(32), default=now)
    started_at: Mapped[str | None] = mapped_column(String(32), nullable=True)
    finished_at: Mapped[str | None] = mapped_column(String(32), nullable=True)
    worker_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    lease_until: Mapped[str | None] = mapped_column(String(32), nullable=True)
    stop_requested: Mapped[bool] = mapped_column(Boolean, default=False)
    log_complete: Mapped[bool] = mapped_column(Boolean, default=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)


class ExecutionStep(Base):
    __tablename__ = "execution_steps"
    __table_args__ = (UniqueConstraint("execution_id", "step_id"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    execution_id: Mapped[str] = mapped_column(ForeignKey("executions.id"), index=True)
    step_id: Mapped[str] = mapped_column(String(64))
    title: Mapped[str] = mapped_column(String(200))
    host_id: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(32), default="pending")
    started_at: Mapped[str | None] = mapped_column(String(32), nullable=True)
    finished_at: Mapped[str | None] = mapped_column(String(32), nullable=True)
    exit_code: Mapped[int | None] = mapped_column(Integer, nullable=True)
    signal: Mapped[str | None] = mapped_column(String(64), nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    outputs: Mapped[dict] = mapped_column(JSON, default=dict)


class LogRecord(Base):
    __tablename__ = "execution_logs"
    __table_args__ = (Index("ix_execution_logs_cursor", "execution_id", "id"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    execution_id: Mapped[str] = mapped_column(ForeignKey("executions.id"))
    step_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    host_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    stream: Mapped[str] = mapped_column(String(16))
    text: Mapped[str] = mapped_column(Text)
    created_at: Mapped[str] = mapped_column(String(32), default=now)


class AuditEvent(Base):
    __tablename__ = "audit_events"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    actor: Mapped[str] = mapped_column(String(64))
    action: Mapped[str] = mapped_column(String(64))
    subject_id: Mapped[str] = mapped_column(String(64))
    details: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[str] = mapped_column(String(32), default=now)


class Job(Base):
    __tablename__ = "jobs"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    slurm_job_id: Mapped[str] = mapped_column(String(64))
    project_id: Mapped[str | None] = mapped_column(ForeignKey("projects.id"), nullable=True)
    profile: Mapped[str] = mapped_column(String(32))
    status: Mapped[str] = mapped_column(String(64), default="PENDING")
    execution_id: Mapped[str] = mapped_column(ForeignKey("executions.id"), unique=True)
    owner: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[str] = mapped_column(String(32), default=now)
    snapshot: Mapped[dict] = mapped_column(JSON, default=dict)


class Artifact(Base):
    __tablename__ = "artifacts"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    job_id: Mapped[str] = mapped_column(ForeignKey("jobs.id"))
    owner: Mapped[str] = mapped_column(String(64))
    filename: Mapped[str] = mapped_column(String(255))
    relative_path: Mapped[str] = mapped_column(String(1024))
    sha256: Mapped[str] = mapped_column(String(64))
    size: Mapped[int] = mapped_column(Integer)


class JobLogCursor(Base):
    __tablename__ = "job_log_cursors"
    __table_args__ = (UniqueConstraint("job_id", "stream"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    job_id: Mapped[str] = mapped_column(ForeignKey("jobs.id"), index=True)
    stream: Mapped[str] = mapped_column(String(16))
    offset: Mapped[int] = mapped_column(BigInteger, default=0)
    inode: Mapped[str | None] = mapped_column(String(64), nullable=True)
    device: Mapped[str | None] = mapped_column(String(64), nullable=True)
    observed_size: Mapped[int] = mapped_column(BigInteger, default=0)
    has_more: Mapped[bool] = mapped_column(Boolean, default=False)
    gap: Mapped[bool] = mapped_column(Boolean, default=False)
    observed_at: Mapped[str] = mapped_column(String(32), default=now)



class MonitorHistory(Base):
    __tablename__ = "monitor_history"
    __table_args__ = (Index("ix_monitor_history_host_time", "host_id", "collected_at"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    host_id: Mapped[str] = mapped_column(String(64), index=True)
    collected_at: Mapped[str] = mapped_column(String(32), default=now, index=True)
    observer: Mapped[str] = mapped_column(String(64))
    result: Mapped[dict] = mapped_column(JSON)


class MonitorAlert(Base):
    __tablename__ = "monitor_alerts"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    host_id: Mapped[str] = mapped_column(String(64), index=True)
    rule: Mapped[str] = mapped_column(String(256))
    message: Mapped[str] = mapped_column(Text)
    opened_at: Mapped[str] = mapped_column(String(32), default=now, index=True)
    last_seen_at: Mapped[str] = mapped_column(String(32), default=now)
    resolved_at: Mapped[str | None] = mapped_column(String(32), nullable=True)


class AccountPlan(Base):
    __tablename__ = "account_plans"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    host_id: Mapped[str] = mapped_column(String(64))
    owner: Mapped[str] = mapped_column(String(64))
    document: Mapped[dict] = mapped_column(JSON)
    content_hash: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(32), default="planned")
    created_at: Mapped[str] = mapped_column(String(32), default=now)
    expires_at: Mapped[str] = mapped_column(String(32))
    finished_at: Mapped[str | None] = mapped_column(String(32), nullable=True)
