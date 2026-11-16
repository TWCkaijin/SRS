from pathlib import Path

from sqlalchemy import create_engine, event, inspect, select, text
from sqlalchemy.orm import sessionmaker

from srs.config import Settings
from srs.models import Base, Project
from srs.seed import seed


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
            cursor.execute("PRAGMA busy_timeout=20000")
            cursor.execute("PRAGMA journal_mode=WAL")
            cursor.close()
    return engine


def initialize(settings: Settings):
    engine = make_engine(settings)
    return engine, sessionmaker(engine, expire_on_commit=False)


def require_schema(connection):
    schema = inspect(connection)
    missing = set(Base.metadata.tables) - set(schema.get_table_names())
    if missing or "interval_seconds" not in {column["name"] for column in schema.get_columns("monitor_sessions")}:
        raise RuntimeError("Database schema is not ready. Run python -m srs.cli init-db first.")


def prepare_database(settings: Settings, *, create_schema: bool):
    """Finish schema setup and commit inventory before the API can accept requests."""
    engine, factory = initialize(settings)
    try:
        with engine.begin() as connection:
            if engine.dialect.name == "sqlite":
                # SQLite otherwise starts its transaction at the first DML.
                # Take the writer lock before checking schema or seed records.
                connection.exec_driver_sql("BEGIN IMMEDIATE")
            if create_schema:
                Base.metadata.create_all(connection)
                columns = {column["name"] for column in inspect(connection).get_columns("monitor_sessions")}
                if engine.dialect.name == "sqlite" and "interval_seconds" not in columns:
                    connection.execute(text(
                        "ALTER TABLE monitor_sessions ADD COLUMN interval_seconds INTEGER NOT NULL DEFAULT 2"
                    ))
            require_schema(connection)
            with factory(bind=connection) as db:
                seed(db, settings)
                db.flush()
        return engine, factory
    except Exception:
        engine.dispose()
        raise


def open_ready_database(settings: Settings):
    """Background services consume the database prepared by the API or init-db."""
    engine, factory = initialize(settings)
    try:
        with engine.connect() as connection:
            require_schema(connection)
            if connection.execute(select(Project.id).limit(1)).first() is None:
                raise RuntimeError("Database inventory is not ready. Run python -m srs.cli init-db first.")
        return engine, factory
    except Exception:
        engine.dispose()
        raise
