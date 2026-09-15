from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor

import pytest
from alembic.config import Config
from sqlalchemy import func, inspect, select

from alembic import command
from app import database, usage_database
from app.config import ConfigurationError, load_settings, settings
from app.models.entities import ClassRoom
from app.models.usage import AiUsageRecord, UsageBase
from app.services import usage
from app.utils.time import now


@pytest.fixture
def legacy_engine(tmp_path):
    engine = database.build_engine(f"sqlite:///{tmp_path / 'core.db'}")
    database.Base.metadata.create_all(engine)
    UsageBase.metadata.create_all(engine)
    try:
        yield engine
    finally:
        engine.dispose()


def _seed(engine, count=1, *, tokens=12):
    with engine.begin() as connection:
        connection.execute(AiUsageRecord.__table__.insert(), [
            {"id": f"record-{index:05d}", "operation": "web-chat", "total_tokens": tokens, "created_at": now()}
            for index in range(count)
        ])


def test_migration_preserves_every_column_and_batches(legacy_engine, usage_store):
    _seed(legacy_engine, 1003)
    with legacy_engine.connect() as connection:
        original = connection.execute(select(AiUsageRecord.__table__).order_by(AiUsageRecord.id)).all()
    assert usage_database.initialize_usage_database(legacy_engine, usage_store) == 1003
    assert "ai_usage_records" not in inspect(legacy_engine).get_table_names()
    assert "users" in inspect(legacy_engine).get_table_names()
    assert inspect(usage_store.engine).get_table_names() == ["ai_usage_records"]
    with usage_store.engine.connect() as connection:
        assert connection.execute(select(AiUsageRecord.__table__).order_by(AiUsageRecord.id)).all() == original
    assert usage_database.initialize_usage_database(legacy_engine, usage_store) == 0


def test_interrupted_migration_can_retry_without_duplicates(legacy_engine, usage_store):
    _seed(legacy_engine, 3)
    with legacy_engine.connect() as core:
        transaction = core.begin()
        assert usage_database.migrate_legacy_usage(core, usage_store.engine) == 3
        # Simulate source commit failure after the target has committed.
        transaction.rollback()
    assert "ai_usage_records" in inspect(legacy_engine).get_table_names()
    assert usage_database.initialize_usage_database(legacy_engine, usage_store) == 3
    with usage_store.reader() as session:
        assert session.scalar(select(func.count()).select_from(AiUsageRecord)) == 3


def test_conflicting_id_preserves_source_and_disables_incomplete_stats(legacy_engine, usage_store):
    _seed(legacy_engine)
    _seed(usage_store.engine, tokens=999)
    with pytest.raises(RuntimeError, match="verification failed"):
        usage_database.initialize_usage_database(legacy_engine, usage_store)
    assert usage_store.ready is False
    with legacy_engine.connect() as core:
        assert core.scalar(select(AiUsageRecord.total_tokens)) == 12
    with usage_store.engine.connect() as target:
        assert target.scalar(select(AiUsageRecord.total_tokens)) == 999


def test_usage_write_does_not_wait_for_core_lock(usage_db):
    with database._write_lock, ThreadPoolExecutor() as workers:
        workers.submit(usage.record_openclaw_usage, {"usage": {"total_tokens": 7}}, user="test", model="test").result(timeout=2)
    assert usage_db.scalar(select(AiUsageRecord.total_tokens)) == 7


@pytest.mark.parametrize("unavailable", ["sqlite_locked", "writer_locked", "uninitialized"])
def test_usage_failure_is_bounded_and_preserves_core_transaction(db, usage_store, caplog, unavailable):
    cls = ClassRoom(name="业务待提交", grade="高一")
    db.add(cls)
    connection = usage_store.engine.connect()
    if unavailable == "sqlite_locked":
        connection.exec_driver_sql("BEGIN IMMEDIATE")
    elif unavailable == "writer_locked":
        usage_store._lock.acquire()
    else:
        usage_store.ready = False
    try:
        started = time.monotonic()
        usage.record_openclaw_usage({"usage": {"total_tokens": 7}}, user="test", model="test")
        assert time.monotonic() - started < 2
        assert cls in db.new
        assert "AI usage record skipped" in caplog.text
        db.commit()
        assert db.get(ClassRoom, cls.id) is cls
    finally:
        connection.close()
        if unavailable == "writer_locked":
            usage_store._lock.release()


