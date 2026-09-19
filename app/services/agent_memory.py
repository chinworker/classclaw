"""Confirmed class memory. Extraction is stateless; only reviewed facts live here."""
from __future__ import annotations

import hashlib
import json
from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings
from app.core.errors import AppError
from app.models.entities import ClassAgentMemory
from app.schemas.agent_memory import MemoryEntry, normalized_name
from app.services.class_student import get_class
from app.utils.time import now

MAX_MEMORIES = 100


def _rows(db: Session, class_id: str) -> list[ClassAgentMemory]:
    get_class(db, class_id)
    return list(db.scalars(select(ClassAgentMemory).where(ClassAgentMemory.class_id == class_id)
                           .order_by(ClassAgentMemory.kind, ClassAgentMemory.name, ClassAgentMemory.id)))


def _entry(row: ClassAgentMemory) -> dict:
    return {"kind": row.kind, "name": row.name, "aliases": row.aliases_json, "content": row.content,
            "start_time": row.start_time, "end_time": row.end_time, "weekdays": row.weekdays_json,
            "valid_from": row.valid_from.isoformat() if row.valid_from else None,
            "valid_to": row.valid_to.isoformat() if row.valid_to else None}


def describe(entry: dict) -> str:
    scope = f"{entry['valid_from']} 至 {entry['valid_to']}" if entry.get("valid_from") else "长期"
    if entry["kind"] == "schedule":
        days = "、".join("一二三四五六日"[day - 1] for day in entry["weekdays"])
        value = f"周{days} {entry['start_time']}—{entry['end_time']}"
        if entry.get("content"):
            value += f"；{entry['content']}"
    else:
        value = entry["content"]
    aliases = f"（也叫{'、'.join(entry['aliases'])}）" if entry.get("aliases") else ""
    return f"{entry['name']}{aliases}：{value} · {scope}"


def _tokens(entry: dict) -> set[str]:
    return {normalized_name(name) for name in [entry["name"], *entry["aliases"]]}


def _scope(entry: dict) -> tuple:
    return tuple(entry["weekdays"]), entry["valid_from"], entry["valid_to"]


def _key(entry: dict) -> str:
    value = [entry["kind"], normalized_name(entry["name"]), *_scope(entry)]
    return hashlib.sha256(json.dumps(value, ensure_ascii=False).encode()).hexdigest()


def _state(rows: list[ClassAgentMemory]) -> str:
    return hashlib.sha256(json.dumps(sorted((row.id, row.revision) for row in rows)).encode()).hexdigest()


def _overlap(left: dict, right: dict) -> bool:
    if left["kind"] != right["kind"] or not _tokens(left) & _tokens(right):
        return False
    if left["kind"] == "schedule" and not set(left["weekdays"]) & set(right["weekdays"]):
        return False
    # A dated exception intentionally overrides a long-term rule. Two dated
    # exceptions must not overlap; neither may silently replace the other.
    if bool(left["valid_from"]) != bool(right["valid_from"]):
        return False
    return not left["valid_from"] or max(left["valid_from"], right["valid_from"]) <= min(left["valid_to"], right["valid_to"])


def _check_rules(entries: list[dict]) -> None:
    if len(entries) > MAX_MEMORIES:
        raise AppError("MEMORY_LIMIT", f"每班最多保存 {MAX_MEMORIES} 条记忆，请先忘记不再需要的内容", 409)
    for index, entry in enumerate(entries):
        if any(_overlap(entry, other) for other in entries[:index]):
            raise AppError("MEMORY_CONFLICT", f"“{entry['name']}”与已有名称或别名的适用范围重叠，请明确要更正哪一条", 409)


def read(db: Session, class_id: str, *, on_date: date | None = None, q: str = "", include_expired: bool = False) -> dict:
    current = now()
    day = on_date or current.date()
    rows = _rows(db, class_id)
    items = []
    needle = normalized_name(q)
    for row in rows:
        entry = _entry(row)
        if not include_expired and row.valid_to and row.valid_to < (on_date or current.date()):
            continue
        if needle and not any(needle in value for value in _tokens(entry)):
            continue
        items.append({"memory_id": row.id, "revision": row.revision, **entry, "description": describe(entry),
                      "expired": bool(row.valid_to and row.valid_to < current.date())})
    applicable = [item for item in items if (not item["valid_from"] or item["valid_from"] <= day.isoformat() <= item["valid_to"])
                  and (item["kind"] != "schedule" or day.isoweekday() in item["weekdays"])]
    effective = [item for item in applicable if item["valid_from"] or not any(
        other["valid_from"] and other["kind"] == item["kind"] and _tokens(other) & _tokens(item) for other in applicable)]
    return {"class_id": class_id, "current_datetime": current.isoformat(), "timezone": settings.timezone,
            "date": day.isoformat(), "items": items, "effective": effective, "total": len(items), "limit": MAX_MEMORIES}


