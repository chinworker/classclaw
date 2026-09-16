from __future__ import annotations

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy.orm import Session

from app.core.errors import AppError
from app.core.responses import ok
from app.core.security import principal_from_request, require_owned_class, require_owned_student
from app.database import get_db
from app.schemas.domain import ClassCreate, ClassUpdate, StudentCreate, StudentUpdate
from app.services import class_student as service
from app.services import deletions

router = APIRouter(tags=["班级与学生"])


@router.post("/classes", status_code=201)
def class_create(request: Request, body: ClassCreate, db: Session = Depends(get_db)):
    raise AppError(
        "WEB_ONBOARDING_REQUIRED",
        "新班级只能通过 ClassClaw 网页向导创建，以同时完成资料复核、专属智能体创建和微信绑定",
        403,
        {"onboarding_url": "/app/"},
    )


@router.get("/classes")
def class_list(request: Request, page: int = Query(1, ge=1), page_size: int = Query(20, ge=1, le=100), status: str | None = None, db: Session = Depends(get_db)):
    principal = principal_from_request(request)
    return ok(request, service.list_classes(db, page, page_size, status, None if principal.is_admin else principal.user_id))


@router.get("/classes/{class_id}")
def class_get(request: Request, class_id: str, db: Session = Depends(get_db)):
    require_owned_class(request, class_id)
    return ok(request, service.get_class(db, class_id, include_inactive=True))


@router.patch("/classes/{class_id}")
def class_update(request: Request, class_id: str, body: ClassUpdate, db: Session = Depends(get_db)):
    require_owned_class(request, class_id)
    return ok(request, service.update_class(db, class_id, body), "班级更新成功")


@router.delete("/classes/{class_id}")
async def class_delete(request: Request, class_id: str, db: Session = Depends(get_db)):
    require_owned_class(request, class_id, allow_pending_deletion=True)
    principal = principal_from_request(request)
    result = await deletions.delete_target(db, "class", class_id, principal.user_id)
    return ok(request, result, "班级及关联业务数据已彻底删除")


@router.post("/classes/{class_id}/current")
def class_set_current(request: Request, class_id: str, db: Session = Depends(get_db)):
    require_owned_class(request, class_id)
    return ok(request, service.set_current_class(db, class_id), "当前班级已切换")


@router.post("/classes/{class_id}/deactivate")
def class_deactivate(request: Request, class_id: str, db: Session = Depends(get_db)):
    require_owned_class(request, class_id)
    return ok(request, service.update_class(db, class_id, ClassUpdate(status="inactive")), "班级已停用")


@router.get("/classes/{class_id}/summary")
def class_summary(request: Request, class_id: str, db: Session = Depends(get_db)):
    require_owned_class(request, class_id)
    return ok(request, service.class_summary(db, class_id))


@router.post("/students", status_code=201)
def student_create(request: Request, body: StudentCreate, db: Session = Depends(get_db)):
    require_owned_class(request, body.class_id)
    return ok(request, service.create_student(db, body), "学生创建成功", 201)


@router.get("/students")
def student_search(
    request: Request,
    class_id: str | None = None,
    q: str | None = None,
    tag: str | None = None,
    status: str | None = None,
    exact_name: bool = False,
    student_no: str | None = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: Session = Depends(get_db),
):
    principal = principal_from_request(request)
    if not principal.is_admin:
        if class_id and class_id != principal.class_id:
            require_owned_class(request, class_id)
        class_id = principal.class_id
        if not class_id:
            return ok(request, {"items": [], "total": 0, "page": page, "page_size": page_size})
    return ok(request, service.search_students(db, class_id=class_id, query=q, tag=tag, status=status, page=page, page_size=page_size, exact_name=exact_name, student_no=student_no))


@router.get("/students/{student_id}")
def student_detail(request: Request, student_id: str, db: Session = Depends(get_db)):
    require_owned_student(request, db, student_id)
    return ok(request, service.student_detail(db, student_id))


@router.patch("/students/{student_id}")
def student_update(request: Request, student_id: str, body: StudentUpdate, db: Session = Depends(get_db)):
    require_owned_student(request, db, student_id)
    return ok(request, service.update_student(db, student_id, body), "学生档案已更新")


@router.delete("/students/{student_id}")
def student_delete(request: Request, student_id: str, db: Session = Depends(get_db)):
    require_owned_student(request, db, student_id)
    return ok(request, service.delete_student(db, student_id), "学生已软删除")
