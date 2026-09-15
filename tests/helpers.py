"""Shared test helpers for creating bound head-teacher accounts and class agents."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session

from app.models.entities import ClassAgentBinding


def teacher_for_class(client: Any, cls: Any, db: Session, username: str, admin_headers: dict | None = None) -> dict:
    """Create a head-teacher account, bound to the class, and return auth headers."""
    user = client.post("/api/v1/admin/users", json={"username": username}, headers=admin_headers or {}).json()["data"]
    cls.owner_user_id = user["id"]
    db.commit()
    token = client.post("/api/v1/auth/login", json={"username": username, "password": "32767"}).json()["data"]["access_token"]
    return {"Authorization": f"Bearer {token}"}


def make_binding(db: Session, class_id: str, workspace: Path | str, **kwargs: Any) -> ClassAgentBinding:
    """Create a minimal ClassAgentBinding; sensible defaults, overridable via kwargs."""
    fields: dict[str, Any] = {
        "class_id": class_id,
        "openclaw_agent_id": "class-agent",
        "agent_name": "测试助手",
        "workspace_path": str(workspace),
        "status": "agent_created",
    }
    fields.update(kwargs)
    binding = ClassAgentBinding(**fields)
    db.add(binding)
    db.commit()
    return binding
