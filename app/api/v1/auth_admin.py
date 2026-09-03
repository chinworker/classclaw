from __future__ import annotations

import hashlib

from fastapi import APIRouter, Body, Depends, Query, Request
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import settings
from app.core.errors import AppError
from app.core.responses import ok
from app.core.security import Principal, require_admin, require_authenticated
from app.database import Base, get_db
from app.models.entities import ClassAgentBinding, ClassRoom, User, UserSession
from app.schemas.auth import LoginRequest, PasswordChange, UserCreate, UserUpdate
from app.services import accounts, admin_console, openclaw_provisioning
from app.utils.time import now

public_router = APIRouter(tags=["账户"])
account_router = APIRouter(tags=["账户"])
admin_router = APIRouter(prefix="/admin", tags=["管理员"], dependencies=[Depends(require_admin)])


@public_router.post("/auth/login")
def login(request: Request, body: LoginRequest, db: Session = Depends(get_db)):
    token, user = accounts.login(db, body.username, body.password)
    return ok(
        request,
        {
            "access_token": token,
            "token_type": "bearer",
            "expires_in": settings.auth_session_hours * 3600,
            "user": accounts.public_user(user, accounts.user_class_id(db, user.id)),
        },
        "登录成功",
    )


@account_router.get("/auth/me")
def me(request: Request, principal: Principal = Depends(require_authenticated)):
    if principal.is_service:
        return ok(request, {"id": None, "username": principal.username, "role": "admin", "is_service": True, "class_id": None})
    return ok(request, accounts.public_user(principal.user, principal.class_id))


@account_router.post("/auth/change-password")
def change_password(request: Request, body: PasswordChange, principal: Principal = Depends(require_authenticated), db: Session = Depends(get_db)):
    if principal.is_service or not principal.user:
        raise AppError("SERVICE_ACCOUNT_PASSWORD_UNAVAILABLE", "系统 API Token 不能通过此接口修改", 409)
    accounts.change_password(db, principal.user, body.current_password, body.new_password)
    return ok(request, {"must_change_password": False}, "密码修改成功")


@account_router.post("/auth/logout")
def logout(request: Request, principal: Principal = Depends(require_authenticated), db: Session = Depends(get_db)):
    if not principal.is_service:
        token = request.headers.get("Authorization", "").removeprefix("Bearer ").strip()
        token_hash = hashlib.sha256(token.encode()).hexdigest()
        session = db.scalar(select(UserSession).where(UserSession.token_hash == token_hash))
        if session and session.revoked_at is None:
            session.revoked_at = now()
            db.commit()
    return ok(request, None, "已退出登录")


@admin_router.get("/users")
def user_list(request: Request, db: Session = Depends(get_db)):
    return ok(request, accounts.list_users(db))


@admin_router.post("/users", status_code=201)
def user_create(request: Request, body: UserCreate, db: Session = Depends(get_db)):
    user = accounts.create_teacher(db, body)
    return ok(request, accounts.public_user(user), "班主任账号已创建", 201)


@admin_router.patch("/users/{user_id}")
def user_update(request: Request, user_id: str, body: UserUpdate, db: Session = Depends(get_db)):
    user = accounts.update_teacher(db, user_id, body)
    return ok(request, accounts.public_user(user, accounts.user_class_id(db, user.id)), "用户已更新")


@admin_router.delete("/users/{user_id}")
def user_delete(request: Request, user_id: str, db: Session = Depends(get_db)):
    return ok(request, accounts.delete_teacher(db, user_id), "用户已删除")


@admin_router.post("/users/{user_id}/reset-password")
def user_reset_password(request: Request, user_id: str, db: Session = Depends(get_db)):
    user = accounts.reset_teacher_password(db, user_id)
    return ok(request, accounts.public_user(user, accounts.user_class_id(db, user.id)), "密码已重置为 32767")


@admin_router.post("/users/{user_id}/assign-class")
def user_assign_class(request: Request, user_id: str, class_id: str = Body(..., embed=True), db: Session = Depends(get_db)):
    cls = accounts.assign_class(db, user_id, class_id)
    return ok(request, {"user_id": user_id, "class_id": cls.id, "class_name": cls.name}, "班级已分配给班主任账号")


@admin_router.get("/agents")
def agent_list(request: Request, db: Session = Depends(get_db)):
    rows = db.execute(
        select(ClassAgentBinding, ClassRoom, User)
        .join(ClassRoom, ClassRoom.id == ClassAgentBinding.class_id)
        .outerjoin(User, User.id == ClassRoom.owner_user_id)
        .order_by(ClassAgentBinding.updated_at.desc())
    ).all()
    data = [
        {
            "binding": binding,
            "class": {"id": cls.id, "name": cls.name, "status": cls.status},
            "owner": accounts.public_user(user, cls.id) if user else None,
        }
        for binding, cls, user in rows
    ]
    return ok(request, data)


@admin_router.post("/agents/{class_id}/wechat/start")
async def agent_wechat_start(request: Request, class_id: str, force: bool = Body(default=True, embed=True), db: Session = Depends(get_db)):
    admin_console.require_feature("feature.wechat_binding")
    return ok(request, await openclaw_provisioning.start_wechat_binding(db, class_id, force), "智能体配置已刷新，请扫码绑定微信")


def _redact_row(table_name: str, row: dict) -> dict:
    sensitive = {
        "users": {"password_hash"},
        "user_sessions": {"token_hash"},
        "system_settings": set(),
    }
    hidden = sensitive.get(table_name, set())
    return {key: "***" if key in hidden and value is not None else value for key, value in row.items()}


@admin_router.get("/database/overview")
def database_overview(request: Request, db: Session = Depends(get_db)):
    tables = []
    for name, table in sorted(Base.metadata.tables.items()):
        count = db.scalar(select(func.count()).select_from(table)) or 0
        tables.append({"name": name, "row_count": count})
    return ok(request, {"dialect": db.bind.dialect.name if db.bind else None, "tables": tables})


@admin_router.get("/database/tables/{table_name}")
def database_table(
    request: Request,
    table_name: str,
    offset: int = Query(0, ge=0),
    limit: int | None = Query(None, ge=1, le=200),
    db: Session = Depends(get_db),
):
    limit = limit or int(admin_console.setting_value("admin.database_page_size"))
    table = Base.metadata.tables.get(table_name)
    if table is None:
        raise AppError("NOT_FOUND", "数据库表不存在", 404, {"table": table_name})
    primary_keys = list(table.primary_key.columns)
    statement = select(table)
    if primary_keys:
        statement = statement.order_by(*primary_keys)
    rows = db.execute(statement.offset(offset).limit(limit)).mappings().all()
    total = db.scalar(select(func.count()).select_from(table)) or 0
    return ok(request, {"table": table_name, "columns": [column.name for column in table.columns], "items": [_redact_row(table_name, dict(row)) for row in rows], "total": total, "offset": offset, "limit": limit})
