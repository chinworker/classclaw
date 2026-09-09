from __future__ import annotations

import os
import subprocess
import sys
from contextlib import contextmanager
from dataclasses import replace
from types import SimpleNamespace

import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import SQLAlchemyError

from app import config, database
from app.core.errors import AppError
from app.models.entities import AuditLog, ClassRoom, User
from app.schemas.auth import UserCreate
from app.services import accounts
from app.utils.time import now
from scripts import reset_admin_password as cli

OLD_PASSWORD = "Previous-test-password"
NEW_PASSWORD = "新的本地管理员密码-2026"


@pytest.fixture
def admin(db):
    user = User(
        username="custom-administrator", role="admin", display_name="现有管理员",
        password_hash=accounts.hash_password(OLD_PASSWORD), is_active=True, must_change_password=False,
    )
    db.add(user)
    db.commit()
    return user


def test_local_reset_revokes_only_admin_sessions_and_keeps_business_data(db, admin, client, usage_db):
    from app.models.usage import AiUsageRecord

    teacher = accounts.create_teacher(db, UserCreate(username="unchanged-teacher"))
    teacher_hash = teacher.password_hash
    admin_token, admin_session = accounts.issue_session(db, admin)
    teacher_token, teacher_session = accounts.issue_session(db, teacher)
    _, previously_revoked = accounts.issue_session(db, admin)
    revoked_at = now()
    previously_revoked.revoked_at = revoked_at
    cls = ClassRoom(name="保留班级", grade="高一", owner_user_id=teacher.id)
    db.add(cls)
    db.commit()
    usage_db.add(AiUsageRecord(operation="test", total_tokens=17))
    usage_db.commit()

    result = accounts.reset_admin_password_locally(db, admin_id=admin.id, new_password=NEW_PASSWORD)

    assert result.id == admin.id and result.username == "custom-administrator"
    assert result.role == "admin" and result.is_active and result.must_change_password
    assert accounts.verify_password(NEW_PASSWORD, result.password_hash)
    assert not accounts.verify_password(OLD_PASSWORD, result.password_hash)
    db.refresh(admin_session)
    assert admin_session.revoked_at is not None
    assert previously_revoked.revoked_at == revoked_at
    assert teacher_session.revoked_at is None and teacher.password_hash == teacher_hash
    assert db.get(ClassRoom, cls.id).owner_user_id == teacher.id
    assert usage_db.scalar(select(AiUsageRecord.total_tokens)) == 17
    audit = db.scalar(select(AuditLog).where(AuditLog.action == "reset_password"))
    assert audit.entity_id == admin.id and audit.operator_type == "local_cli"
    assert audit.before_json is None and audit.after_json is None and audit.operator_id is None
    assert client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {admin_token}"}).status_code == 401
    assert client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {teacher_token}"}).status_code == 200
    assert client.post("/api/v1/auth/login", json={"username": admin.username, "password": OLD_PASSWORD}).status_code == 401
    login = client.post("/api/v1/auth/login", json={"username": admin.username, "password": NEW_PASSWORD})
    assert login.status_code == 200 and login.json()["data"]["user"]["must_change_password"] is True


@pytest.mark.parametrize("password", ["", "x" * 129, " " * 8])
def test_local_reset_rejects_invalid_password_without_changes(db, admin, password):
    old_hash = admin.password_hash
    with pytest.raises(AppError, match="1–128"):
        accounts.reset_admin_password_locally(db, admin_id=admin.id, new_password=password)
    assert admin.password_hash == old_hash and not admin.must_change_password
    assert db.scalar(select(func.count()).select_from(AuditLog)) == 0


def test_local_reset_never_creates_admin_or_promotes_teacher(db):
    teacher = accounts.create_teacher(db, UserCreate(username="teacher-only"))
    with pytest.raises(AppError, match="不存在管理员"):
        accounts.reset_admin_password_locally(db, admin_id=teacher.id, new_password=NEW_PASSWORD)
    assert teacher.role == "head_teacher"
    assert db.scalar(select(func.count()).select_from(User)) == 1


def test_changed_admin_target_is_rejected(db, admin):
    with pytest.raises(AppError, match="已发生变化"):
        accounts.reset_admin_password_locally(db, admin_id="old-admin-id", new_password=NEW_PASSWORD)
    assert accounts.verify_password(OLD_PASSWORD, admin.password_hash)


@pytest.mark.parametrize("failure", ["audit", "commit"])
def test_reset_rolls_back_password_sessions_and_audit(db, admin, monkeypatch, failure):
    _, old_session = accounts.issue_session(db, admin)
    original_hash = admin.password_hash

    def fail(*args, **kwargs):
        db.flush()
        raise SQLAlchemyError("simulated failure")

    monkeypatch.setattr(accounts if failure == "audit" else db, "audit" if failure == "audit" else "commit", fail)
    with pytest.raises(SQLAlchemyError):
        accounts.reset_admin_password_locally(db, admin_id=admin.id, new_password=NEW_PASSWORD)
    assert admin.password_hash == original_hash and not admin.must_change_password
    assert old_session.revoked_at is None
    assert db.scalar(select(func.count()).select_from(AuditLog)) == 0


def test_local_reset_does_not_reactivate_disabled_admin(db, admin):
    admin.is_active = False
    db.commit()
    accounts.reset_admin_password_locally(db, admin_id=admin.id, new_password="x" * 128)
    assert not admin.is_active
    assert accounts.verify_password("x" * 128, admin.password_hash)


