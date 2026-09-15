from __future__ import annotations

import json
import math
import re
from collections import defaultdict
from datetime import date, datetime, time
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from app.config import settings

_SAFE_AGENT_ID = re.compile(r"^[A-Za-z0-9_-]+$")
_ERROR_STOP_REASONS = {"error", "aborted", "timeout"}
_MAX_FILES = 256
_MAX_BYTES = 64 * 1024 * 1024
_MAX_LATENCY_MS = 720 * 60 * 60 * 1000


def _timestamp_ms(value: Any) -> int | None:
    if isinstance(value, (int, float)) and math.isfinite(value):
        return int(value if value > 10_000_000_000 else value * 1000)
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=ZoneInfo(settings.timezone))
    return int(parsed.timestamp() * 1000)


def _number(value: Any) -> float | None:
    if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value):
        return float(value)
    return None


def _stats(values: list[float], *, latest_ms: float | None = None) -> dict[str, float | int] | None:
    if not values:
        return None
    ordered = sorted(values)
    count = len(ordered)
    result: dict[str, float | int] = {
        "count": count,
        "avgMs": sum(ordered) / count,
        "p50Ms": ordered[max(0, math.ceil(count * 0.50) - 1)],
        "p95Ms": ordered[max(0, math.ceil(count * 0.95) - 1)],
        "minMs": ordered[0],
        "maxMs": ordered[-1],
    }
    if latest_ms is not None:
        result["latestMs"] = latest_ms
    return result


def _is_transcript(path: Path) -> bool:
    name = path.name
    if path.is_symlink() or not path.is_file() or ".trajectory.jsonl" in name:
        return False
    return name.endswith(".jsonl") or ".jsonl.reset." in name or ".jsonl.deleted." in name


def scan_agent_sessions(agent_id: str, start_date: date, end_date: date) -> dict[str, Any]:
    """Read only timing/usage metadata from OpenClaw transcripts; message content is ignored."""
    if not _SAFE_AGENT_ID.fullmatch(agent_id):
        return {"available": False, "error": "invalid agent id", "calls": 0, "messages": {}, "latency": None, "daily": []}
    state_root = settings.openclaw_state_dir.expanduser().resolve()
    session_dir = (state_root / "agents" / agent_id / "sessions").resolve()
    if state_root not in session_dir.parents or not session_dir.is_dir():
        return {"available": False, "error": "session directory unavailable", "calls": 0, "messages": {}, "latency": None, "daily": []}

    zone = ZoneInfo(settings.timezone)
    start_ms = int(datetime.combine(start_date, time.min, tzinfo=zone).timestamp() * 1000)
    end_ms = int(datetime.combine(end_date, time.max, tzinfo=zone).timestamp() * 1000)
    candidates: list[tuple[float, int, Path]] = []
    read_errors = 0
    for path in session_dir.iterdir():
        try:
            if not _is_transcript(path):
                continue
            stat = path.stat()
            if stat.st_mtime * 1000 < start_ms:
                continue
            candidates.append((stat.st_mtime, stat.st_size, path))
        except OSError:
            read_errors += 1
    candidates.sort(key=lambda item: item[0], reverse=True)

    messages = {"total": 0, "user": 0, "assistant": 0, "errors": 0}
    calls = 0
    latency_values: list[float] = []
    daily: dict[str, dict[str, Any]] = defaultdict(
        lambda: {"model_calls": 0, "messages": 0, "user": 0, "assistant": 0, "errors": 0, "latency_values": []}
    )
    latest_latency: tuple[int, float] | None = None
    files_scanned = 0
    bytes_scanned = 0
    truncated = len(candidates) > _MAX_FILES

    for _mtime, size, path in candidates[:_MAX_FILES]:
        if bytes_scanned + size > _MAX_BYTES:
            truncated = True
            break
        bytes_scanned += size
        files_scanned += 1
        last_user_ms: int | None = None
        try:
            with path.open("r", encoding="utf-8", errors="replace") as handle:
                for raw in handle:
                    try:
                        entry = json.loads(raw)
                    except (json.JSONDecodeError, UnicodeDecodeError):
                        continue
                    if not isinstance(entry, dict) or entry.get("type") != "message":
                        continue
                    message = entry.get("message")
                    if not isinstance(message, dict):
                        continue
                    timestamp = _timestamp_ms(entry.get("timestamp") or message.get("timestamp"))
                    if timestamp is None or timestamp < start_ms or timestamp > end_ms:
                        continue
                    role = message.get("role")
                    day_key = datetime.fromtimestamp(timestamp / 1000, zone).date().isoformat()
                    bucket = daily[day_key]
                    if role == "user":
                        messages["user"] += 1
                        messages["total"] += 1
                        bucket["user"] += 1
                        bucket["messages"] += 1
                        last_user_ms = timestamp
                        continue
                    if role == "toolResult":
                        if message.get("isError") is True:
                            messages["errors"] += 1
                            bucket["errors"] += 1
                        continue
                    if role != "assistant":
                        continue
                    messages["assistant"] += 1
                    messages["total"] += 1
                    bucket["assistant"] += 1
                    bucket["messages"] += 1
                    if isinstance(message.get("usage"), dict):
                        calls += 1
                        bucket["model_calls"] += 1
                    if message.get("stopReason") in _ERROR_STOP_REASONS or entry.get("stopReason") in _ERROR_STOP_REASONS:
                        messages["errors"] += 1
                        bucket["errors"] += 1
                    latency = _number(message.get("durationMs"))
                    if latency is None:
                        latency = _number(entry.get("durationMs"))
                    if latency is None and last_user_ms is not None:
                        latency = float(max(0, timestamp - last_user_ms))
                    if latency is not None and 0 <= latency <= _MAX_LATENCY_MS:
                        latency_values.append(latency)
                        bucket["latency_values"].append(latency)
                        if latest_latency is None or timestamp > latest_latency[0]:
                            latest_latency = (timestamp, latency)
        except OSError:
            read_errors += 1

    daily_rows = []
    for day_key in sorted(daily):
        bucket = daily[day_key]
        values = bucket.pop("latency_values")
        daily_rows.append({"date": day_key, **bucket, "latency": _stats(values)})
    return {
        "available": True,
        "source": "openclaw_transcripts",
        "calls": calls,
        "messages": messages,
        "latency": _stats(latency_values, latest_ms=latest_latency[1] if latest_latency else None),
        "daily": daily_rows,
        "files_scanned": files_scanned,
        "bytes_scanned": bytes_scanned,
        "read_errors": read_errors,
        "truncated": truncated,
    }


def scan_agents(agent_ids: list[str], start_date: date, end_date: date) -> dict[str, dict[str, Any]]:
    return {agent_id: scan_agent_sessions(agent_id, start_date, end_date) for agent_id in agent_ids}
