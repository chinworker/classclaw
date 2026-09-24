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
    if not user:
        raise AppError("ACCOUNT_NOT_FOUND", "账号不存在，请检查用户名", 401)
    if not user.is_active:
        raise AppError("ACCOUNT_DISABLED", "账号已停用，请联系管理员", 401)
    if not verify_password(password, user.password_hash):
        raise AppError("INVALID_PASSWORD", "密码错误，请检查密码", 401)
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
    from app.services import deletions
    deletions.require_available(db, "user", user_id)
    user = db.get(User, user_id)
    if not user:
        raise AppError("NOT_FOUND", "用户不存在", 404, {"id": user_id})
    if user.role == "admin":
        raise AppError("ADMIN_ACCOUNT_PROTECTED", "唯一管理员账号不能通过用户管理接口修改", 409)
    changes = data.model_dump(exclude_unset=True)
    if "username" in changes:
        username = normalize_username(changes["username"])
        collision = db.scalar(select(User).where(User.username == username, User.id != user.id))
        if collision:
            raise AppError("USERNAME_CONFLICT", "用户名已存在", 409, {"username": username})
        user.username = username
    if "display_name" in changes:
        value = changes["display_name"]
        user.display_name = value.strip() if value else None
    if changes.get("is_active") is not None:
        user.is_active = changes["is_active"]
        if not user.is_active:
            db.query(UserSession).filter(UserSession.user_id == user.id, UserSession.revoked_at.is_(None)).update({"revoked_at": now()})
            from app.services.classroom_media import revoke_user
            revoke_user(db, user.id, "account_disabled", commit=False)
    audit(db, "update", "user", user.id, operator_type="admin")
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise AppError("USERNAME_CONFLICT", "用户名已存在", 409) from exc
    return user


def delete_teacher(db: Session, user_id: str, *, operator_id: str | None = None, commit: bool = True) -> dict:
    user = db.get(User, user_id)
    if not user:
        raise AppError("NOT_FOUND", "用户不存在", 404, {"id": user_id})
    if user.role != "head_teacher":
        raise AppError("ADMIN_ACCOUNT_PROTECTED", "唯一管理员账号不能删除", 409)
    username = user.username
    class_ids = list(db.scalars(select(ClassRoom.id).where(ClassRoom.owner_user_id == user.id, ClassRoom.deleted_at.is_(None))))
    db.query(UserSession).filter(UserSession.user_id == user.id, UserSession.revoked_at.is_(None)).update({"revoked_at": now()})
    for cls in db.scalars(select(ClassRoom).where(ClassRoom.owner_user_id == user.id)):
        cls.owner_user_id = None
    audit(db, "delete", "user", user.id, operator_type="admin", operator_id=operator_id, before={"username": username, "class_ids": class_ids})
    db.delete(user)
    if commit:
        db.commit()
    return {"id": user_id, "username": username, "deleted": True, "unassigned_class_ids": class_ids}


def reset_teacher_password(db: Session, user_id: str) -> User:
    from app.services import deletions
    deletions.require_available(db, "user", user_id)
    user = db.get(User, user_id)
    if not user:
        raise AppError("NOT_FOUND", "用户不存在", 404, {"id": user_id})
    if user.role != "head_teacher":
        raise AppError("ADMIN_ACCOUNT_PROTECTED", "管理员密码只能由管理员本人修改", 409)
    user.password_hash = hash_password("32767")
    user.must_change_password = True
    db.query(UserSession).filter(UserSession.user_id == user.id, UserSession.revoked_at.is_(None)).update({"revoked_at": now()})
    from app.services.classroom_media import revoke_user
    revoke_user(db, user.id, "password_reset", commit=False)
    audit(db, "reset_password", "user", user.id, operator_type="admin")
    db.commit()
    return user


def admin_for_local_reset(db: Session) -> User:
    """Lookup only; recovery must never bootstrap an account in a wrong database."""
    admin = db.scalars(select(User).where(User.role == "admin")).one_or_none()
    if admin is None:
        raise AppError("ADMIN_NOT_FOUND", "数据库中不存在管理员，请核对数据库配置；不会自动创建账号", 404)
    return admin


def reset_admin_password_locally(db: Session, *, admin_id: str, new_password: str) -> User:
    """Privileged local CLI only: never expose through an HTTP route or Agent tool.

    The caller uses a dedicated writer_session; password, revocations and audit
    are committed together. Match the selected administrator before writing.
    """
    # Match the login schema, including explicitly configured legacy passwords.
    if not 1 <= len(new_password) <= 128 or not new_password.strip():
        raise AppError("VALIDATION_ERROR", "配置的管理员密码须为 1–128 个字符，不能全部为空白", 422)
    admin = admin_for_local_reset(db)
    if admin.id != admin_id:
        raise AppError("ADMIN_CHANGED", "管理员账号已发生变化，请重新运行重置命令", 409)
    try:
        admin.password_hash = hash_password(new_password)
        admin.must_change_password = True
        db.query(UserSession).filter(UserSession.user_id == admin.id, UserSession.revoked_at.is_(None)).update(
            {"revoked_at": now()}, synchronize_session="fetch",
        )
        audit(db, "reset_password", "user", admin.id, operator_type="local_cli")
        db.commit()
    except Exception:
        db.rollback()
        raise
    return admin


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
    from app.services import deletions
    deletions.require_available(db, "user", user_id)
    deletions.require_available(db, "class", class_id)
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
