from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base, get_db
from app.config import settings
from app.main import app
from app.models import entities  # noqa: F401
from app.models.entities import ClassRoom, Student
from app.services import openclaw_bridge


@pytest.fixture()
def db():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)

    @event.listens_for(engine, "connect")
    def pragmas(connection, _record):
        connection.execute("PRAGMA foreign_keys=ON")

    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine, expire_on_commit=False, class_=Session)()
    try:
        yield session
    finally:
        session.close()
        Base.metadata.drop_all(engine)


@pytest.fixture()
def sample(db: Session):
    cls = ClassRoom(name="测试班", grade="高一", room="101", head_teacher="李老师")
    other = ClassRoom(name="其他班", grade="高一")
    db.add_all([cls, other])
    db.flush()
    students = [
        Student(class_id=cls.id, student_no="001", name="张三", tags=[], status="active"),
        Student(class_id=cls.id, student_no="002", name="李四", tags=[], status="active"),
        Student(class_id=cls.id, student_no="003", name="王五", tags=[], status="active"),
        Student(class_id=other.id, student_no="001", name="外班生", tags=[], status="active"),
    ]
    db.add_all(students)
    db.commit()
    return cls, other, students


@pytest.fixture()
def client(db: Session, monkeypatch):
    def override():
        yield db

    async def connected(force: bool = False):
        return {"ready": True, "gateway_live": True, "plugin_ready": True, "gateway_url": "http://openclaw.test", "agent_id": "main"}

    async def extractor_off() -> bool:
        return False

    monkeypatch.setattr(openclaw_bridge, "connection_status", connected)
    monkeypatch.setattr(openclaw_bridge, "ensure_extractor_agent", extractor_off)
    app.dependency_overrides[get_db] = override
    headers = {"X-ClassClaw-Surface": "web"}
    if settings.api_token:
        headers["Authorization"] = f"Bearer {settings.api_token}"
    with TestClient(app, headers=headers) as value:
        yield value
    app.dependency_overrides.clear()
