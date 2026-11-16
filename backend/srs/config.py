"""Public application configuration. This module never reads host credentials."""

from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT = Path(__file__).resolve().parents[2]
LOCAL_INVENTORY = ROOT / "config" / "inventory.yaml"
PERMISSIONS = (
    "hosts.terminal", "hosts.accounts", "read", "monitor.manage", "hosts.manage", "project.manage", "draft.manage", "plan.approve", "execution.stop", "execution.reconcile",
    "schedule.manage", "schedule.approve", "schedule.enable",
)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="SRS_", env_file=ROOT / ".env.web", extra="ignore")

    mode: Literal["demo", "local-slurm", "remote-slurm"] = "demo"
    database_url: str = f"sqlite:///{ROOT / 'var' / 'srs-demo.sqlite3'}"
    data_dir: Path = ROOT / "var"
    inventory_file: Path = LOCAL_INVENTORY
    project_root: Path = Path("/srv/srs/projects")
    web_admin_username: str = Field(default="admin", validation_alias="WEB_ADMIN_USERNAME")
    web_admin_password: SecretStr = Field(default=SecretStr(""), validation_alias="WEB_ADMIN_PASSWORD")
    permissions: str = ",".join(PERMISSIONS)
    session_hours: int = 8
    cookie_secure: bool = False
    allowed_origins: str = "http://127.0.0.1:8000,http://localhost:8000,http://127.0.0.1:5173,http://localhost:5173"
    approval_minutes: int = 30
    worker_poll_seconds: float = 1.0
    lease_seconds: int = 45
    min_free_bytes: int = 256 * 1024 * 1024
    allow_sqlite_live: bool = False
    slurm_api_version: str = "v0.0.45"
    slurm_base_url: str = "http://127.0.0.1:6820"
    slurm_owner: str = "srs-runner"
    slurm_controller_id: str = Field(default="225", min_length=1, max_length=64)
    max_schedule_days: int = 30
    request_max_bytes: int = 1024 * 1024

    @field_validator("web_admin_username")
    @classmethod
    def valid_username(cls, value: str) -> str:
        if not value or len(value) > 64:
            raise ValueError("WEB_ADMIN_USERNAME must be 1–64 characters")
        return value

    @property
    def configured(self) -> bool:
        password = self.web_admin_password.get_secret_value()
        return len(password) >= 12 and not password.startswith("<")

    @property
    def permission_set(self) -> set[str]:
        return {p.strip() for p in self.permissions.split(",") if p.strip()} & set(PERMISSIONS)

    def validate_runtime(self) -> None:
        if self.mode != "demo" and self.database_url.startswith("sqlite") and not self.allow_sqlite_live:
            raise ValueError("Real execution requires MariaDB; SQLite is only a labelled local test option")
        if self.mode != "demo" and not self.configured:
            raise ValueError("Set a unique WEB_ADMIN_PASSWORD (at least 12 characters) before live mode")
        self.data_dir.mkdir(parents=True, exist_ok=True, mode=0o700)

