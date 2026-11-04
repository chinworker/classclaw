from __future__ import annotations

import logging
from typing import Any

from sqlalchemy import inspect
from sqlalchemy.orm import Session

from app.models.entities import AuditLog

logger = logging.getLogger(__name__)

AUDITED_ACTIONS = {
    ("config_update", "configuration"),
    ("config_rollback", "configuration"),
    ("gateway_config_patch", "configuration"),
    ("create", "class"),
    ("update", "class"),
    ("assign_owner", "class"),
    ("hard_delete", "class"),
    ("soft_delete", "student"),
    ("revoke", "student_event"),
    ("delete", "exam"),
    ("delete", "attachment"),
    ("create", "user"),
    ("delete", "user"),
    ("complete", "deletion"),
    ("update", "user"),
    ("reset_password", "user"),
    ("repair_agent_claim", "class_agent_binding"),
    ("recover_provision", "class_agent_binding"),
    ("provision", "class_agent_binding"),
    ("bind_channel", "class_agent_binding"),
    ("update_agent_models", "class_agent_binding"),
    ("update_agent_thinking", "class_agent_binding"),
    ("update_openclaw_agent", "class_agent_binding"),
    ("update_openclaw_agent", "openclaw_agent"),
    ("update_workspace_file", "class_agent_binding"),
    ("reset_workspace_file", "class_agent_binding"),
    ("update_workspace_file", "openclaw_agent"),
    ("reset_workspace_file", "openclaw_agent"),
    ("pair_issue", "classroom_device"),
    ("pair_cancel", "classroom_device"),
    ("pair", "classroom_device"),
    ("revoke", "classroom_device"),
    ("unbind", "classroom_device"),
    ("connect", "classroom_camera"),
    ("replace", "classroom_camera"),
    ("disconnect", "classroom_camera"),
}


def entity_dict(obj: Any) -> dict[str, Any]:
    mapper = inspect(obj).mapper
    return {attr.key: getattr(obj, attr.key) for attr in mapper.column_attrs}


def audit(
    db: Session,
    action: str,
    entity_type: str,
    entity_id: str,
    *,
    after: dict | None = None,
    operator_type: str = "user",
    operator_id: str | None = None,
) -> AuditLog | None:
    if (action, entity_type) not in AUDITED_ACTIONS:
        logger.debug("Audit action outside the allowlist: %s / %s", action, entity_type)
        return None
    log = AuditLog(
        operator_type=operator_type,
        operator_id=operator_id,
        action=action,
        entity_type=entity_type,
        entity_id=entity_id,
        before_json=None,
        after_json={key: after[key] for key in ("paths", "old_config_hash", "new_config_hash") if key in after}
        if entity_type == "configuration" and after else None,
        source_message_id=None,
    )
    db.add(log)
    return log
