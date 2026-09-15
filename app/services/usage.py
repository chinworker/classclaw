from __future__ import annotations

import logging
from typing import Any

from app.models.usage import AiUsageRecord
from app.usage_database import writer_session


def _int(value: Any) -> int:
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError, OverflowError):
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


def record_openclaw_usage(payload: dict[str, Any], *, user: str, model: str) -> None:
    usage = payload.get("usage")
    if not isinstance(usage, dict):
        return
    input_tokens = _int(usage.get("input_tokens") or usage.get("prompt_tokens"))
    output_tokens = _int(usage.get("output_tokens") or usage.get("completion_tokens"))
    total_tokens = _int(usage.get("total_tokens")) or input_tokens + output_tokens
    details = usage.get("input_tokens_details") or usage.get("prompt_tokens_details") or {}
    cached = _int(details.get("cached_tokens")) if isinstance(details, dict) else 0

    try:
        with writer_session() as target_db:
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
    except Exception as exc:
        # Never commit or roll back a caller's business transaction. Telemetry
        # failures must not turn a successful model response into a failed call.
        logging.getLogger(__name__).warning("AI usage record skipped: %s", type(exc).__name__)