@pytest.fixture
def terminal(db, admin, tmp_path, monkeypatch):
    database_path = tmp_path / "core.db"
    database_path.touch()
    monkeypatch.setattr(config, "settings", replace(
        config.settings, database_url=f"sqlite:///{database_path}", default_admin_password=NEW_PASSWORD,
    ))
    monkeypatch.setenv("CLASSCLAW_DEFAULT_ADMIN_PASSWORD", NEW_PASSWORD)
    # No interactive terminal is required and no password should be read from it.
    monkeypatch.setattr(cli.sys, "stdin", SimpleNamespace(isatty=lambda: False))
    events = []

    @contextmanager
    def session(kind):
        events.append(kind)
        yield db

    monkeypatch.setattr(database, "reader_session", lambda: session("reader"))
    monkeypatch.setattr(database, "writer_session", lambda: session("writer"))

    def forbidden_init(**kwargs):
        raise AssertionError("Password recovery must not initialize any database")

    monkeypatch.setattr(database, "init_db", forbidden_init)
    return events, database_path


def test_terminal_reset_uses_configured_password_without_prompt_or_secret_output(terminal, db, admin, capsys):
    original_hash = admin.password_hash
    assert cli.main([]) == 0
    out, err = capsys.readouterr()
    assert "custom-administrator" in out and "密码已重置" in out
    assert not err
    for secret in (NEW_PASSWORD, OLD_PASSWORD, original_hash, admin.password_hash):
        assert secret not in out + err
    assert terminal[0] == ["writer"]
    assert accounts.verify_password(NEW_PASSWORD, admin.password_hash)


@pytest.mark.parametrize("configured", [None, "", "   "])
def test_terminal_missing_env_password_does_not_fall_back(terminal, admin, monkeypatch, configured):
    if configured is None:
        monkeypatch.delenv("CLASSCLAW_DEFAULT_ADMIN_PASSWORD")
    else:
        monkeypatch.setenv("CLASSCLAW_DEFAULT_ADMIN_PASSWORD", configured)
    assert cli.main([]) == 1
    assert accounts.verify_password(OLD_PASSWORD, admin.password_hash)
    assert terminal[0] == []


def test_explicit_legacy_password_in_env_is_used_exactly(terminal, admin, monkeypatch):
    monkeypatch.setenv("CLASSCLAW_DEFAULT_ADMIN_PASSWORD", "32767")
    monkeypatch.setattr(config, "settings", replace(config.settings, default_admin_password="32767"))
    assert cli.main([]) == 0
    assert accounts.verify_password("32767", admin.password_hash)


def test_terminal_missing_database_is_not_created(terminal, tmp_path, monkeypatch):
    absent = tmp_path / "absent" / "core.db"
    monkeypatch.setattr(config, "settings", replace(config.settings, database_url=f"sqlite:///{absent}"))
    assert cli.main([]) == 1
    assert not absent.parent.exists() and terminal[0] == []


def test_terminal_database_error_does_not_print_sql_or_hashes(terminal, monkeypatch, capsys):
    def broken_reset(*args, **kwargs):
        raise SQLAlchemyError("private hash and SQL parameters")

    monkeypatch.setattr(accounts, "reset_admin_password_locally", broken_reset)
    assert cli.main([]) == 1
    out, err = capsys.readouterr()
    assert "数据库操作失败" in err
    assert "private hash" not in out + err and NEW_PASSWORD not in out + err


def test_help_does_not_open_database(terminal):
    with pytest.raises(SystemExit) as stopped:
        cli.main(["--help"])
    assert stopped.value.code == 0 and terminal[0] == []


def test_real_command_updates_only_temporary_core_database(tmp_path):
    engine = database.build_engine(f"sqlite:///{tmp_path / 'command-core.db'}")
    database.Base.metadata.create_all(engine)
    try:
        with engine.begin() as connection:
            connection.execute(User.__table__.insert(), {
                "id": "existing-admin", "username": "configured-existing-admin", "role": "admin",
                "password_hash": accounts.hash_password(OLD_PASSWORD),
            })
        config_file = tmp_path / "classclaw.toml"
        config_file.write_text("", encoding="utf-8")
        usage_path = tmp_path / "unused-usage.db"
        env = {
            **os.environ,
            "CLASSCLAW_CONFIG_FILE": str(config_file), "CLASSCLAW_DATABASE_URL": str(engine.url),
            "CLASSCLAW_USAGE_DATABASE_URL": f"sqlite:///{usage_path}", "CLASSCLAW_DEFAULT_ADMIN_PASSWORD": NEW_PASSWORD,
        }
        result = subprocess.run(
            [sys.executable, str(config.BASE_DIR / "scripts" / "reset_admin_password.py")],
            cwd=tmp_path, env=env, capture_output=True, text=True, timeout=15, check=False,
        )
        assert result.returncode == 0, result.stderr
        assert NEW_PASSWORD not in result.stdout + result.stderr
        assert "configured-existing-admin" in result.stdout
        with engine.connect() as connection:
            assert accounts.verify_password(NEW_PASSWORD, connection.scalar(select(User.password_hash)))
            assert connection.scalar(select(func.count()).select_from(User)) == 1
            assert connection.scalar(select(AuditLog.operator_type)) == "local_cli"
        assert not usage_path.exists()
    finally:
        engine.dispose()
