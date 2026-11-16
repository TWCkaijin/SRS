from concurrent.futures import ThreadPoolExecutor
from contextlib import nullcontext
from threading import Event
from types import SimpleNamespace
from urllib.error import URLError

import pytest
from sqlalchemy import func, select
from srs import database
from srs.app import create_app
from srs.config import LOCAL_INVENTORY, Settings
from srs.models import Node, Project

from scripts import dev


def settings_at(tmp_path):
    return Settings(_env_file=None, data_dir=tmp_path, database_url=f"sqlite:///{tmp_path / 'fresh.sqlite3'}")


def test_api_and_workers_cannot_use_uncommitted_seed(tmp_path, monkeypatch):
    settings = settings_at(tmp_path)
    staged = Event()
    release = Event()
    real_seed = database.seed

    def slow_seed(db, config):
        real_seed(db, config)
        db.flush()
        staged.set()
        assert release.wait(5)

    monkeypatch.setattr(database, "seed", slow_seed)
    with ThreadPoolExecutor() as executor:
        result = executor.submit(create_app, settings)
        try:
            assert staged.wait(3)
            assert not result.done()
            with pytest.raises(RuntimeError, match="Database schema is not ready"):
                database.open_ready_database(settings)
        finally:
            release.set()
        app = result.result(timeout=5)
    engine, factory = database.open_ready_database(settings)
    try:
        with factory() as db:
            assert db.scalar(select(func.count()).select_from(Node)) == 3
            assert db.scalar(select(func.count()).select_from(Project)) == 1
    finally:
        engine.dispose()
        app.state.engine.dispose()


def test_fresh_checkout_without_local_inventory_has_no_hosts(tmp_path, monkeypatch):
    settings = settings_at(tmp_path)
    local_file = tmp_path / LOCAL_INVENTORY.name
    monkeypatch.setattr("srs.seed.LOCAL_INVENTORY", local_file)
    settings.inventory_file = local_file
    engine, factory = database.prepare_database(settings, create_schema=True)
    try:
        with factory() as db:
            assert db.scalar(select(func.count()).select_from(Node)) == 0
            assert db.scalar(select(func.count()).select_from(Project)) == 1
    finally:
        engine.dispose()


def test_bad_inventory_rolls_back_demo_initialization(tmp_path):
    settings = settings_at(tmp_path)
    settings.inventory_file = tmp_path / "missing-custom.yaml"
    with pytest.raises(FileNotFoundError):
        database.prepare_database(settings, create_schema=True)
    with pytest.raises(RuntimeError, match="Database schema is not ready"):
        database.open_ready_database(settings)


def test_launcher_waits_for_successful_health_response(monkeypatch):
    responses = iter((URLError("starting"), nullcontext(SimpleNamespace(status=503)),
                      nullcontext(SimpleNamespace(status=200))))
    visits = []

    def open_health(url, *, timeout):
        visits.append(url)
        response = next(responses)
        if isinstance(response, Exception):
            raise response
        return response

    monkeypatch.setattr(dev, "build_opener", lambda _: SimpleNamespace(open=open_health))
    monkeypatch.setattr(dev.time, "sleep", lambda _: None)
    process = SimpleNamespace(poll=lambda: None)
    dev.wait_for_api(process)
    assert visits == [f"{dev.BASE_URL}/api/v1/health"] * 3
    with pytest.raises(SystemExit, match="API exited"):
        dev.wait_for_api(SimpleNamespace(poll=lambda: 1))
