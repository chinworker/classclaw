from __future__ import annotations

import asyncio
import uuid
import time
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
from app.core.logging import configure_logging, get_logger
from app.database import init_db, writer_session
from app.services import accounts, openclaw_bridge
from app.services.http_client import close_http_client


@asynccontextmanager
async def lifespan(_app: FastAPI):
    configure_logging()
    settings.attachment_dir.mkdir(parents=True, exist_ok=True)
    init_db()
    with writer_session() as db:
        accounts.ensure_default_admin(db)
    cleanup_task = None
    if settings.openclaw_session_cleanup_hours > 0:
        cleanup_task = asyncio.create_task(openclaw_bridge.session_cleanup_loop())
    try:
        yield
    finally:
        if cleanup_task:
            cleanup_task.cancel()
            try:
                await cleanup_task
            except asyncio.CancelledError:
                pass
        await close_http_client()


app = FastAPI(
    title=settings.app_name,
    version="0.1.0",
    description="供 OpenClaw 智能体与未来网页端共用的班级管理 REST API。",
    lifespan=lifespan,
)


@app.middleware("http")
async def request_context(request: Request, call_next):
    started = time.perf_counter()
    request.state.request_id = request.headers.get("X-Request-ID") or str(uuid.uuid4())
    response = await call_next(request)
    response.headers["X-Request-ID"] = request.state.request_id
    if request.url.path == "/app" or request.url.path.startswith("/app/"):
        response.headers["Cache-Control"] = "no-store, max-age=0"
        response.headers["Pragma"] = "no-cache"
        response.headers["Expires"] = "0"
    if request.url.path.startswith(settings.api_prefix) or request.url.path == "/health":
        get_logger("request").info(
            "%s %s %s", request.method, request.url.path, response.status_code,
            extra={
                "request_id": request.state.request_id, "method": request.method, "path": request.url.path,
                "status_code": response.status_code, "duration_ms": round((time.perf_counter() - started) * 1000, 2),
            },
        )
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
    get_logger("error").exception(
        "Unhandled request error", exc_info=(type(_exc), _exc, _exc.__traceback__),
        extra={"request_id": request.state.request_id, "path": request.url.path, "error_type": type(_exc).__name__},
    )
    return JSONResponse(status_code=500, content={"success": False, "error": {"code": "INTERNAL_ERROR", "message": "服务器内部错误", "details": {}}, "request_id": request.state.request_id})


@app.get("/health")
def health():
    return {"status": "ok", "timezone": settings.timezone}


app.include_router(api_router, prefix=settings.api_prefix)

web_dir = Path(__file__).resolve().parent.parent / "web"
if web_dir.is_dir():
    app.mount("/app", StaticFiles(directory=web_dir, html=True), name="classclaw-web")
