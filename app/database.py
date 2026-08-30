from __future__ import annotations

from collections.abc import Generator
from pathlib import Path

from sqlalchemy import Engine, event
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker
from sqlalchemy import create_engine

from app.config import settings


class Base(DeclarativeBase):
    pass


def _sqlite_path(url: str) -> Path | None:
    prefix = "sqlite:///"
    if url.startswith(prefix) and not url.endswith(":memory:"):
        return Path(url.removeprefix(prefix))
    return None


def build_engine(url: str | None = None) -> Engine:
    db_url = url or settings.database_url
    path = _sqlite_path(db_url)
    if path:
        path.parent.mkdir(parents=True, exist_ok=True)
    engine = create_engine(
        db_url,
        connect_args={"check_same_thread": False, "timeout": 5} if db_url.startswith("sqlite") else {},
        pool_pre_ping=True,
    )
    if db_url.startswith("sqlite"):
        @event.listens_for(engine, "connect")
        def set_sqlite_pragmas(dbapi_connection, _connection_record) -> None:  # type: ignore[no-untyped-def]
            cursor = dbapi_connection.cursor()
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.execute("PRAGMA busy_timeout=5000")
            if not db_url.endswith(":memory:"):
                cursor.execute("PRAGMA journal_mode=WAL")
            cursor.close()
    return engine


engine = build_engine()
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False, class_=Session)


def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_db() -> None:
    from app.models import entities  # noqa: F401

    Base.metadata.create_all(bind=engine)

