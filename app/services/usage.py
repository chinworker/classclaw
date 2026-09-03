from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from app.models.entities import AiUsageRecord


def _int(value: Any) -> int:
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError):
        return 0


def operation_from_user(user: str) -> str:
    prefixes = (
        "classclaw-onboarding-import",
        "classclaw-timetable-import",
        "classclaw-seating-import",
        "classclaw-event",
        "classclaw-interaction",
        "classclaw-web-chat",
    )
    return next((prefix.removeprefix("classclaw-") for prefix in prefixes if user.startswith(prefix)), "other")


def record_openclaw_usage(payload: dict[str, Any], *, user: str, model: str, db: Session) -> None:
    usage = payload.get("usage")
    if not isinstance(usage, dict):
        return
    input_tokens = _int(usage.get("input_tokens") or usage.get("prompt_tokens"))
    output_tokens = _int(usage.get("output_tokens") or usage.get("completion_tokens"))
    total_tokens = _int(usage.get("total_tokens")) or input_tokens + output_tokens
    details = usage.get("input_tokens_details") or usage.get("prompt_tokens_details") or {}
    cached = _int(details.get("cached_tokens")) if isinstance(details, dict) else 0

    def save(target_db: Session) -> None:
        try:
            target_db.add(
                AiUsageRecord(
                    operation=operation_from_user(user),
                    model=str(payload.get("model") or model)[:200],
                    response_id=str(payload.get("id"))[:200] if payload.get("id") else None,
                    input_tokens=input_tokens,
                    output_tokens=output_tokens,
                    total_tokens=total_tokens,
                    cached_input_tokens=cached,
                )
            )
            target_db.commit()
        except Exception:
            target_db.rollback()

    # HTTP 写请求已持有全局写锁；复用请求会话，禁止在异步调用链中
    # 再创建写会话，否则会跨线程等待自己持有的锁。
    save(db)
