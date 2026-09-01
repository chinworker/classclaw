from __future__ import annotations

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy.orm import Session

from app.core.errors import AppError
from app.core.responses import ok
from app.core.security import Principal, require_admin
from app.database import get_db
from app.schemas.admin import AdminClassOwnerUpdate, AdminInitializeRequest, AdminSettingUpdate, OpenClawAgentUpdate, OpenClawGlobalUpdate, OpenClawWorkspaceFileUpdate
from app.services import admin_console, openclaw_bridge, openclaw_provisioning


router = APIRouter(prefix="/admin", tags=["管理员控制台"], dependencies=[Depends(require_admin)])


@router.get("/overview")
async def overview(request: Request, db: Session = Depends(get_db)):
    data = admin_console.overview(db)
    data["openclaw"] = await openclaw_bridge.connection_status(force=True)
    return ok(request, data)


@router.get("/usage")
def usage(request: Request, days: int | None = Query(None, ge=1, le=365), db: Session = Depends(get_db)):
    return ok(request, admin_console.usage_stats(db, days))


@router.get("/usage/agents")
async def usage_agents(request: Request, days: int = Query(30, ge=1, le=365), db: Session = Depends(get_db)):
    return ok(request, await admin_console.openclaw_agent_usage(db, days))


@router.get("/settings")
def settings_list(request: Request, db: Session = Depends(get_db)):
    return ok(request, admin_console.list_settings(db))


@router.put("/settings/{key}")
def settings_update(request: Request, key: str, body: AdminSettingUpdate, principal: Principal = Depends(require_admin), db: Session = Depends(get_db)):
    return ok(request, admin_console.update_setting(db, key, body.value, operator_id=principal.user_id), "配置已保存")


@router.post("/classes", status_code=201)
def class_create(request: Request):
    raise AppError(
        "WEB_ONBOARDING_REQUIRED",
        "新班级只能由班主任账号通过创建向导创建，以同时完成资料复核、专属智能体创建和微信绑定；管理员可在班级创建后分配负责人",
        403,
        {"onboarding_url": "/app/#/onboarding"},
    )


@router.patch("/classes/{class_id}/owner")
def class_owner_update(request: Request, class_id: str, body: AdminClassOwnerUpdate, db: Session = Depends(get_db)):
    return ok(request, admin_console.update_class_owner(db, class_id, body.owner_user_id), "班级负责人已更新")


@router.get("/openclaw/config")
async def openclaw_config(request: Request):
    return ok(request, await admin_console.openclaw_config())


@router.patch("/openclaw/config")
async def openclaw_config_update(request: Request, body: OpenClawGlobalUpdate):
    return ok(request, await admin_console.update_openclaw_config(body), "OpenClaw 配置已提交")


@router.get("/openclaw/agents/catalog")
async def openclaw_agent_catalog(request: Request, db: Session = Depends(get_db)):
    return ok(request, await admin_console.openclaw_agent_catalog(db))


@router.patch("/openclaw/agents/{identifier}")
async def openclaw_agent_update(request: Request, identifier: str, body: OpenClawAgentUpdate, db: Session = Depends(get_db)):
    return ok(request, await admin_console.update_openclaw_agent(db, identifier, body), "智能体配置已更新")


@router.get("/openclaw/agents/{identifier}/settings")
async def openclaw_agent_settings(request: Request, identifier: str, db: Session = Depends(get_db)):
    return ok(request, await admin_console.openclaw_agent_settings(db, identifier))


@router.put("/openclaw/agents/{identifier}/workspace/{filename}")
async def openclaw_agent_workspace_update(request: Request, identifier: str, filename: str, body: OpenClawWorkspaceFileUpdate, db: Session = Depends(get_db)):
    result = await admin_console.update_openclaw_workspace(db, identifier, filename, body.content, body.expected_sha256)
    return ok(request, result, "智能体工作区文件已保存")


@router.post("/openclaw/agents/{identifier}/workspace/{filename}/reset")
async def openclaw_agent_workspace_reset(request: Request, identifier: str, filename: str, db: Session = Depends(get_db)):
    return ok(request, await admin_console.reset_openclaw_workspace(db, identifier, filename), "智能体工作区文件已恢复默认")


@router.get("/openclaw/sessions/cleanup")
def openclaw_sessions_cleanup_status(request: Request):
    return ok(request, openclaw_bridge.session_cleanup_status())


@router.post("/openclaw/sessions/cleanup")
async def openclaw_sessions_cleanup(request: Request, enforce: bool = Query(True)):
    result = await openclaw_bridge.run_session_cleanup(enforce=enforce)
    return ok(request, result, "Session 清理已执行" if result["ok"] else "Session 清理未完全成功，详见 output")


@router.get("/logs")
async def logs_tail(
    request: Request,
    source: str = Query("classclaw"),
    level: str | None = Query(None),
    q: str | None = Query(None, max_length=200),
    limit: int = Query(100, ge=1, le=500),
    cursor: int | None = Query(None, ge=0),
):
    return ok(request, await admin_console.logs_view(source=source, limit=limit, level=level, query=q, cursor=cursor))


@router.post("/system/initialize")
async def system_initialize(request: Request, body: AdminInitializeRequest, db: Session = Depends(get_db)):
    result = await admin_console.initialize_system(db)
    return ok(request, result, "系统完整初始化完成")
