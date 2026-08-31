from __future__ import annotations

from fastapi import APIRouter, Body, Depends, Query, Request
from sqlalchemy.orm import Session

from app.core.responses import ok
from app.core.security import Principal, require_admin
from app.database import get_db
from app.schemas.admin import AdminClassCreate, AdminClassOwnerUpdate, AdminInitializeRequest, AdminSettingUpdate, OpenClawAgentUpdate, OpenClawGlobalUpdate, OpenClawWorkspaceFileUpdate
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
async def class_create(request: Request, body: AdminClassCreate, db: Session = Depends(get_db)):
    cls = admin_console.create_admin_class(db, body)
    binding = openclaw_provisioning.get_binding(db, cls.id)
    agent_error = None
    if body.provision_agent:
        try:
            binding = await openclaw_provisioning.provision_class_agent(db, cls.id)
        except Exception as exc:
            agent_error = str(exc)[:1000]
    return ok(request, {"class": cls, "binding": binding, "agent_error": agent_error}, "班级已创建", 201)


@router.patch("/classes/{class_id}/owner")
def class_owner_update(request: Request, class_id: str, body: AdminClassOwnerUpdate, db: Session = Depends(get_db)):
    return ok(request, admin_console.update_class_owner(db, class_id, body.owner_user_id), "班级负责人已更新")


@router.get("/openclaw/config")
async def openclaw_config(request: Request):
    return ok(request, await admin_console.openclaw_config())


@router.patch("/openclaw/config")
async def openclaw_config_update(request: Request, body: OpenClawGlobalUpdate):
    return ok(request, await admin_console.update_openclaw_config(body), "OpenClaw 配置已提交")


@router.patch("/openclaw/agents/{class_id}")
async def openclaw_agent_update(request: Request, class_id: str, body: OpenClawAgentUpdate, db: Session = Depends(get_db)):
    return ok(request, await admin_console.update_openclaw_agent(db, class_id, body), "智能体配置已更新")


@router.get("/openclaw/agents/{class_id}/settings")
async def openclaw_agent_settings(request: Request, class_id: str, db: Session = Depends(get_db)):
    return ok(request, await admin_console.openclaw_agent_settings(db, class_id))


@router.put("/openclaw/agents/{class_id}/workspace/{filename}")
def openclaw_agent_workspace_update(request: Request, class_id: str, filename: str, body: OpenClawWorkspaceFileUpdate, db: Session = Depends(get_db)):
    return ok(request, openclaw_provisioning.update_workspace_file(db, class_id, filename, body.content, body.expected_sha256), "智能体工作区文件已保存")


@router.post("/openclaw/agents/{class_id}/workspace/{filename}/reset")
def openclaw_agent_workspace_reset(request: Request, class_id: str, filename: str, db: Session = Depends(get_db)):
    return ok(request, openclaw_provisioning.reset_workspace_file(db, class_id, filename), "智能体工作区文件已恢复默认")


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
async def system_initialize(request: Request, body: AdminInitializeRequest, principal: Principal = Depends(require_admin), db: Session = Depends(get_db)):
    result = await admin_console.initialize_class_data(db, operator_id=principal.user_id)
    message = "班级数据初始化完成" if result["status"] == "completed" else "班级数据已初始化，但部分外部资源清理失败"
    return ok(request, result, message)
