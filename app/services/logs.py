from __future__ import annotations

import json
import re
from collections import deque
from typing import Any

from app.config import settings

_SECRET_PATTERNS = (
    re.compile(r"(?i)(authorization\s*[:=]\s*bearer\s+)[^\s\"']+"),
    re.compile(r"(?i)((?:api[_-]?key|token|password)\s*[:=]\s*)[^\s,;\"']+"),
)


def redact_line(value: str) -> str:
    result = value
    for pattern in _SECRET_PATTERNS:
        result = pattern.sub(r"\1***", result)
    return result[:20_000]


def classclaw_logs(*, limit: int, level: str | None = None, query: str | None = None) -> dict[str, Any]:
    path = settings.log_file
    if not path.exists():
        return {"source": "classclaw", "items": [], "file": str(path), "available": False}
    with path.open("r", encoding="utf-8", errors="replace") as handle:
        lines = deque(handle, maxlen=min(max(limit * 5, 200), 5000))
    items: list[dict[str, Any]] = []
    target_level = level.upper() if level else None
    needle = query.casefold() if query else None
    for raw in reversed(lines):
        try:
            item = json.loads(raw)
        except json.JSONDecodeError:
            item = {"timestamp": None, "level": "INFO", "logger": "classclaw", "message": raw.strip()}
        if target_level and str(item.get("level", "")).upper() != target_level:
            continue
        safe_text = redact_line(json.dumps(item, ensure_ascii=False))
        if needle and needle not in safe_text.casefold():
            continue
        items.append(json.loads(safe_text))
        if len(items) >= limit:
            break
    return {"source": "classclaw", "items": items, "file": str(path), "available": True}


def openclaw_logs_payload(payload: dict[str, Any], *, limit: int, level: str | None = None, query: str | None = None) -> dict[str, Any]:
    target_level = level.upper() if level else None
    needle = query.casefold() if query else None
    items = []
    for raw in reversed(payload.get("lines") or []):
        safe = redact_line(str(raw))
        parsed: dict[str, Any]
        try:
            value = json.loads(safe)
            parsed = value if isinstance(value, dict) else {"message": safe}
        except json.JSONDecodeError:
            parsed = {"message": safe}
        parsed.setdefault("level", parsed.get("levelName") or "INFO")
        parsed.setdefault("timestamp", parsed.get("time") or parsed.get("ts"))
        parsed.setdefault("logger", "openclaw")
        if target_level and str(parsed.get("level", "")).upper() != target_level:
            continue
        if needle and needle not in safe.casefold():
            continue
        items.append(parsed)
        if len(items) >= limit:
            break
    return {
        "source": "openclaw", "items": items, "file": payload.get("file"), "available": True,
        "cursor": payload.get("cursor"), "size": payload.get("size"), "truncated": payload.get("truncated", False),
    }
