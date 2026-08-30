from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
import unicodedata
from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.config import settings
from app.core.errors import AppError
from app.models.entities import ClassRoom, User, UserSession
from app.schemas.auth import UserCreate, UserUpdate
from app.services.common import audit
from app.utils.time import now


PASSWORD_ITERATIONS = 260_000


def normalize_username(value: str) -> str:
    username = unicodedata.normalize("NFKC", value).strip().casefold()
    if not username or any(character.isspace() for character in username):
        raise AppError("VALIDATION_ERROR", "用户名不能为空且不能包含空格", 422)
    return username


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, PASSWORD_ITERATIONS)
    return f"pbkdf2_sha256${PASSWORD_ITERATIONS}${base64.urlsafe_b64encode(salt).decode()}${base64.urlsafe_b64encode(digest).decode()}"


def verify_password(password: str, encoded: str) -> bool:
    try:
        algorithm, iterations, salt_text, expected_text = encoded.split("$", 3)
        if algorithm != "pbkdf2_sha256":
            return False
        salt = base64.urlsafe_b64decode(salt_text.encode())
        expected = base64.urlsafe_b64decode(expected_text.encode())
        actual = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, int(iterations))
        return hmac.compare_digest(actual, expected)
    except (ValueError, TypeError):
        return False


def ensure_default_admin(db: Session) -> User:
    admin = db.scalar(select(User).where(User.role == "admin"))
    if admin:
        return admin
    username = normalize_username(settings.default_admin_username)
    collision = db.scalar(select(User).where(User.username == username))
    if collision:
        raise RuntimeError(f"默认管理员用户名 {username!r} 已被班主任账号占用")
    admin = User(
        username=username,
        password_hash=hash_password(settings.default_admin_password),
        role="admin",
        display_name="系统管理员",
        is_active=True,
        must_change_password=True,
    )
    db.add(admin)
    db.commit()
    return admin


def public_user(user: User, class_id: str | None = None) -> dict:
    return {
        "id": user.id,
        "username": user.username,
        "display_name": user.display_name,
        "role": user.role,
        "is_active": user.is_active,
        "must_change_password": user.must_change_password,
        "last_login_at": user.last_login_at,
        "created_at": user.created_at,
        "updated_at": user.updated_at,
        "class_id": class_id,
    }


def issue_session(db: Session, user: User) -> tuple[str, UserSession]:
    expires_at = now() + timedelta(hours=settings.auth_session_hours)
    token = secrets.token_urlsafe(32)
    session = UserSession(user_id=user.id, token_hash=hashlib.sha256(token.encode()).hexdigest(), expires_at=expires_at)
    db.add(session)
    user.last_login_at = now()
    db.commit()
    return token, session


def login(db: Session, username: str, password: str) -> tuple[str, User]:
    ensure_default_admin(db)
    normalized = normalize_username(username)
    user = db.scalar(select(User).where(User.username == normalized))
    if not user or not user.is_active or not verify_password(password, user.password_hash):
        raise AppError("INVALID_CREDENTIALS", "用户名或密码错误", 401)
    token, _session = issue_session(db, user)
    return token, user


def create_teacher(db: Session, data: UserCreate) -> User:
    username = normalize_username(data.username)
    if db.scalar(select(User).where(User.username == username)):
        raise AppError("USERNAME_CONFLICT", "用户名已存在", 409, {"username": username})
    user = User(
        username=username,
        password_hash=hash_password(data.password),
        role="head_teacher",
        display_name=data.display_name.strip() if data.display_name else None,
        is_active=True,
        must_change_password=True,
    )
    db.add(user)
    db.flush()
    audit(db, "create", "user", user.id, operator_type="admin")
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise AppError("USERNAME_CONFLICT", "用户名已存在", 409, {"username": username}) from exc
    return user


def list_users(db: Session) -> list[dict]:
    rows = db.execute(
        select(User, ClassRoom.id)
        .outerjoin(ClassRoom, ClassRoom.owner_user_id == User.id)
        .order_by(User.role, User.created_at)
    ).all()
    return [public_user(user, class_id) for user, class_id in rows]


def update_teacher(db: Session, user_id: str, data: UserUpdate) -> User:
    user = db.get(User, user_id)
    if not user:
        raise AppError("NOT_FOUND", "用户不存在", 404, {"id": user_id})
    if user.role == "admin":
        raise AppError("ADMIN_ACCOUNT_PROTECTED", "唯一管理员账号不能通过用户管理接口修改", 409)
    changes = data.model_dump(exclude_unset=True)
    if "display_name" in changes:
        value = changes["display_name"]
        user.display_name = value.strip() if value else None
    if changes.get("is_active") is not None:
        user.is_active = changes["is_active"]
        if not user.is_active:
            db.query(UserSession).filter(UserSession.user_id == user.id, UserSession.revoked_at.is_(None)).update({"revoked_at": now()})
    audit(db, "update", "user", user.id, operator_type="admin")
    db.commit()
    return user


def reset_teacher_password(db: Session, user_id: str) -> User:
    user = db.get(User, user_id)
    if not user:
        raise AppError("NOT_FOUND", "用户不存在", 404, {"id": user_id})
    if user.role != "head_teacher":
        raise AppError("ADMIN_ACCOUNT_PROTECTED", "管理员密码只能由管理员本人修改", 409)
    user.password_hash = hash_password("32767")
    user.must_change_password = True
    db.query(UserSession).filter(UserSession.user_id == user.id, UserSession.revoked_at.is_(None)).update({"revoked_at": now()})
    audit(db, "reset_password", "user", user.id, operator_type="admin")
    db.commit()
    return user


def change_password(db: Session, user: User, current_password: str, new_password: str) -> None:
    if not verify_password(current_password, user.password_hash):
        raise AppError("INVALID_CREDENTIALS", "当前密码错误", 401)
    if hmac.compare_digest(current_password, new_password):
        raise AppError("VALIDATION_ERROR", "新密码不能与当前密码相同", 422)
    user.password_hash = hash_password(new_password)
    user.must_change_password = False
    db.commit()


def user_class_id(db: Session, user_id: str) -> str | None:
    return db.scalar(select(ClassRoom.id).where(ClassRoom.owner_user_id == user_id, ClassRoom.deleted_at.is_(None)))


def assign_class(db: Session, user_id: str, class_id: str) -> ClassRoom:
    user = db.get(User, user_id)
    if not user or user.role != "head_teacher":
        raise AppError("NOT_FOUND", "班主任用户不存在", 404, {"user_id": user_id})
    cls = db.get(ClassRoom, class_id)
    if not cls or cls.deleted_at is not None:
        raise AppError("NOT_FOUND", "班级不存在", 404, {"class_id": class_id})
    existing_class_id = user_class_id(db, user.id)
    if existing_class_id and existing_class_id != class_id:
        raise AppError("CLASS_LIMIT_REACHED", "一个班主任账号只能创建并绑定一个班级", 409, {"class_id": existing_class_id})
    if cls.owner_user_id and cls.owner_user_id != user.id:
        raise AppError("CLASS_ALREADY_ASSIGNED", "该班级已分配给其他班主任账号", 409, {"class_id": class_id})
    cls.owner_user_id = user.id
    if not cls.head_teacher and user.display_name:
        cls.head_teacher = user.display_name
    db.commit()
    return cls
