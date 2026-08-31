from __future__ import annotations

from typing import Any

from sqlalchemy import inspect
from sqlalchemy.orm import Session

from app.models.entities import AuditLog


AUDITED_ACTIONS = {
    ("create", "class"),
    ("hard_delete", "class"),
    ("soft_delete", "student"),
    ("revoke", "student_event"),
    ("create", "user"),
    ("update", "user"),
    ("reset_password", "user"),
    ("repair_agent_claim", "class_agent_binding"),
    ("recover_provision", "class_agent_binding"),
    ("provision", "class_agent_binding"),
    ("bind_channel", "class_agent_binding"),
}


def entity_dict(obj: Any) -> dict[str, Any]:
    mapper = inspect(obj).mapper
    return {attr.key: getattr(obj, attr.key) for attr in mapper.column_attrs}


def audit(
    db: Session,
    action: str,
    entity_type: str,
    entity_id: str,
    before: dict | None = None,
    after: dict | None = None,
    *,
    operator_type: str = "user",
    operator_id: str | None = None,
    source_message_id: str | None = None,
) -> AuditLog | None:
    if (action, entity_type) not in AUDITED_ACTIONS:
        return None
    log = AuditLog(
        operator_type=operator_type,
        operator_id=operator_id,
        action=action,
        entity_type=entity_type,
        entity_id=entity_id,
        before_json=None,
        after_json=None,
        source_message_id=None,
    )
    db.add(log)
    return log
