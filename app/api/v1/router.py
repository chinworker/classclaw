from fastapi import APIRouter, Depends

from app.api.v1 import (
    academic,
    agent_chat,
    admin_console,
    ai_tasks,
    analytics,
    approval,
    auth_admin,
    classes_students,
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

protected_router = APIRouter(dependencies=[Depends(require_authenticated)])
protected_router.include_router(auth_admin.account_router)
protected_router.include_router(auth_admin.admin_router)
protected_router.include_router(admin_console.router)
protected_router.include_router(ai_tasks.router)
protected_router.include_router(agent_chat.router)
protected_router.include_router(approval.router)
protected_router.include_router(interactions.router)
protected_router.include_router(classes_students.router)
protected_router.include_router(seating_duty.router)
protected_router.include_router(academic.router)
protected_router.include_router(timetable.router)
protected_router.include_router(operations.router)
protected_router.include_router(analytics.router)
api_router.include_router(protected_router)
