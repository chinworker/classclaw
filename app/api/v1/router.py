from fastapi import APIRouter, Depends

from app.api.v1 import (
    academic,
    admin_console,
    agent_chat,
    agent_memory,
    ai_tasks,
    analytics,
    approval,
    auth_admin,
    classes_students,
    classroom,
    classroom_terminal,
    classroom_streaming,
    configuration,
    interactions,
    operations,
    seating_duty,
    timetable,
)
from app.core.security import require_authenticated

api_router = APIRouter()
api_router.include_router(configuration.router)
api_router.include_router(auth_admin.public_router)
# 终端用设备凭据鉴权，不走用户会话，因此挂在受保护路由之外。
api_router.include_router(classroom_terminal.router)
api_router.include_router(classroom_streaming.public_router)

protected_router = APIRouter(dependencies=[Depends(require_authenticated)])
protected_router.include_router(auth_admin.account_router)
protected_router.include_router(auth_admin.admin_router)
protected_router.include_router(admin_console.router)
protected_router.include_router(ai_tasks.router)
protected_router.include_router(agent_chat.router)
protected_router.include_router(agent_memory.router)
protected_router.include_router(approval.router)
protected_router.include_router(interactions.router)
protected_router.include_router(classes_students.router)
protected_router.include_router(classroom.router)
protected_router.include_router(classroom_streaming.router)
protected_router.include_router(seating_duty.router)
protected_router.include_router(academic.router)
protected_router.include_router(timetable.router)
protected_router.include_router(operations.router)
protected_router.include_router(analytics.router)
api_router.include_router(protected_router)
