from __future__ import annotations

import uuid
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy.exc import OperationalError

from app.api.v1.router import api_router
from app.config import settings
from app.core.errors import AppError
from app.database import SessionLocal, init_db
from app.services import accounts
from app.services.http_client import close_http_client


@asynccontextmanager
async def lifespan(_app: FastAPI):
    settings.attachment_dir.mkdir(parents=True, exist_ok=True)
    init_db()
    with SessionLocal() as db:
        accounts.ensure_default_admin(db)
    try:
        yield
    finally:
        await close_http_client()


app = FastAPI(
    title=settings.app_name,
    version="0.1.0",
    description="供 OpenClaw 智能体与未来网页端共用的班级管理 REST API。",
    lifespan=lifespan,
)


@app.middleware("http")
async def request_context(request: Request, call_next):
    request.state.request_id = request.headers.get("X-Request-ID") or str(uuid.uuid4())
    response = await call_next(request)
    response.headers["X-Request-ID"] = request.state.request_id
    return response


@app.exception_handler(AppError)
async def app_error_handler(request: Request, exc: AppError):
    return JSONResponse(status_code=exc.status_code, content={"success": False, "error": {"code": exc.code, "message": exc.message, "details": exc.details}, "request_id": request.state.request_id})


@app.exception_handler(RequestValidationError)
async def validation_error_handler(request: Request, exc: RequestValidationError):
    return JSONResponse(status_code=422, content={"success": False, "error": {"code": "VALIDATION_ERROR", "message": "请求参数校验失败", "details": {"errors": exc.errors()}}, "request_id": request.state.request_id})


@app.exception_handler(OperationalError)
async def database_error_handler(request: Request, exc: OperationalError):
    text = str(exc.orig).lower()
    if "locked" in text or "busy" in text:
        return JSONResponse(status_code=503, content={"success": False, "error": {"code": "DATABASE_BUSY", "message": "数据库繁忙，请稍后重试", "details": {}}, "request_id": request.state.request_id})
    return JSONResponse(status_code=500, content={"success": False, "error": {"code": "INTERNAL_ERROR", "message": "数据库操作失败", "details": {}}, "request_id": request.state.request_id})


@app.exception_handler(Exception)
async def internal_error_handler(request: Request, _exc: Exception):
    return JSONResponse(status_code=500, content={"success": False, "error": {"code": "INTERNAL_ERROR", "message": "服务器内部错误", "details": {}}, "request_id": request.state.request_id})


@app.get("/health")
def health():
    return {"status": "ok", "timezone": settings.timezone}


app.include_router(api_router, prefix=settings.api_prefix)

web_dir = Path(__file__).resolve().parent.parent / "web"
if web_dir.is_dir():
    app.mount("/app", StaticFiles(directory=web_dir, html=True), name="classclaw-web")
