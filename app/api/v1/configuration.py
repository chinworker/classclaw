from __future__ import annotations

from fastapi import APIRouter, Request

from app.config import settings
from app.core.responses import ok
from app.services.semester_calendar import current_or_next_semester

router = APIRouter(tags=["静态配置"])


@router.get("/app-config")
def app_config(request: Request):
    """Expose only the startup configuration that is safe and useful in the browser."""
    semester_defaults = current_or_next_semester().as_json()
    response = ok(
        request,
        {
            "config_hash": settings.config_hash,
            "brand": settings.web.brand.model_dump(),
            "features": settings.features.model_dump(),
            "runtime": {"timezone": settings.timezone},
            "storage": {"max_attachment_bytes": settings.max_attachment_bytes},
            "semester_defaults": semester_defaults,
            "web": {
                "ai_request_timeout_seconds": settings.web.ai_request_timeout_seconds,
                "usage_window_days": settings.web.usage_window_days,
                "database_page_size": settings.web.database_page_size,
            },
            "wechat": {
                "qr_binding_timeout_seconds": settings.wechat.qr_binding_timeout_seconds,
                "qr_initial_poll_ms": settings.wechat.qr_initial_poll_ms,
                "qr_poll_ms": settings.wechat.qr_poll_ms,
                "qr_retry_ms": settings.wechat.qr_retry_ms,
            },
        },
    )
    response.headers["Cache-Control"] = "no-store, max-age=0"
    response.headers["Pragma"] = "no-cache"
    return response
