from __future__ import annotations

import hashlib
import json
import uuid
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session

from app.core.errors import AppError
from app.services.common import audit


EDITABLE_FILES = {
    "AGENTS.md": {"label": "系统提示词", "description": "核心行为、工作流和回复规则。"},
    "SOUL.md": {"label": "Soul", "description": "人格、语气、价值取向与边界。"},
    "IDENTITY.md": {"label": "Identity", "description": "名称、角色和对外身份。"},
    "TOOLS.md": {"label": "Tools", "description": "工具使用策略和调用约定。"},
    "USER.md": {"label": "User", "description": "主要用户与业务上下文。"},
    "HEARTBEAT.md": {"label": "Heartbeat", "description": "心跳任务说明。"},
}
CUSTOMIZED_META = ".classclaw-admin-customized.json"


def customized_files(workspace: Path) -> set[str]:
    path = workspace / CUSTOMIZED_META
    if not path.is_file():
        return set()
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return set()
    return {str(name) for name in value if name in EDITABLE_FILES} if isinstance(value, list) else set()


def _write_customized(workspace: Path, names: set[str]) -> None:
    _atomic_write(workspace / CUSTOMIZED_META, json.dumps(sorted(names), ensure_ascii=False, indent=2) + "\n")


def _atomic_write(target: Path, content: str) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.parent / f".{target.name}.{uuid.uuid4().hex}.tmp"
    temporary.write_text(content, encoding="utf-8")
    temporary.replace(target)


def ensure_defaults(workspace_path: str | Path, defaults: dict[str, str]) -> None:
    """Write managed defaults without replacing files customized in Agent Studio."""
    workspace = Path(workspace_path).expanduser().resolve()
    workspace.mkdir(parents=True, exist_ok=True)
    customized = customized_files(workspace)
    for name, content in defaults.items():
        if name not in EDITABLE_FILES or name in customized:
            continue
        target = workspace / name
        if not target.is_file() or target.read_text(encoding="utf-8") != content:
            _atomic_write(target, content)


def snapshot(workspace_path: str | Path, *, defaults: dict[str, str] | None = None) -> dict[str, Any]:
    workspace = Path(workspace_path).expanduser().resolve()
    customized = customized_files(workspace)
    defaults = defaults or {}
    items = []
    for name, metadata in EDITABLE_FILES.items():
        path = workspace / name
        content = path.read_text(encoding="utf-8") if path.is_file() else ""
        items.append({
            "name": name,
            **metadata,
            "content": content,
            "sha256": hashlib.sha256(content.encode("utf-8")).hexdigest(),
            "characters": len(content),
            "customized": name in customized,
            "exists": path.is_file(),
            "resettable": name in defaults,
        })
    return {"workspace": str(workspace), "files": items}


def update_file(
    db: Session,
    *,
    agent_id: str,
    workspace_path: str | Path,
    filename: str,
    content: str,
    expected_sha256: str | None,
) -> dict[str, Any]:
    if filename not in EDITABLE_FILES:
        raise AppError("WORKSPACE_FILE_NOT_ALLOWED", "该工作区文件不允许通过管理端修改", 422, {"filename": filename})
    if "\x00" in content:
        raise AppError("VALIDATION_ERROR", "文件内容包含无效字符", 422)
    workspace = Path(workspace_path).expanduser().resolve()
    workspace.mkdir(parents=True, exist_ok=True)
    target = workspace / filename
    current = target.read_text(encoding="utf-8") if target.is_file() else ""
    current_hash = hashlib.sha256(current.encode("utf-8")).hexdigest()
    if expected_sha256 and expected_sha256 != current_hash:
        raise AppError("WORKSPACE_FILE_CONFLICT", "文件已被其他操作修改，请刷新后重试", 409, {"current_sha256": current_hash})
    _atomic_write(target, content)
    customized = customized_files(workspace)
    customized.add(filename)
    _write_customized(workspace, customized)
    digest = hashlib.sha256(content.encode("utf-8")).hexdigest()
    audit(
        db,
        "update_workspace_file",
        "openclaw_agent",
        agent_id,
        operator_type="admin",
        after={"filename": filename, "sha256": digest, "characters": len(content)},
    )
    db.commit()
    return {"name": filename, "sha256": digest, "characters": len(content), "customized": True, "exists": True}


def reset_file(
    db: Session,
    *,
    agent_id: str,
    workspace_path: str | Path,
    filename: str,
    defaults: dict[str, str] | None,
) -> dict[str, Any]:
    if filename not in EDITABLE_FILES:
        raise AppError("WORKSPACE_FILE_NOT_ALLOWED", "该工作区文件不允许通过管理端修改", 422, {"filename": filename})
    if not defaults or filename not in defaults:
        raise AppError("WORKSPACE_DEFAULT_UNAVAILABLE", "该智能体没有由 ClassClaw 管理的默认内容", 409, {"filename": filename})
    workspace = Path(workspace_path).expanduser().resolve()
    content = defaults[filename]
    _atomic_write(workspace / filename, content)
    customized = customized_files(workspace)
    customized.discard(filename)
    _write_customized(workspace, customized)
    digest = hashlib.sha256(content.encode("utf-8")).hexdigest()
    audit(
        db,
        "reset_workspace_file",
        "openclaw_agent",
        agent_id,
        operator_type="admin",
        after={"filename": filename, "sha256": digest},
    )
    db.commit()
    return {
        "name": filename,
        "content": content,
        "sha256": digest,
        "characters": len(content),
        "customized": False,
        "exists": True,
        "resettable": True,
    }
