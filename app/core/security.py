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
from app.models.entities import DeletionOperation, Student, User, UserSession
from app.services.accounts import user_class_id
from app.utils.time import now

_READ_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})


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


def _require_no_pending_deletion(request: Request, target_type: str, target_id: str, db: Session | None = None) -> None:
    """Refuse new writes while a deletion job for the same resource still needs retry."""
    if request.method in _READ_METHODS:
        return
    session = db or getattr(request.state, "db", None)
    if session is None:
        return
    pending = session.scalar(
        select(DeletionOperation.id).where(
            DeletionOperation.target_type == target_type,
            DeletionOperation.target_id == target_id,
            DeletionOperation.status != "complete",
        )
    )
    if pending:
        raise AppError("DELETION_IN_PROGRESS", "该资源正在删除或等待清理，请先完成删除操作", 409,
                       {"target_type": target_type, "target_id": target_id})


def require_authenticated(request: Request, db: Session = Depends(get_db)) -> Principal:
    supplied = request.headers.get("Authorization", "")
    if not supplied.startswith("Bearer "):
        raise AppError("UNAUTHORIZED", "请先登录", 401)
    token = supplied.removeprefix("Bearer ").strip()
    request.state.db = db
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
    # Let an existing run be cancelled and let the user log out while deletion waits.
    route_name = getattr(request.scope.get("route"), "name", None)
    if route_name not in {"logout", "ai_task_cancel"}:
        _require_no_pending_deletion(request, "user", user.id, db)
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


def require_owned_class(request: Request, class_id: str, *, allow_pending_deletion: bool = False) -> None:
    principal = principal_from_request(request)
    previous_owner = False
    if allow_pending_deletion and not principal.is_admin and principal.class_id != class_id:
        db = getattr(request.state, "db", None)
        if db is not None:
            previous_owner = db.scalar(select(DeletionOperation.id).where(
                DeletionOperation.target_type == "class", DeletionOperation.target_id == class_id,
                DeletionOperation.owner_user_id == principal.user_id)) is not None
    if not principal.is_admin and principal.class_id != class_id and not previous_owner:
        raise AppError("CLASS_ACCESS_DENIED", "只能访问当前班主任账号绑定的班级", 403, {"class_id": class_id})
    if not allow_pending_deletion:
        _require_no_pending_deletion(request, "class", class_id)


def require_class_available(request: Request, class_id: str, db: Session | None = None) -> None:
    """Explicit route-layer state guard; service-layer callers already run this check."""
    _require_no_pending_deletion(request, "class", class_id, db=db)


def require_owned_student(request: Request, db: Session, student_id: str) -> None:
    principal = principal_from_request(request)
    student = db.get(Student, student_id)
    if not principal.is_admin and (not student or student.class_id != principal.class_id):
        raise AppError("CLASS_ACCESS_DENIED", "只能访问当前班主任账号绑定班级的学生", 403, {"student_id": student_id})
    if student:
        _require_no_pending_deletion(request, "class", student.class_id, db=db)


def scoped_class_id(request: Request, class_id: str | None = None) -> str | None:
    principal = principal_from_request(request)
    if principal.is_admin:
        resolved = class_id
    elif not principal.class_id:
        raise AppError("CLASS_ACCESS_DENIED", "当前班主任账号尚未绑定班级", 403)
    elif class_id and class_id != principal.class_id:
        raise AppError("CLASS_ACCESS_DENIED", "只能访问当前班主任账号绑定的班级", 403, {"class_id": class_id})
    else:
        resolved = principal.class_id
    if resolved:
        _require_no_pending_deletion(request, "class", resolved)
    return resolved


def require_owned_record(request: Request, db: Session, model: type, entity_id: str, class_attribute: str = "class_id"):
    obj = db.get(model, entity_id)
    principal = principal_from_request(request)
    if not obj or (not principal.is_admin and getattr(obj, class_attribute, None) != principal.class_id):
        raise AppError("CLASS_ACCESS_DENIED", "只能访问当前班主任账号绑定班级的数据", 403, {"id": entity_id})
    class_id = getattr(obj, class_attribute, None)
    if class_id:
        _require_no_pending_deletion(request, "class", class_id, db=db)
    return obj
