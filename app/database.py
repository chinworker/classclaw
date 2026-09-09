from __future__ import annotations

import asyncio
import threading
from collections.abc import AsyncIterator, Generator
from contextlib import asynccontextmanager, contextmanager
from dataclasses import dataclass
from pathlib import Path

from fastapi import Request
from sqlalchemy import Engine, create_engine, event
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker
from sqlalchemy.pool import QueuePool, StaticPool

from app.config import settings


class Base(DeclarativeBase):
    pass


def _sqlite_path(url: str) -> Path | None:
    prefix = "sqlite:///"
    if url.startswith(prefix) and not url.endswith(":memory:"):
        return Path(url.removeprefix(prefix))
    return None


def build_engine(
    url: str | None = None, *, single_connection: bool = False, timeout_seconds: float = 5, pool_timeout_seconds: float = 30,
) -> Engine:
    db_url = url or settings.database_url
    in_memory = db_url in {"sqlite://", "sqlite:///"} or db_url.endswith(":memory:")
    path = _sqlite_path(db_url)
    if path:
        path.parent.mkdir(parents=True, exist_ok=True)
    kwargs: dict = {"pool_pre_ping": True}
    if db_url.startswith("sqlite"):
        kwargs["connect_args"] = {"check_same_thread": False, "timeout": timeout_seconds}
        if in_memory:
            kwargs["poolclass"] = StaticPool
    if single_connection:
        kwargs["poolclass"] = QueuePool
        kwargs["pool_size"] = 1
        kwargs["max_overflow"] = 0
        kwargs["pool_timeout"] = pool_timeout_seconds
    elif not in_memory:
        kwargs["pool_timeout"] = pool_timeout_seconds
    engine = create_engine(db_url, **kwargs)
    if db_url.startswith("sqlite"):

        @event.listens_for(engine, "connect")
        def set_sqlite_pragmas(dbapi_connection, _connection_record) -> None:  # type: ignore[no-untyped-def]
            cursor = dbapi_connection.cursor()
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.execute(f"PRAGMA busy_timeout={int(timeout_seconds * 1000)}")
            if not in_memory:
                cursor.execute("PRAGMA journal_mode=WAL")
                cursor.execute("PRAGMA synchronous=NORMAL")
                cursor.execute("PRAGMA wal_autocheckpoint=1000")
            cursor.close()

    return engine


read_engine = build_engine()
write_engine = build_engine(single_connection=True)

_read_sessionmaker = sessionmaker(bind=read_engine, autoflush=False, expire_on_commit=False, class_=Session)
_write_sessionmaker = sessionmaker(bind=write_engine, autoflush=False, expire_on_commit=False, class_=Session)

# A plain lock may be released by a different worker thread. FastAPI enters and
# exits synchronous generator dependencies through its thread pool and does not
# guarantee thread affinity between those two calls.
_write_lock = threading.Lock()
_writer_stack = threading.local()
_LEASE_KEY = "classclaw_writer_lease"


@dataclass
class _WriterLease:
    held: bool = True
    uncommitted_writes: bool = False

    def release(self) -> None:
        if self.held:
            self.held = False
            _write_lock.release()

    async def acquire(self) -> None:
        # A blocking threading.Lock.acquire() here would freeze the event loop
        # that the current writer may itself need in order to finish.
        while not _write_lock.acquire(blocking=False):
            await asyncio.sleep(0.01)
        self.held = True


@event.listens_for(Session, "before_flush")
def _guard_flush(session, _context, _instances) -> None:
    lease = session.info.get(_LEASE_KEY)
    if lease:
        if not lease.held:
            raise RuntimeError("Database access is forbidden while the writer is suspended")
        lease.uncommitted_writes = True


@event.listens_for(Session, "do_orm_execute")
def _guard_execute(state) -> None:
    lease = state.session.info.get(_LEASE_KEY)
    if lease:
        if not lease.held:
            raise RuntimeError("Database access is forbidden while the writer is suspended")
        if not state.is_select:
            lease.uncommitted_writes = True


@event.listens_for(Session, "after_transaction_end")
def _end_writer_transaction(session, transaction) -> None:
    lease = session.info.get(_LEASE_KEY)
    if lease and transaction.parent is None:
        lease.uncommitted_writes = False


@asynccontextmanager
async def suspend_writer(db: Session) -> AsyncIterator[None]:
    """Release a clean request writer during I/O; no database work inside this block.

    Callers must commit their preparation first and materialize I/O arguments.
    Pending writes (including already flushed SQL) are never committed implicitly.
    Injected test sessions and reader sessions have no writer lease to release.
    """
    lease = db.info.get(_LEASE_KEY)
    if lease is None:
        yield
        return
    if not lease.held or lease.uncommitted_writes or db.new or db.dirty or db.deleted:
        raise RuntimeError("Commit or roll back pending writes before suspending the writer")
    # End any read-only transaction, returning the single pooled connection too.
    db.commit()
    lease.release()
    try:
        yield
    finally:
        async def restore() -> None:
            await lease.acquire()
            # Re-read draft revisions and other facts changed during I/O.
            db.expire_all()

        # Restore even when a cancellation arrives during reacquisition: caller
        # error handlers may need to persist a failure/cancellation status.
        task = asyncio.create_task(restore())
        interrupted = False
        while not task.done():
            try:
                await asyncio.shield(task)
            except asyncio.CancelledError:
                interrupted = True
        task.result()
        if interrupted:
            raise asyncio.CancelledError


_READ_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})
_READ_ONLY_ROUTE_NAMES = frozenset({"ai_task_cancel", "openclaw_config_update", "openclaw_sessions_cleanup"})


@contextmanager
def writer_session() -> Generator[Session, None, None]:
    """Open a re-entrant writer session for synchronous non-request work."""
    stack = getattr(_writer_stack, "stack", None)
    if stack:
        yield stack[-1]
        return
    with request_writer_session() as session:
        _writer_stack.stack = [session]
        try:
            yield session
        finally:
            _writer_stack.stack = []


@contextmanager
def request_writer_session() -> Generator[Session, None, None]:
    """Open a single-writer request session without thread-local state."""
    _write_lock.acquire()
    lease = _WriterLease()
    session: Session | None = None
    try:
        session = _write_sessionmaker()
        session.info[_LEASE_KEY] = lease
        yield session
    finally:
        try:
            if session is not None:
                session.close()
        finally:
            lease.release()


@contextmanager
def reader_session() -> Generator[Session, None, None]:
    session = _read_sessionmaker()
    try:
        yield session
    finally:
        session.close()


def get_db(request: Request) -> Generator[Session, None, None]:
    route = request.scope.get("route")
    route_name = getattr(route, "name", None)
    if request.method in _READ_METHODS or route_name in _READ_ONLY_ROUTE_NAMES:
        with reader_session() as db:
            db.info["request_id"] = getattr(request.state, "request_id", None)
            yield db
    else:
        with request_writer_session() as db:
            db.info["request_id"] = getattr(request.state, "request_id", None)
            yield db


def init_db(*, strict_usage: bool = False) -> None:
    from app.models import entities  # noqa: F401

    Base.metadata.create_all(bind=write_engine)
    from app.usage_database import initialize_usage_database

    try:
        initialize_usage_database(write_engine)
    except Exception as exc:
        if strict_usage:
            raise
        # Usage is optional telemetry. Preserve the legacy table on migration
        # failure and keep deterministic business endpoints available.
        import logging

        logging.getLogger(__name__).error("Usage database initialization failed: %s", type(exc).__name__)
