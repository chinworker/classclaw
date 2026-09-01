from __future__ import annotations

import threading
from collections.abc import Generator
from contextlib import contextmanager
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


def build_engine(url: str | None = None, *, single_connection: bool = False) -> Engine:
    db_url = url or settings.database_url
    path = _sqlite_path(db_url)
    if path:
        path.parent.mkdir(parents=True, exist_ok=True)
    kwargs: dict = {"pool_pre_ping": True}
    if db_url.startswith("sqlite"):
        kwargs["connect_args"] = {"check_same_thread": False, "timeout": 5}
        if db_url.endswith(":memory:"):
            kwargs["poolclass"] = StaticPool
    if single_connection:
        kwargs["poolclass"] = QueuePool
        kwargs["pool_size"] = 1
        kwargs["max_overflow"] = 0
        kwargs["pool_timeout"] = 30
    engine = create_engine(db_url, **kwargs)
    if db_url.startswith("sqlite"):

        @event.listens_for(engine, "connect")
        def set_sqlite_pragmas(dbapi_connection, _connection_record) -> None:  # type: ignore[no-untyped-def]
            cursor = dbapi_connection.cursor()
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.execute("PRAGMA busy_timeout=5000")
            if not db_url.endswith(":memory:"):
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

_READ_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})


@contextmanager
def writer_session() -> Generator[Session, None, None]:
    """Open a re-entrant writer session for synchronous non-request work."""
    stack = getattr(_writer_stack, "stack", None)
    if stack:
        yield stack[-1]
        return
    with _write_lock:
        session = _write_sessionmaker()
        _writer_stack.stack = [session]
        try:
            yield session
        finally:
            _writer_stack.stack = []
            session.close()


@contextmanager
def request_writer_session() -> Generator[Session, None, None]:
    """Open a single-writer request session without thread-local state."""
    _write_lock.acquire()
    session: Session | None = None
    try:
        session = _write_sessionmaker()
        yield session
    finally:
        if session is not None:
            session.close()
        _write_lock.release()


@contextmanager
def reader_session() -> Generator[Session, None, None]:
    session = _read_sessionmaker()
    try:
        yield session
    finally:
        session.close()


def get_db(request: Request) -> Generator[Session, None, None]:
    if request.method in _READ_METHODS:
        with reader_session() as db:
            yield db
    else:
        with request_writer_session() as db:
            yield db


def init_db() -> None:
    from app.models import entities  # noqa: F401

    Base.metadata.create_all(bind=write_engine)
