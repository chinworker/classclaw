from __future__ import annotations

import json
import os
import re
from datetime import datetime
from typing import Any

from app.config import settings
from app.core.errors import AppError
from app.utils.time import now

_SECRET_PATTERNS = (
    re.compile(r"(?i)(\bbearer\s+)[^\s\"']+"),
    re.compile(r'''(?i)((?:api[_-]?key|token|password|secret)\s*["']?\s*[:=]\s*["']?)[^\s,;"'}]+'''),
)


def redact_line(value: str) -> str:
    result = value
    for pattern in _SECRET_PATTERNS:
        result = pattern.sub(r"\1***", result)
    return result[:20_000]


def _safe(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: "***" if re.search(r"token|password|secret|api.?key", key, re.IGNORECASE) else _safe(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_safe(item) for item in value]
    return redact_line(value) if isinstance(value, str) else value


def _timestamp(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=now().tzinfo)


def _matches(item: dict, *, level: str | None, query: str | None, request_id: str | None, since: str | None) -> bool:
    if level and str(item.get("level", "")).upper() != level.upper():
        return False
    if request_id and item.get("request_id") != request_id:
        return False
    if query and query.casefold() not in json.dumps(item, ensure_ascii=False).casefold():
        return False
    if since:
        try:
            cutoff = _timestamp(since)
        except ValueError as exc:
            raise AppError("VALIDATION_ERROR", "日志起始时间格式无效", 422) from exc
        try:
            return _timestamp(str(item.get("timestamp") or "")) >= cutoff
        except ValueError:
            return False
    return True


def classclaw_logs(*, limit: int, level: str | None = None, query: str | None = None,
                  request_id: str | None = None, since: str | None = None,
                  cursor: int | None = None, file_id: str | None = None) -> dict[str, Any]:
    path = settings.log_file
    if not path.exists():
        return {"source": "classclaw", "items": [], "file": str(path), "available": False}
    with path.open("rb") as handle:
        info = os.fstat(handle.fileno())
        identity = f"{info.st_dev}:{info.st_ino}"
        incremental = cursor is not None and cursor <= info.st_size and (not file_id or file_id == identity)
        start = cursor if incremental else 0
        bounded_start = max(start, info.st_size - 4_000_000)
        handle.seek(bounded_start)
        if bounded_start > start:
            handle.readline()  # Skip a partial first line.
        base = handle.tell()
        buffer = handle.read(4_000_000)
        complete = buffer.rfind(b"\n") + 1
        lines = buffer[:complete].splitlines()
        next_cursor = base + complete
        truncated = bounded_start > start or len(lines) > 5000
        lines = lines[-5000:]
    items: list[dict[str, Any]] = []
    for raw in reversed(lines):
        try:
            item = json.loads(raw)
        except (json.JSONDecodeError, UnicodeError):
            item = None
        if not isinstance(item, dict):
            item = {"timestamp": None, "level": "INFO", "logger": "classclaw", "message": raw.decode("utf-8", errors="replace").strip()}
        item = _safe(item)
        if not _matches(item, level=level, query=query, request_id=request_id, since=since):
            continue
        items.append(item)
        if len(items) >= limit:
            truncated = True
            break
    return {"source": "classclaw", "items": items, "file": str(path), "available": True,
            "cursor": next_cursor, "file_id": identity, "truncated": truncated, "reset": cursor is not None and not incremental}


def openclaw_logs_payload(payload: dict[str, Any], *, limit: int, level: str | None = None, query: str | None = None,
                          request_id: str | None = None, since: str | None = None) -> dict[str, Any]:
    items = []
    for raw in reversed((payload.get("lines") or [])[-5000:]):
        safe = str(raw)
        parsed: dict[str, Any]
        try:
            value = json.loads(safe)
            parsed = value if isinstance(value, dict) else {"message": safe}
        except json.JSONDecodeError:
            parsed = {"message": safe}
        parsed = _safe(parsed)
        meta = parsed.get("_meta") if isinstance(parsed.get("_meta"), dict) else {}
        parsed.setdefault("level", parsed.get("levelName") or meta.get("logLevelName") or "INFO")
        parsed.setdefault("timestamp", parsed.get("time") or parsed.get("ts"))
        parsed.setdefault("logger", meta.get("name") or "openclaw")
        parsed.setdefault("message", " ".join(str(parsed[key]) for key in ("0", "1", "2") if key in parsed))
        if not _matches(parsed, level=level, query=query, request_id=request_id, since=since):
            continue
        items.append(parsed)
        if len(items) >= limit:
            break
    return {
        "source": "openclaw", "items": items, "file": payload.get("file"), "available": True,
        "cursor": payload.get("cursor"), "size": payload.get("size"), "truncated": payload.get("truncated", False),
    }