def prepare(db: Session, operation: str, payload: dict, preview: dict) -> tuple[dict, dict]:
    rows = _rows(db, payload["class_id"])
    by_id = {row.id: row for row in rows}
    proposed = {row.id: _entry(row) for row in rows}
    changes = []
    if operation == "memory.forget":
        for memory_id in dict.fromkeys(payload["memory_ids"]):
            if memory_id not in by_id:
                raise AppError("MEMORY_NOT_FOUND", "要忘记的记忆不存在或不属于本班", 404)
            changes.append({"memory_id": memory_id, "action": "forget", "before": proposed.pop(memory_id), "after": None})
    else:
        touched: set[str] = set()
        for index, raw in enumerate(payload["entries"]):
            entry = {key: value for key, value in raw.items() if key != "memory_id"}
            memory_id = raw.get("memory_id")
            if memory_id and memory_id not in by_id:
                raise AppError("MEMORY_NOT_FOUND", "要更正的记忆不存在或不属于本班", 404)
            if not memory_id:
                candidates = [key for key, value in proposed.items() if value["kind"] == entry["kind"]
                              and _scope(value) == _scope(entry) and _tokens(value) & _tokens(entry)]
                if len(candidates) > 1:
                    raise AppError("MEMORY_CONFLICT", "名称或别名对应多条记忆，请明确要更正的内容", 409)
                memory_id = candidates[0] if candidates else None
            previous = proposed.get(memory_id)
            if previous:
                if previous["kind"] != entry["kind"] or (previous["valid_from"], previous["valid_to"]) != (entry["valid_from"], entry["valid_to"]):
                    raise AppError("MEMORY_SCOPE_CONFLICT", "临时调整须另存带日期的记忆，不能覆盖长期约定或改变原有效期", 409)
                # Keep known aliases when learning a correction, including a
                # prior name. Explicit forgetting remains a separate operation.
                names = [*entry["aliases"], *previous["aliases"], previous["name"]]
                if not raw.get("memory_id"):
                    names.append(entry["name"])
                    entry["name"] = previous["name"]
                aliases = {normalized_name(name): name for name in names if normalized_name(name) != normalized_name(entry["name"])}
                if len(aliases) > 5:
                    raise AppError("MEMORY_LIMIT", "同一条记忆最多保存 5 个别名", 409)
                entry["aliases"] = sorted(aliases.values(), key=normalized_name)
            key = memory_id or f"new:{index}"
            if previous == entry:
                continue
            if key in touched:
                raise AppError("MEMORY_CONFLICT", "同一批次对同一记忆给出了不同内容，请统一后重试", 409)
            touched.add(key)
            proposed[key] = entry
            changes.append({"memory_id": memory_id, "action": "update" if previous else "create", "before": previous, "after": entry})
        _check_rules(list(proposed.values()))
    if not changes:
        raise AppError("MEMORY_UNCHANGED", "这些内容已经记住，无需重复保存", 409)
    summary = [{"action": {"create": "记住", "update": "更正", "forget": "忘记"}[item["action"]],
                "before": describe(item["before"]) if item["before"] else None,
                "after": describe(item["after"]) if item["after"] else None} for item in changes]
    normalized = {"class_id": payload["class_id"], "expected_state": _state(rows), "changes": changes}
    return normalized, {**preview, "summary": {"changes": summary, "record_count": len(changes)},
                        "confirmation_message": "请核对以上记忆变化，确认后将在本班后续对话中使用。"}


def validate_batch(db: Session, payloads: list[dict]) -> None:
    """Freeze all memory previews before any mutation in an atomic confirmation."""
    groups: dict[str, list[dict]] = {}
    for payload in payloads:
        groups.setdefault(payload["class_id"], []).append(payload)
    for class_id, group in groups.items():
        rows = _rows(db, class_id)
        if any(_state(rows) != payload["expected_state"] for payload in group):
            raise AppError("MEMORY_STALE", "记忆在预览后已发生变化，请重新核对后确认", 409)
        projected = {row.id: _entry(row) for row in rows}
        touched: set[str] = set()
        for payload in group:
            for change in payload["changes"]:
                memory_id = change["memory_id"]
                if memory_id and memory_id in touched:
                    raise AppError("MEMORY_CONFLICT", "多个预览修改了同一条记忆，请合并更正后重新核对", 409)
                key = memory_id or f"new:{len(touched)}"
                touched.add(key)
                if change["action"] == "forget":
                    projected.pop(key)
                else:
                    projected[key] = change["after"]
        _check_rules(list(projected.values()))


def execute(db: Session, payload: dict, *, prevalidated: bool = False) -> dict:
    rows = _rows(db, payload["class_id"])
    if not prevalidated:
        validate_batch(db, [payload])
    by_id = {row.id: row for row in rows}
    for change in payload["changes"]:
        row = by_id.get(change["memory_id"])
        if change["action"] == "forget":
            db.delete(row)
            continue
        entry = MemoryEntry.model_validate(change["after"])
        if row is None:
            row = ClassAgentMemory(class_id=payload["class_id"], revision=1)
            db.add(row)
        else:
            row.revision += 1
        row.memory_key = _key(change["after"])
        for field in ("kind", "name", "content", "start_time", "end_time", "valid_from", "valid_to"):
            setattr(row, field, getattr(entry, field))
        row.aliases_json = entry.aliases
        row.weekdays_json = entry.weekdays
    db.flush()
    return {"class_id": payload["class_id"], "changed_count": len(payload["changes"])}
