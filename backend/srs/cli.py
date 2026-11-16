"""Explicit service entry points. Starting the API never starts an executor."""
import argparse
import asyncio
from pathlib import Path

from alembic import command
from alembic.config import Config

from srs.config import ROOT, Settings
from srs.database import open_ready_database, prepare_database


def main():
    parser = argparse.ArgumentParser(description="Approval-first research compute platform")
    sub = parser.add_subparsers(dest="command", required=True)
    api = sub.add_parser("api", help="Cached API and web UI; never dispatches remote commands")
    api.add_argument("--host", default="127.0.0.1")
    api.add_argument("--port", type=int, default=8000)
    worker = sub.add_parser("worker", help="Execute only persisted, approved plans")
    worker.add_argument("--credentials-file", type=Path)
    worker.add_argument("--once", action="store_true")
    scheduler = sub.add_parser("scheduler", help="Enqueue only approved, unexpired read-only schedules")
    scheduler.add_argument("--once", action="store_true")
    monitor = sub.add_parser("monitor", help="Real read-only observations for explicit browser leases")
    monitor.add_argument("--credentials-file", type=Path, help="Optional one-time legacy settings import")
    sub.add_parser("init-db", help="Prepare the database schema and seed local inventory")
    args = parser.parse_args()
    settings = Settings()
    settings.validate_runtime()
    if args.command == "monitor":
        from srs.monitor import MonitorWorker

        _, factory = open_ready_database(settings)
        asyncio.run(MonitorWorker(settings, factory, args.credentials_file).run())
        return
    if args.command == "api":
        import uvicorn

        from srs.app import create_app
        uvicorn.run(create_app(settings), host=args.host, port=args.port, log_level="info")
        return
    if args.command == "init-db":
        local_demo = settings.mode == "demo" and settings.database_url.startswith("sqlite:")
        if not local_demo:
            config = Config(str(ROOT / "alembic.ini"))
            config.set_main_option("script_location", str(ROOT / "migrations"))
            config.set_main_option("sqlalchemy.url", settings.database_url.replace("%", "%%"))
            command.upgrade(config, "head")
        engine, _ = prepare_database(settings, create_schema=local_demo)
        engine.dispose()
        print("Database schema ready and local inventory initialized. No remote host was contacted.")
        return
    from srs.worker import Worker
    _, factory = open_ready_database(settings)
    # Scheduler has no host credentials and does not use a transport.
    service = Worker(settings, factory, credentials_file=getattr(args, "credentials_file", None))
    if args.command == "scheduler":
        if args.once:
            service.trigger_schedules()
        else:
            asyncio.run(service.schedule())
    elif args.once:
        asyncio.run(service.run_once())
    else:
        asyncio.run(service.run())


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        pass
