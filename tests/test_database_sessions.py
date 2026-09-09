from __future__ import annotations

import threading
import time
from collections.abc import Generator
from contextlib import contextmanager
from types import SimpleNamespace

from fastapi import Request
from sqlalchemy.orm import Session, sessionmaker

import app.database as database
from app.database import build_engine, get_db, reader_session, request_writer_session, writer_session


def _memory_sessionmaker() -> sessionmaker:
    engine = build_engine("sqlite://")
    return sessionmaker(bind=engine, autoflush=False, expire_on_commit=False, class_=Session)


def test_writer_session_reenters_same_session(monkeypatch):
    monkeypatch.setattr(database, "_write_sessionmaker", _memory_sessionmaker())
    with writer_session() as outer:
        with writer_session() as inner:
            assert outer is inner


def test_writer_sessions_serialize_across_threads(monkeypatch):
    monkeypatch.setattr(database, "_write_sessionmaker", _memory_sessionmaker())
    order: list[str] = []

    def worker() -> None:
        with writer_session():
            order.append("enter")
            time.sleep(0.05)
            order.append("exit")

    thread = threading.Thread(target=worker)
    thread.start()
    time.sleep(0.01)
    with writer_session():
        order.append("main-enter")
    thread.join()
    assert order == ["enter", "exit", "main-enter"]


def test_reader_sessions_are_independent(monkeypatch):
    monkeypatch.setattr(database, "_read_sessionmaker", _memory_sessionmaker())
    with reader_session() as first:
        with reader_session() as second:
            assert first is not second


def test_request_writer_can_close_on_another_thread(monkeypatch):
    monkeypatch.setattr(database, "_write_sessionmaker", _memory_sessionmaker())
    generator = request_writer_session()

    thread = threading.Thread(target=lambda: generator.__enter__())
    thread.start()
    thread.join()

    generator.__exit__(None, None, None)
    assert database._write_lock.acquire(timeout=0.1)
    database._write_lock.release()


def test_get_db_routes_by_http_method(monkeypatch):
    used: list[str] = []
    reader = SimpleNamespace(info={})
    writer = SimpleNamespace(info={})

    @contextmanager
    def fake_reader() -> Generator:
        used.append("read")
        yield reader

    @contextmanager
    def fake_writer() -> Generator:
        used.append("write")
        yield writer

    monkeypatch.setattr(database, "reader_session", fake_reader)
    monkeypatch.setattr(database, "request_writer_session", fake_writer)
    assert next(get_db(Request({"type": "http", "method": "GET"}))) is reader
    assert next(get_db(Request({"type": "http", "method": "POST"}))) is writer
    assert next(get_db(Request({"type": "http", "method": "DELETE"}))) is writer
    assert used == ["read", "write", "write"]