def test_stats_and_admin_browser_use_separate_database(client, db, usage_db, usage_store):
    usage_db.add(AiUsageRecord(operation="web-chat", total_tokens=19, created_at=now().replace(hour=0, minute=0, second=0)))
    usage_db.commit()
    assert "ai_usage_records" not in inspect(db.bind).get_table_names()
    response = client.get("/api/v1/admin/usage?days=1")
    assert response.status_code == 200
    assert response.json()["data"]["totals"]["total_tokens"] == 19
    overview = client.get("/api/v1/admin/database/overview").json()["data"]
    assert next(row for row in overview["tables"] if row["name"] == "ai_usage_records") == {
        "name": "ai_usage_records", "database": "usage", "row_count": 1,
    }
    data = client.get("/api/v1/admin/database/tables/ai_usage_records?limit=1").json()["data"]
    assert data["database"] == "usage" and data["items"][0]["total_tokens"] == 19
    assert client.get("/api/v1/admin/database/tables/not_a_table").status_code == 404
    usage_store.ready = False
    assert client.get("/api/v1/admin/usage").json()["error"]["code"] == "USAGE_DATABASE_UNAVAILABLE"
    assert client.get("/api/v1/admin/database/tables/ai_usage_records").status_code == 503
    assert client.get("/api/v1/admin/database/tables/users").status_code == 200
    overview = client.get("/api/v1/admin/database/overview").json()["data"]
    assert next(row for row in overview["tables"] if row["name"] == "ai_usage_records")["error"] == "USAGE_DATABASE_UNAVAILABLE"


def test_database_overview_survives_usage_sql_error(client, monkeypatch):
    from contextlib import contextmanager

    from sqlalchemy.exc import SQLAlchemyError

    @contextmanager
    def broken_reader():
        raise SQLAlchemyError("disk I/O error")
        yield

    monkeypatch.setattr(usage_database, "reader_session", broken_reader)
    response = client.get("/api/v1/admin/database/overview")
    assert response.status_code == 200
    tables = response.json()["data"]["tables"]
    assert any(row["database"] == "core" and row["row_count"] is not None for row in tables)
    assert next(row for row in tables if row["name"] == "ai_usage_records")["error"] == "SQLAlchemyError"


def test_core_startup_survives_usage_migration_failure(legacy_engine, usage_store, monkeypatch, caplog):
    _seed(legacy_engine)
    _seed(usage_store.engine, tokens=99)
    monkeypatch.setattr(database, "write_engine", legacy_engine)
    database.init_db()
    assert "Usage database initialization failed" in caplog.text
    assert "ai_usage_records" in inspect(legacy_engine).get_table_names()
    assert "users" in inspect(legacy_engine).get_table_names()


def test_alembic_upgrade_downgrade_and_upgrade(tmp_path):
    config = Config("alembic.ini")
    config.attributes["database_url"] = f"sqlite:///{tmp_path / 'migration-core.db'}"
    config.attributes["usage_database_url"] = f"sqlite:///{tmp_path / 'migration-usage.db'}"
    command.upgrade(config, "0013")
    engine = database.build_engine(config.attributes["database_url"])
    try:
        _seed(engine, 2)
        command.upgrade(config, "head")
        assert "ai_usage_records" not in inspect(engine).get_table_names()
        command.downgrade(config, "0013")
        with engine.connect() as connection:
            assert connection.scalar(select(func.count()).select_from(AiUsageRecord)) == 2
        command.upgrade(config, "head")
        assert "ai_usage_records" not in inspect(engine).get_table_names()
    finally:
        engine.dispose()


def test_usage_configuration_paths_and_same_file_rejection(tmp_path):
    config = tmp_path / "classclaw.toml"
    config.write_text('[storage]\nusage_database_url = "sqlite:///statistics/usage.db"\n', encoding="utf-8")
    loaded = load_settings(config, environ={})
    assert loaded.usage_database_url == f"sqlite:///{tmp_path / 'statistics' / 'usage.db'}"
    override = f"sqlite:///{tmp_path / 'override.db'}"
    assert load_settings(config, environ={"CLASSCLAW_USAGE_DATABASE_URL": override}).usage_database_url == override
    with pytest.raises(ConfigurationError, match="不同文件"):
        load_settings(config, environ={"CLASSCLAW_USAGE_DATABASE_URL": override, "CLASSCLAW_DATABASE_URL": override})
    with pytest.raises(ConfigurationError, match="不能包含用量数据库"):
        load_settings(config, environ={"CLASSCLAW_USAGE_DATABASE_URL": f"sqlite:///{settings.attachment_dir / 'usage.db'}"})
    with pytest.raises(ConfigurationError, match="查询参数"):
        load_settings(config, environ={"CLASSCLAW_USAGE_DATABASE_URL": f"{override}?timeout=1"})


def test_migration_cannot_drop_source_when_target_points_to_same_file(legacy_engine):
    _seed(legacy_engine)
    with legacy_engine.begin() as connection:
        with pytest.raises(RuntimeError, match="different file"):
            usage_database.migrate_legacy_usage(connection, legacy_engine)
        assert connection.scalar(select(AiUsageRecord.total_tokens)) == 12
