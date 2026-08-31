from __future__ import annotations

from typing import Any

from app.database import writer_session
from app.models.entities import AiUsageRecord


def _int(value: Any) -> int:
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError):
        return 0


def operation_from_user(user: str) -> str:
    prefixes = (
        "classclaw-onboarding-import", "classclaw-timetable-import", "classclaw-seating-import",
        "classclaw-duty-rule", "classclaw-event", "classclaw-interaction",
    )
    return next((prefix.removeprefix("classclaw-") for prefix in prefixes if user.startswith(prefix)), "other")


def record_openclaw_usage(payload: dict[str, Any], *, user: str, model: str) -> None:
    usage = payload.get("usage")
    if not isinstance(usage, dict):
        return
    input_tokens = _int(usage.get("input_tokens") or usage.get("prompt_tokens"))
    output_tokens = _int(usage.get("output_tokens") or usage.get("completion_tokens"))
    total_tokens = _int(usage.get("total_tokens")) or input_tokens + output_tokens
    details = usage.get("input_tokens_details") or usage.get("prompt_tokens_details") or {}
    cached = _int(details.get("cached_tokens")) if isinstance(details, dict) else 0
    with writer_session() as db:
        try:
            db.add(AiUsageRecord(
                operation=operation_from_user(user),
                model=str(payload.get("model") or model)[:200],
                response_id=str(payload.get("id"))[:200] if payload.get("id") else None,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                total_tokens=total_tokens,
                cached_input_tokens=cached,
            ))
            db.commit()
        except Exception:
            db.rollback()
