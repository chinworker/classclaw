"""Shared test helpers for creating bound head-teacher accounts and class agents."""
from __future__ import annotations

import asyncio
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session

from app.models.entities import ClassAgentBinding, ClassroomDevice
from app.services.classroom_channel import TerminalConnection, register, unregister
from app.utils.time import now

TERMINAL_CAPABILITIES = {
    "display": True, "speak": True, "volume_control": True,
    "chinese_tts": True, "capture": True, "displays": ["教室一体机", "外接投影"],
}
TERMINAL_INVENTORY = {
    "cameras": [{"name": "USB 摄像头", "identifier": "usb-cam-1"}],
    "microphones": [{"name": "内置麦克风", "identifier": "mic-1"}],
    "speakers": [{"name": "教室音箱", "identifier": "spk-1"}],
}


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


def pair_terminal(client: Any, cls: Any, *, name: str = "教室终端", capabilities: dict | None = None,
                  inventory: dict | None = None) -> dict:
    """Issue a one-time pairing code over HTTP and exchange it for a device credential."""
    issued = client.post(f"/api/v1/classes/{cls.id}/classroom/pairing", json={"name": name}).json()["data"]
    response = client.post("/api/v1/classroom/device/pair", json={
        "pairing_code": issued["pairing_code"], "protocol_version": 1, "app_version": "1.0.0",
        "os_version": "Windows 10 教育版", "device_name": name,
        "capabilities": capabilities or TERMINAL_CAPABILITIES, "inventory": inventory or TERMINAL_INVENTORY,
    })
    assert response.status_code == 201, response.text
    return response.json()["data"]


@contextmanager
def online_terminal(db: Session, device_id: str, class_id: str) -> Any:
    """Register an in-memory control channel so the terminal counts as online.

    The loop never runs, so commands stay `authorized`: tests drive delivery and
    receipts explicitly instead of relying on a real socket.
    """
    device = db.get(ClassroomDevice, device_id)
    device.last_seen_at = now()
    db.commit()
    sent: list[dict] = []

    async def send(message: dict) -> None:
        sent.append(message)

    connection = TerminalConnection(device_id=device_id, class_id=class_id, send=send,
                                    loop=asyncio.new_event_loop())
    register(connection)
    try:
        yield sent
    finally:
        unregister(device_id, connection)
        connection.loop.close()


def report(db: Session, device_id: str, command_id: str, state: str, *, display: str | None = None,
           speak: str | None = None, error_code: str = "") -> dict:
    """Simulate one terminal receipt."""
    from app.schemas.classroom import CommandChannelResult
    from app.services import classroom_devices

    device = classroom_devices.require_paired(db, device_id)
    return classroom_devices.record_result(db, device, CommandChannelResult(
        command_id=command_id, state=state, display=display, speak=speak, error_code=error_code))
