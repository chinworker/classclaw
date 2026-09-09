from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from dataclasses import replace

import pytest
from sqlalchemy import select

from app import database, usage_database
from app.config import settings
from app.models.entities import ClassRoom
from app.models.usage import AiUsageRecord, UsageBase
from scripts import backup, restore


@pytest.fixture
def storage(tmp_path, usage_store, monkeypatch):
    core = database.build_engine(f"sqlite:///{tmp_path / 'core.db'}")
    database.Base.metadata.create_all(core)
    configured = replace(
        settings, database_url=str(core.url), usage_database_url=str(usage_store.engine.url),
        attachment_dir=tmp_path / "attachments",
    )
    monkeypatch.setattr(backup, "settings", configured)
    monkeypatch.setattr(restore, "settings", configured)
    configured.attachment_dir.mkdir()
    (configured.attachment_dir / "test.txt").write_text("original", encoding="utf-8")
    with core.begin() as connection:
        connection.execute(ClassRoom.__table__.insert(), {"id": "class-1", "name": "原始班级", "grade": "高一"})
    with usage_store.writer() as session:
        session.add(AiUsageRecord(operation="web-chat", total_tokens=23))
        session.commit()
    try:
        yield configured, core, usage_store
    finally:
        core.dispose()


def test_dual_database_backup_and_restore_including_wal(storage, tmp_path):
    configured, core, usage_store = storage
    directory = backup.backup(tmp_path / "backups")
    manifest = json.loads((directory / "manifest.json").read_text())
    assert manifest["databases"] == {"core": "classclaw.db", "usage": "usage.db"}
    with core.begin() as connection:
        connection.execute(ClassRoom.__table__.update().values(name="新班级"))
    with usage_store.writer() as session:
        session.execute(AiUsageRecord.__table__.update().values(total_tokens=900))
        session.commit()
    (configured.attachment_dir / "test.txt").write_text("changed", encoding="utf-8")
    restore.restore(directory)
    with core.connect() as connection:
        assert connection.scalar(select(ClassRoom.name)) == "原始班级"
    with usage_store.reader() as session:
        assert session.scalar(select(AiUsageRecord.total_tokens)) == 23
    assert (configured.attachment_dir / "test.txt").read_text() == "original"


@pytest.mark.parametrize("damage", ["missing", "corrupt"])
def test_invalid_usage_backup_does_not_overwrite_core(storage, tmp_path, damage):
    _configured, core, _usage_store = storage
    directory = backup.backup(tmp_path / "backups")
    if damage == "missing":
        (directory / "usage.db").rename(directory / "usage.saved")
    else:
        (directory / "usage.db").write_bytes(b"not a SQLite database")
    with core.begin() as connection:
        connection.execute(ClassRoom.__table__.update().values(name="必须保留"))
    with pytest.raises((RuntimeError, sqlite3.DatabaseError)):
        restore.restore(directory)
    with core.connect() as connection:
        assert connection.scalar(select(ClassRoom.name)) == "必须保留"


def test_legacy_backup_resets_current_usage_and_migrates_only_legacy_rows(storage, tmp_path, monkeypatch):
    configured, core, usage_store = storage
    UsageBase.metadata.create_all(core)
    with core.begin() as connection:
        connection.execute(AiUsageRecord.__table__.insert(), {"id": "old-usage", "operation": "event", "total_tokens": 41})
    monkeypatch.setattr(backup, "settings", replace(configured, usage_database_url=f"sqlite:///{tmp_path / 'not-created.db'}"))
    directory = backup.backup(tmp_path / "backups")
    assert not (directory / "usage.db").exists()
    # Backups made by the old script have no manifest.
    (directory / "manifest.json").rename(directory / "manifest.saved")
    restore.restore(directory)
    assert usage_database.initialize_usage_database(core, usage_store) == 1
    with usage_store.reader() as session:
        assert list(session.scalars(select(AiUsageRecord.total_tokens))) == [41]


def test_missing_live_usage_cannot_silently_create_empty_backup(storage, tmp_path, monkeypatch):
    configured, _core, _store = storage
    absent = tmp_path / "missing-usage.db"
    monkeypatch.setattr(backup, "settings", replace(configured, usage_database_url=f"sqlite:///{absent}"))
    with pytest.raises(RuntimeError, match="用量库缺失"):
        backup.backup(tmp_path / "backups")
    assert not absent.exists()


def test_sqlite_snapshot_does_not_wait_forever_on_locked_destination(tmp_path, monkeypatch):
    source, target = tmp_path / "source.db", tmp_path / "target.db"
    with closing(sqlite3.connect(source)) as connection:
        connection.execute("CREATE TABLE test (id INTEGER)")
    with closing(sqlite3.connect(target)) as connection:
        connection.execute("CREATE TABLE test (id INTEGER)")
        connection.execute("BEGIN IMMEDIATE")
        times = iter([0, 31, 32])
        monkeypatch.setattr(backup.time, "monotonic", lambda: next(times))
        with pytest.raises(TimeoutError, match="超时"):
            backup.sqlite_snapshot(source, target)
