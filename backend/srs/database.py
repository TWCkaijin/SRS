from pathlib import Path

from sqlalchemy import create_engine, event, inspect, text
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import sessionmaker

from srs.config import Settings
from srs.models import Base


def make_engine(settings: Settings):
    if settings.database_url.startswith("sqlite:///"):
        file_path = settings.database_url.removeprefix("sqlite:///")
        if file_path != ":memory:":
            Path(file_path).parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    kwargs = {"pool_pre_ping": True}
    if settings.database_url.startswith("sqlite"):
        kwargs["connect_args"] = {"check_same_thread": False, "timeout": 20}
    engine = create_engine(settings.database_url, **kwargs)
    if engine.dialect.name == "sqlite":
        @event.listens_for(engine, "connect")
        def sqlite_pragmas(connection, _):
            cursor = connection.cursor()
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.execute("PRAGMA journal_mode=WAL")
            cursor.execute("PRAGMA busy_timeout=20000")
            cursor.close()
    return engine


def initialize(settings: Settings, *, create_schema: bool = False):
    engine = make_engine(settings)
    if create_schema:
        Base.metadata.create_all(engine)
        # Existing demo databases predate selectable monitoring intervals. Real deployments
        # use Alembic 0004; this additive compatibility step preserves local demo records.
        if engine.dialect.name == "sqlite" and "interval_seconds" not in {
            column["name"] for column in inspect(engine).get_columns("monitor_sessions")
        }:
            try:
                with engine.begin() as connection:
                    connection.execute(text("ALTER TABLE monitor_sessions ADD COLUMN interval_seconds INTEGER NOT NULL DEFAULT 2"))
            except OperationalError:
                if "interval_seconds" not in {c["name"] for c in inspect(engine).get_columns("monitor_sessions")}:
                    raise
    return engine, sessionmaker(engine, expire_on_commit=False)
