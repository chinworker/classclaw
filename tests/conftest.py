from __future__ import annotations

import sys
from contextlib import contextmanager
from dataclasses import replace

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session, sessionmaker

from app import usage_database
from app.config import settings
from app.database import Base, build_engine, get_db
from app.main import app
from app.models import entities  # noqa: F401
from app.models.entities import ClassRoom, Student
from app.services import openclaw_bridge


@pytest.fixture(autouse=True)
def isolated_files(tmp_path, monkeypatch):
    """All imported Settings aliases must point at disposable filesystem roots."""
    original = settings
    isolated = replace(original, attachment_dir=tmp_path / "attachments",
                       openclaw_class_workspace_root=tmp_path / "workspaces",
                       openclaw_state_dir=tmp_path / "openclaw", log_file=tmp_path / "logs" / "test.log")
    for module in list(sys.modules.values()):
        if module and getattr(module, "settings", None) is original:
            monkeypatch.setattr(module, "settings", isolated)
    yield isolated


@pytest.fixture(autouse=True)
def usage_store(tmp_path, monkeypatch):
    store = usage_database.UsageStore(f"sqlite:///{tmp_path / 'usage.db'}")
    usage_database.UsageBase.metadata.create_all(store.engine)
    store.ready = True
    monkeypatch.setattr(usage_database, "get_usage_store", lambda: store)
    try:
        yield store
    finally:
        store.dispose()


@pytest.fixture()
def deletion_gateway(client, monkeypatch):
    """Fake Gateway admin RPC and private WeChat transport for deletion flows.

    Depends on ``client`` so the conftest no-real-WeChat guard is installed first.
    """
    calls: list[tuple[str, dict | None]] = []

    async def fake_admin_rpc(method: str, params: dict | None = None):
        calls.append((method, params))
        if method == "config.get":
            return {"hash": "deletion-hash", "config": {
                "agents": {"list": []},
                "bindings": [],
                "plugins": {"entries": {"classclaw": {"config": {"agentClasses": {}}}}},
            }}
        if method == "tasks.list":
            return {"tasks": []}
        if method == "channels.logout":
            return {"cleared": True, "loggedOut": True}
        if method == "config.patch":
            return {"ok": True}
        raise AssertionError(method)

    async def cancel_wechat(class_id: str, action: str, **params):
        return {"cancelled": True, "accountIds": []}

    monkeypatch.setattr("app.services.openclaw_provisioning.admin_rpc", fake_admin_rpc)
    monkeypatch.setattr("app.services.wechat_login.call", cancel_wechat)
    return calls


@pytest.fixture()
def usage_db(usage_store):
    with usage_store.reader() as session:
        yield session


@pytest.fixture()
def db():
    engine = build_engine("sqlite://")

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

    async def no_runtime_sync():
        return None

    async def no_real_wechat(*_args, **_kwargs):
        raise AssertionError("Tests must mock the private WeChat login transport")

    # TestClient runs the lifespan too: never initialize/migrate real local data.
    @contextmanager
    def startup_session():
        yield db

    monkeypatch.setattr("app.main.init_db", lambda: None)
    monkeypatch.setattr("app.main.writer_session", startup_session)
    monkeypatch.setattr("app.services.openclaw_provisioning.sync_class_agent_thinking_defaults", no_runtime_sync)
    monkeypatch.setattr("app.services.wechat_login.call", no_real_wechat)
    monkeypatch.setattr(openclaw_bridge, "connection_status", connected)
    monkeypatch.setattr(openclaw_bridge, "ensure_extractor_agent", extractor_off)
    app.dependency_overrides[get_db] = override
    headers = {"X-ClassClaw-Surface": "web"}
    if settings.api_token:
        headers["Authorization"] = f"Bearer {settings.api_token}"
    with TestClient(app, headers=headers) as value:
        yield value
    app.dependency_overrides.pop(get_db, None)
