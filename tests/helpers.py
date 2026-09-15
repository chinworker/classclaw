"""Shared test helpers for creating bound head-teacher accounts."""
from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session


def teacher_for_class(client: Any, cls: Any, db: Session, username: str, admin_headers: dict | None = None) -> dict:
    """Create a head-teacher account, bound to the class, and return auth headers."""
    user = client.post("/api/v1/admin/users", json={"username": username}, headers=admin_headers or {}).json()["data"]
    cls.owner_user_id = user["id"]
    db.commit()
    token = client.post("/api/v1/auth/login", json={"username": username, "password": "32767"}).json()["data"]["access_token"]
    return {"Authorization": f"Bearer {token}"}
