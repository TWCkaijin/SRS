from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class LoginInput(StrictModel):
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=1024, repr=False)


class DraftInput(StrictModel):
    title: str = Field(min_length=1, max_length=200)
    operation: Literal["preflight", "submit_job", "cancel_job", "collect_results", "deploy", "write_config", "cleanup"]
    host_ids: list[str] = Field(min_length=1, max_length=2)
    parameters: dict = Field(default_factory=dict)

    @field_validator("host_ids")
    @classmethod
    def unique_hosts(cls, values):
        if len(values) != len(set(values)):
            raise ValueError("主機不可重複")
        return values


class ApprovalInput(StrictModel):
    content_hash: str = Field(pattern=r"^[a-f0-9]{64}$")


class RejectionInput(StrictModel):
    reason: str = Field(default="使用者拒絕此計畫", min_length=1, max_length=1000)


class ProjectInput(StrictModel):
    name: str = Field(min_length=1, max_length=128)
    path: str = Field(min_length=1, max_length=1024)
    entrypoint: str = Field(min_length=1, max_length=512)
    modes: list[Literal["cpu", "single", "ddp", "fsdp2"]] = Field(min_length=1, max_length=4)
    container: str = Field(min_length=1, max_length=1024)
    revision: str = Field(min_length=1, max_length=128)


class ScheduleInput(StrictModel):
    title: str = Field(min_length=1, max_length=200)
    operation: Literal["host_snapshot", "job_status", "job_logs"]
    host_ids: list[str] = Field(min_length=1, max_length=2)
    interval_seconds: int = Field(default=5, ge=5, le=86400)
    starts_at: datetime
    expires_at: datetime
    scope: dict = Field(default_factory=lambda: {"job_ids": []})

    @model_validator(mode="after")
    def valid_window(self):
        if self.starts_at.tzinfo is None or self.expires_at.tzinfo is None:
            raise ValueError("起訖時間必須包含時區")
        if self.expires_at <= self.starts_at:
            raise ValueError("到期時間必須晚於開始時間")
        if len(self.host_ids) != len(set(self.host_ids)):
            raise ValueError("主機不可重複")
        if set(self.scope) - {"job_ids"}:
            raise ValueError("排程 scope 只允許 job_ids")
        jobs = self.scope.get("job_ids", [])
        if not isinstance(jobs, list) or len(jobs) > 50 or any(not isinstance(j, str) for j in jobs):
            raise ValueError("job_ids 必須是最多 50 個工作 ID 的陣列")
        return self
