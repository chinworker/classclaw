from __future__ import annotations

import logging
import threading
from collections.abc import Generator
from contextlib import contextmanager
from functools import lru_cache
from pathlib import Path

from sqlalchemy import Connection, Engine, MetaData, Table, inspect, select
from sqlalchemy.dialects.sqlite import insert
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker

from app.config import settings
from app.core.errors import AppError
from app.database import build_engine
from app.models.usage import UsageBase

logger = logging.getLogger(__name__)


class UsageStore:
    """Independent, bounded-wait single writer; never takes the business writer lock."""

    def __init__(self, url: str):
        self.engine = build_engine(url, timeout_seconds=0.25, pool_timeout_seconds=0.25)
        self.write_engine = (
            self.engine if url.endswith(":memory:")
            else build_engine(url, single_connection=True, timeout_seconds=0.25, pool_timeout_seconds=0.25)
        )
        self._read_sessions = sessionmaker(bind=self.engine, autoflush=False, expire_on_commit=False)
        self._write_sessions = sessionmaker(bind=self.write_engine, autoflush=False, expire_on_commit=False)
        self._lock = threading.Lock()
        self.ready = False

    def _check_ready(self) -> None:
        if not self.ready:
            raise RuntimeError("Usage database initialization or migration has not completed")

    @contextmanager
    def reader(self) -> Generator[Session, None, None]:
        try:
            self._check_ready()
            with self._read_sessions() as session:
                yield session
        except (RuntimeError, OSError, SQLAlchemyError) as exc:
            raise unavailable() from exc

    @contextmanager
    def writer(self) -> Generator[Session, None, None]:
        self._check_ready()
        if not self._lock.acquire(timeout=0.25):
            raise RuntimeError("Usage database writer is busy")
        try:
            with self._write_sessions() as session:
                yield session
        finally:
            self._lock.release()

    def dispose(self) -> None:
        self.engine.dispose()
        self.write_engine.dispose()


def unavailable() -> AppError:
    return AppError("USAGE_DATABASE_UNAVAILABLE", "用量统计库暂不可用，业务功能不受影响；请检查统计库权限或迁移日志", 503)


@lru_cache(maxsize=1)
def get_usage_store() -> UsageStore:
    return UsageStore(settings.usage_database_url)


@contextmanager
def reader_session() -> Generator[Session, None, None]:
    try:
        store = get_usage_store()
    except Exception as exc:
        raise unavailable() from exc
    with store.reader() as session:
        yield session


@contextmanager
def writer_session() -> Generator[Session, None, None]:
    with get_usage_store().writer() as session:
        yield session


def copy_verified(source: Connection, destination: Connection) -> int:
    """Idempotently copy immutable rows, rejecting ID collisions with different data."""
    source_table = Table("ai_usage_records", MetaData(), autoload_with=source)
    target_table = Table("ai_usage_records", MetaData(), autoload_with=destination)
    if set(source_table.c.keys()) != set(target_table.c.keys()):
        raise RuntimeError("Usage database columns do not match; original data was preserved")
    last_id = None
    copied = 0
    while True:
        statement = select(source_table).order_by(source_table.c.id).limit(500)
        if last_id is not None:
            statement = statement.where(source_table.c.id > last_id)
        rows = [dict(row) for row in source.execute(statement).mappings()]
        if not rows:
            break
        destination.execute(insert(target_table).on_conflict_do_nothing(index_elements=["id"]), rows)
        stored = {
            row["id"]: dict(row)
            for row in destination.execute(select(target_table).where(target_table.c.id.in_([row["id"] for row in rows]))).mappings()
        }
        if any(stored.get(row["id"]) != row for row in rows):
            raise RuntimeError("Usage migration verification failed; original data was preserved")
        copied += len(rows)
        last_id = rows[-1]["id"]
    return copied


def migrate_legacy_usage(core: Connection, usage_engine: Engine) -> int:
    """Target commit precedes source removal, so an interrupted migration is retryable.

    Run with the old server stopped and a transaction on the core connection.
    This is also called by Alembic 0014 for an explicit, versioned schema upgrade.
    """
    core_path, usage_path = core.engine.url.database, usage_engine.url.database
    if core_path and usage_path and core_path != ":memory:" and usage_path != ":memory:":
        source, target = Path(core_path).resolve(), Path(usage_path).resolve()
        if source == target or (source.exists() and target.exists() and source.samefile(target)):
            raise RuntimeError("Usage database must be a different file from the core database")
    UsageBase.metadata.create_all(usage_engine)
    if "ai_usage_records" not in inspect(core).get_table_names():
        return 0
    # Start a real SQLite write transaction before reading (pysqlite legacy mode
    # does not BEGIN for SELECT/DDL). Protect the copy/drop against other writers.
    core.exec_driver_sql("UPDATE ai_usage_records SET id = id WHERE 0")
    with usage_engine.begin() as target:
        count = copy_verified(core, target)
    core.exec_driver_sql("DROP TABLE ai_usage_records")
    return count


def initialize_usage_database(core_engine: Engine, store: UsageStore | None = None) -> int:
    target = store or get_usage_store()
    target.ready = False
    with core_engine.begin() as core:
        count = migrate_legacy_usage(core, target.engine)
    target.ready = True
    logger.info("Usage database ready; migrated_records=%d", count)
    return count


def clear_records() -> int:
    from sqlalchemy import func

    table = UsageBase.metadata.tables["ai_usage_records"]
    try:
        with writer_session() as session:
            count = session.scalar(select(func.count()).select_from(table)) or 0
            session.execute(table.delete())
            session.commit()
            return count
    except Exception as exc:
        raise unavailable() from exc
