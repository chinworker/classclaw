from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime

from fastapi import Depends, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings
from app.core.errors import AppError
from app.database import get_db
from app.models.entities import Student, User, UserSession
from app.services.accounts import user_class_id
from app.utils.time import now


@dataclass(frozen=True, slots=True)
class Principal:
    user_id: str | None
    username: str
    role: str
    class_id: str | None
    user: User | None = None
    is_service: bool = False

    @property
    def is_admin(self) -> bool:
        return self.role == "admin"


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=now().tzinfo)


def require_authenticated(request: Request, db: Session = Depends(get_db)) -> Principal:
    supplied = request.headers.get("Authorization", "")
    if not supplied.startswith("Bearer "):
        raise AppError("UNAUTHORIZED", "请先登录", 401)
    token = supplied.removeprefix("Bearer ").strip()
    if settings.api_token and token == settings.api_token:
        principal = Principal(None, "openclaw-service", "admin", None, is_service=True)
        request.state.principal = principal
        return principal
    token_hash = hashlib.sha256(token.encode()).hexdigest()
    row = db.execute(
        select(UserSession, User)
        .join(User, User.id == UserSession.user_id)
        .where(UserSession.token_hash == token_hash, UserSession.revoked_at.is_(None))
    ).first()
    if not row:
        raise AppError("UNAUTHORIZED", "登录会话不存在或已退出", 401)
    session, user = row
    if _aware(session.expires_at) <= now() or not user.is_active:
        raise AppError("UNAUTHORIZED", "登录会话已过期或账号已停用", 401)
    principal = Principal(user.id, user.username, user.role, user_class_id(db, user.id), user=user)
    request.state.principal = principal
    return principal


def require_admin(principal: Principal = Depends(require_authenticated)) -> Principal:
    if not principal.is_admin:
        raise AppError("ADMIN_REQUIRED", "仅管理员可以执行此操作", 403)
    return principal


def principal_from_request(request: Request) -> Principal:
    principal = getattr(request.state, "principal", None)
    if not principal:
        raise AppError("UNAUTHORIZED", "请先登录", 401)
    return principal


def require_owned_class(request: Request, class_id: str) -> None:
    principal = principal_from_request(request)
    if not principal.is_admin and principal.class_id != class_id:
        raise AppError("CLASS_ACCESS_DENIED", "只能访问当前班主任账号绑定的班级", 403, {"class_id": class_id})


def require_owned_student(request: Request, db: Session, student_id: str) -> None:
    principal = principal_from_request(request)
    if principal.is_admin:
        return
    student = db.get(Student, student_id)
    if not student or student.class_id != principal.class_id:
        raise AppError("CLASS_ACCESS_DENIED", "只能访问当前班主任账号绑定班级的学生", 403, {"student_id": student_id})


def scoped_class_id(request: Request, class_id: str | None = None) -> str | None:
    principal = principal_from_request(request)
    if principal.is_admin:
        return class_id
    if not principal.class_id:
        raise AppError("CLASS_ACCESS_DENIED", "当前班主任账号尚未绑定班级", 403)
    if class_id and class_id != principal.class_id:
        raise AppError("CLASS_ACCESS_DENIED", "只能访问当前班主任账号绑定的班级", 403, {"class_id": class_id})
    return principal.class_id


def require_owned_record(request: Request, db: Session, model: type, entity_id: str, class_attribute: str = "class_id"):
    obj = db.get(model, entity_id)
    principal = principal_from_request(request)
    if not obj or (not principal.is_admin and getattr(obj, class_attribute, None) != principal.class_id):
        raise AppError("CLASS_ACCESS_DENIED", "只能访问当前班主任账号绑定班级的数据", 403, {"id": entity_id})
    return obj
