"""教室终端：配对、凭据、心跳、固定命令账本与到期清扫。

终端只持有本设备凭据，只能收到白名单内的固定动作。数据库提交与物理显示/播报
不是同一事务，所以命令状态区分登记、送达、执行中、成功、失败、过期与结果未知，
提案完成不等于已发出声音。
"""
from __future__ import annotations

import hashlib
import secrets
import time
from datetime import datetime, timedelta

from pydantic import ValidationError
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.config import settings
from app.core.errors import AppError, not_found
from app.core.logging import get_logger
from app.database import writer_session
from app.models.entities import (
    ClassroomBroadcast,
    ClassroomCamera,
    ClassroomDevice,
    ClassroomDeviceCommand,
    ClassroomMediaSession,
)
from app.schemas.classroom import (
    COMMAND_PAYLOADS,
    CommandChannelResult,
    DeviceHeartbeat,
    DeviceHello,
    DeviceOutputRequest,
    DevicePairRequest,
    VolumeSetRequest,
)
from app.services import classroom_channel, deletions
from app.services.class_student import get_class
from app.services.common import audit
from app.utils.time import now

PAIRING_ALPHABET = "ABCDEFGHJKMNPQRSTVWXZ23456789"
CREDENTIAL_BYTES = 32
PROTOCOL_VERSION = 1
# 配对端点对终端开放，必须限流；配对码本身有 29^8 空间与短有效期。
PAIR_WINDOW_SECONDS = 300.0
PAIR_MAX_ATTEMPTS = 10
PENDING_STATUSES = ("authorized", "delivered", "executing")
TERMINAL_STATUSES = ("succeeded", "failed", "expired", "unknown")
_DISPLAY_OUTCOME = {"pending": "executing", "shown": "succeeded", "cleared": "succeeded", "unavailable": "failed", "failed": "failed"}
_SPEAK_OUTCOME = {"pending": "executing", "spoken": "succeeded", "skipped": "failed", "unavailable": "failed", "failed": "failed"}

_pair_attempts: dict[str, tuple[int, float]] = {}
# Session-scoped, mirroring db.info["deletion_operation"]: commands wait for their
# authorizing transaction to commit before being pushed to the terminal.
_DISPATCH_KEY = "classroom_dispatch"


def _aware(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value if value.tzinfo else value.replace(tzinfo=now().tzinfo)


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def _canonical_code(value: str) -> str:
    chars = [char for char in value.strip().upper() if char in PAIRING_ALPHABET]
    if len(chars) != 8:
        return ""
    return f"{''.join(chars[:4])}-{''.join(chars[4:])}"


def _new_pairing_code() -> str:
    raw = "".join(secrets.choice(PAIRING_ALPHABET) for _ in range(8))
    return f"{raw[:4]}-{raw[4:]}"


def _throttle_pairing(client_key: str) -> None:
    current = time.monotonic()
    stale = [key for key, (_, started) in _pair_attempts.items() if current - started > PAIR_WINDOW_SECONDS]
    for key in stale:
        _pair_attempts.pop(key, None)
    attempts, started = _pair_attempts.get(client_key, (0, current))
    _pair_attempts[client_key] = (attempts + 1, started)
    if attempts >= PAIR_MAX_ATTEMPTS:
        raise AppError("DEVICE_PAIRING_THROTTLED", "配对码尝试过于频繁，请稍后重试或在网页重新生成", 429)


def _require_protocol(version: int) -> None:
    if version != PROTOCOL_VERSION:
        raise AppError("DEVICE_PROTOCOL_UNSUPPORTED",
                       f"终端协议版本 {version} 与服务器支持的版本 {PROTOCOL_VERSION} 不一致，请更新终端", 409,
                       {"server_protocol_version": PROTOCOL_VERSION})


def get_device(db: Session, class_id: str) -> ClassroomDevice | None:
    return db.scalar(select(ClassroomDevice).where(ClassroomDevice.class_id == class_id))


def require_device(db: Session, class_id: str) -> ClassroomDevice:
    device = get_device(db, class_id)
    if not device:
        raise not_found("教室终端", class_id)
    return device


def is_online(db: Session, device: ClassroomDevice) -> bool:
    """A live socket plus a fresh heartbeat; either alone is not proof of readiness."""
    if not classroom_channel.is_online(device.id) or device.pairing_status != "paired":
        return False
    deadline = now() - timedelta(seconds=settings.classroom.heartbeat_timeout_seconds)
    seen = _aware(device.last_seen_at)
    return seen is not None and seen >= deadline


def require_online(db: Session, class_id: str) -> ClassroomDevice:
    device = require_device(db, class_id)
    if device.pairing_status != "paired":
        raise AppError("DEVICE_NOT_PAIRED", "教室终端尚未配对或凭据已撤销", 409, {"device_id": device.id})
    if not is_online(db, device):
        raise AppError("DEVICE_OFFLINE", "教室终端当前不在线，无法执行实时操作；恢复后不会补播", 409, {"device_id": device.id})
    return device


def public_device(device: ClassroomDevice, *, online: bool, pairing_code: str | None = None) -> dict:
    """Never project credential or pairing hashes to the browser."""
    return {
        "device_id": device.id,
        "class_id": device.class_id,
        "name": device.name,
        "pairing_status": device.pairing_status,
        "pairing_code": pairing_code,
        "pairing_expires_at": device.pairing_expires_at if device.pairing_status == "unpaired" else None,
        "online": online,
        "protocol_version": device.protocol_version,
        "app_version": device.app_version,
        "os_version": device.os_version,
        "capabilities": device.capabilities_json,
        "inventory": device.inventory_json,
        "display_name": device.display_name,
        "audio_output_name": device.audio_output_name,
        "volume_level": device.volume_level,
        "muted": device.muted,
        "last_seen_at": device.last_seen_at,
        "last_error": device.last_error,
        "config_revision": device.config_revision,
    }


def issue_pairing(db: Session, class_id: str, name: str, *, operator_id: str | None = None) -> dict:
    get_class(db, class_id)
    device = get_device(db, class_id)
    if device and device.pairing_status == "paired":
        raise AppError("DEVICE_ALREADY_PAIRED", "本班终端已配对，请先撤销凭据再重新配对", 409, {"device_id": device.id})
    if device is None:
        device = ClassroomDevice(class_id=class_id, name=name)
        db.add(device)
    code = _new_pairing_code()
    device.name = name
    device.pairing_status = "unpaired"
    device.pairing_code_hash = _hash(_canonical_code(code))
    device.pairing_expires_at = now() + timedelta(seconds=settings.classroom.pairing_code_ttl_seconds)
    device.credential_hash = None
    device.last_error = None
    db.flush()
    audit(db, "pair_issue", "classroom_device", device.id, operator_id=operator_id)
    db.commit()
    return {**public_device(device, online=False, pairing_code=code),
            "pairing_ttl_seconds": settings.classroom.pairing_code_ttl_seconds}


def cancel_pairing(db: Session, class_id: str, *, operator_id: str | None = None) -> dict:
    device = require_device(db, class_id)
    if device.pairing_status == "paired":
        raise AppError("DEVICE_ALREADY_PAIRED", "终端已配对，请使用撤销凭据", 409, {"device_id": device.id})
    device.pairing_code_hash = None
    device.pairing_expires_at = None
    audit(db, "pair_cancel", "classroom_device", device.id, operator_id=operator_id)
    db.commit()
    return public_device(device, online=False)


def pair_device(db: Session, data: DevicePairRequest, *, client_key: str = "unknown") -> dict:
    """Exchange a one-time pairing code for this device's own credential."""
    _throttle_pairing(client_key)
    _require_protocol(data.protocol_version)
    code = _canonical_code(data.pairing_code)
    invalid = AppError("DEVICE_PAIRING_INVALID", "配对码无效或已被使用，请在网页重新生成", 400)
    if not code:
        raise invalid
    device = db.scalar(
        select(ClassroomDevice).where(
            ClassroomDevice.pairing_code_hash == _hash(code), ClassroomDevice.pairing_status == "unpaired"
        )
    )
    if not device:
        raise invalid
    deletions.require_available(db, "class", device.class_id)
    expires = _aware(device.pairing_expires_at)
    if expires is None or expires <= now():
        device.pairing_code_hash = None
        device.pairing_expires_at = None
        db.commit()
        raise AppError("DEVICE_PAIRING_EXPIRED", "配对码已过期，请在网页重新生成", 400)
    credential = secrets.token_urlsafe(CREDENTIAL_BYTES)
    device.pairing_status = "paired"
    device.pairing_code_hash = None
    device.pairing_expires_at = None
    device.credential_hash = _hash(credential)
    device.paired_at = now()
    device.revoked_at = None
    device.protocol_version = data.protocol_version
    device.app_version = data.app_version or None
    device.os_version = data.os_version or None
    device.capabilities_json = data.capabilities.model_dump(mode="json")
    device.inventory_json = data.inventory.model_dump(mode="json")
    device.name = data.device_name or device.name
    device.last_error = None
    device.config_revision += 1
    audit(db, "pair", "classroom_device", device.id, operator_type="device")
    db.commit()
    # The credential is returned exactly once and is never stored in plaintext.
    return {"device_id": device.id, "class_id": device.class_id, "credential": credential,
            "protocol_version": PROTOCOL_VERSION, "server_time": now().isoformat(),
            "config_revision": device.config_revision,
            "command_ttl_seconds": settings.classroom.command_ttl_seconds,
            "heartbeat_interval_seconds": max(10, settings.classroom.heartbeat_timeout_seconds // 3)}


def authenticate(db: Session, device_id: str, credential: str) -> ClassroomDevice:
    device = db.get(ClassroomDevice, device_id)
    if not device or device.pairing_status != "paired" or not device.credential_hash:
        raise AppError("DEVICE_UNAUTHORIZED", "终端凭据无效或已撤销", 401)
    if not secrets.compare_digest(device.credential_hash, _hash(credential)):
        raise AppError("DEVICE_UNAUTHORIZED", "终端凭据无效或已撤销", 401)
    return device


def require_paired(db: Session, device_id: str) -> ClassroomDevice:
    """Re-check on every channel message so a revocation takes effect immediately."""
    device = db.get(ClassroomDevice, device_id)
    if not device or device.pairing_status != "paired" or not device.credential_hash:
        raise AppError("DEVICE_UNAUTHORIZED", "终端凭据无效或已撤销", 401, {"device_id": device_id})
    return device


def apply_report(device: ClassroomDevice, *, capabilities=None, inventory=None, display_name=None,
                 audio_output_name=None, volume_level=None, muted=None, app_version=None,
                 os_version=None) -> None:
    if capabilities is not None:
        device.capabilities_json = capabilities.model_dump(mode="json")
    if inventory is not None:
        device.inventory_json = inventory.model_dump(mode="json")
    if display_name:
        device.display_name = display_name
    if audio_output_name:
        device.audio_output_name = audio_output_name
    if volume_level is not None:
        device.volume_level = volume_level
    if muted is not None:
        device.muted = muted
    if app_version:
        device.app_version = app_version
    if os_version:
        device.os_version = os_version


def hello(db: Session, device: ClassroomDevice, data: DeviceHello) -> dict:
    _require_protocol(data.protocol_version)
    # 新连接不能继承旧连接的实时命令，包括服务端重启留下的 authorized 记录。
    expire_device_commands(db, device.id, "DEVICE_DISCONNECTED", commit=False)
    apply_report(device, capabilities=data.capabilities, inventory=data.inventory,
                 display_name=data.display_name, audio_output_name=data.audio_output_name,
                 volume_level=data.volume_level, muted=data.muted,
                 app_version=data.app_version, os_version=data.os_version)
    device.protocol_version = data.protocol_version
    device.last_seen_at = now()
    device.last_error = None
    db.commit()
    from app.services.classroom_streaming import device_state
    return {"type": "welcome", "device_id": device.id, "class_id": device.class_id,
            "media": device_state(db, device),
            "output": {"display_name": device.display_name, "audio_output_name": device.audio_output_name},
            "protocol_version": PROTOCOL_VERSION, "server_time": now().isoformat(),
            "config_revision": device.config_revision,
            "command_ttl_seconds": settings.classroom.command_ttl_seconds,
            "heartbeat_interval_seconds": max(10, settings.classroom.heartbeat_timeout_seconds // 3)}


def heartbeat(db: Session, device: ClassroomDevice, data: DeviceHeartbeat) -> dict:
    apply_report(device, capabilities=data.capabilities, inventory=data.inventory,
                 display_name=data.display_name, audio_output_name=data.audio_output_name,
                 volume_level=data.volume_level, muted=data.muted)
    device.last_seen_at = now()
    if data.camera_state:
        _apply_camera_state(db, device, data.camera_state)
    db.commit()
    from app.services.classroom_streaming import device_state
    return {"type": "heartbeat_ack", "server_time": now().isoformat(), "config_revision": device.config_revision,
            "media": device_state(db, device)}


def _apply_camera_state(db: Session, device: ClassroomDevice, state: dict) -> None:
    """终端回报的采集状态是摄像头是否真正可用的唯一来源。"""
    camera = db.scalar(select(ClassroomCamera).where(ClassroomCamera.class_id == device.class_id))
    if not camera or camera.device_id != device.id or camera.status == "disconnected":
        return
    if state.get("camera_id") != camera.id or state.get("config_revision") != camera.config_revision:
        return
    identifier = str(state.get("identifier") or "")
    if camera.device_identifier and identifier != camera.device_identifier:
        return
    reported = str(state.get("state") or "").strip().lower()
    if reported == "connected":
        camera.status = "connected"
        camera.connected_at = now()
        camera.last_error = None
    elif reported in {"idle", "stopped"}:
        camera.status = "registered"
        camera.disconnected_at = now()
        camera.last_error = None
    elif reported in {"failed", "unavailable", "busy"}:
        camera.status = "failed"
        camera.disconnected_at = now()
        camera.last_error = str(state.get("error") or reported)[:500]
    if isinstance(state.get("video"), dict):
        camera.video_json = state["video"]
    if isinstance(state.get("audio_capable"), bool):
        camera.audio_capable = state["audio_capable"]


def mark_disconnected(db: Session, device: ClassroomDevice, reason: str | None = None) -> None:
    """Record why the control channel ended. Command outcomes are settled by expire_overdue."""
    device.last_error = reason
    expire_device_commands(db, device.id, "DEVICE_DISCONNECTED", commit=False)
    camera = db.scalar(select(ClassroomCamera).where(ClassroomCamera.device_id == device.id))
    if camera and camera.status == "connected":
        camera.status = "failed"
        camera.last_error = "教室终端离线，采集状态未知"
        camera.disconnected_at = now()
    db.commit()


def send_command(db: Session, device: ClassroomDevice, kind: str, payload: dict, *,
                 broadcast_id: str | None = None, idempotency_key: str | None = None,
                 requested_by: str | None = None, source_type: str = "web") -> ClassroomDeviceCommand:
    """Authorize one fixed command in the caller's transaction; delivery happens after commit."""
    model = COMMAND_PAYLOADS.get(kind)
    if model is None:
        raise AppError("VALIDATION_ERROR", "不支持的终端命令", 400, {"kind": kind, "supported": sorted(COMMAND_PAYLOADS)})
    try:
        normalized = model.model_validate(payload).model_dump(mode="json")
    except ValidationError as exc:
        raise AppError("VALIDATION_ERROR", "终端命令参数校验失败", 422,
                       {"errors": exc.errors(include_url=False, include_context=False)}) from exc
    if idempotency_key:
        existing = find_command(db, idempotency_key)
        if existing:
            # 重试同一次点名不能重复播报；返回已登记的命令与真实状态。
            return existing
    if not is_online(db, device):
        raise AppError("DEVICE_OFFLINE", "教室终端当前不在线，无法执行实时操作；恢复后不会补播", 409, {"device_id": device.id})
    command = ClassroomDeviceCommand(
        class_id=device.class_id, device_id=device.id, kind=kind, payload_json=normalized,
        broadcast_id=broadcast_id, idempotency_key=idempotency_key, status="authorized",
        expires_at=now() + timedelta(seconds=settings.classroom.command_ttl_seconds),
        requested_by=requested_by, source_type=source_type,
    )
    db.add(command)
    db.flush()
    db.info.setdefault(_DISPATCH_KEY, []).append(command.id)
    return command


def dispatch(db: Session, command: ClassroomDeviceCommand) -> dict:
    """Push an authorized command over the terminal's own socket and report what really happened.

    `pushed` only means the socket was woken; delivery is confirmed by the terminal.
    """
    if command.status != "authorized":
        return {"command_id": command.id, "kind": command.kind, "status": command.status, "pushed": True}
    if classroom_channel.notify(command.device_id):
        return {"command_id": command.id, "kind": command.kind, "status": command.status, "pushed": True,
                "note": "已推送终端，等待送达与执行回执"}
    command.status = "failed"
    command.error_code = "DEVICE_OFFLINE"
    command.error_message = "控制连接在登记后断开，命令未送达"
    command.completed_at = now()
    _sync_broadcast(db, command, display="failed", speak="failed")
    db.commit()
    return {"command_id": command.id, "kind": command.kind, "status": command.status, "pushed": False,
            "error_code": command.error_code}


def dispatch_queued(db: Session) -> list[dict]:
    """Called after the authorizing transaction commits; safe to call when nothing is queued."""
    outcomes = [dispatch(db, command) for command in
                (db.get(ClassroomDeviceCommand, command_id) for command_id in db.info.pop(_DISPATCH_KEY, []))
                if command is not None]
    return outcomes


def require_delivered(outcomes: list[dict]) -> None:
    """Web callers act on a live socket: an undelivered command is an error, not a success."""
    for outcome in outcomes:
        if not outcome.get("pushed"):
            raise AppError("DEVICE_OFFLINE", "命令已登记但未送达教室终端", 409, outcome)


def find_command(db: Session, idempotency_key: str | None) -> ClassroomDeviceCommand | None:
    if not idempotency_key:
        return None
    return db.scalar(select(ClassroomDeviceCommand).where(ClassroomDeviceCommand.idempotency_key == idempotency_key))


def pending_commands(db: Session, device_id: str) -> list[dict]:
    expire_overdue(db)
    # 同一事务内登记的命令可能共享 created_at；rowid 保证按登记顺序下发，
    # 例如更换摄像头时必须先停旧连接再启新连接。
    rows = db.scalars(
        select(ClassroomDeviceCommand)
        .where(ClassroomDeviceCommand.device_id == device_id, ClassroomDeviceCommand.status == "authorized")
        .order_by(ClassroomDeviceCommand.created_at, text("rowid"))
    )
    return [_wire_command(row) for row in rows]


def _wire_command(command: ClassroomDeviceCommand) -> dict:
    return {"type": "command", "command_id": command.id, "kind": command.kind,
            "payload": command.payload_json, "expires_at": _aware(command.expires_at).isoformat() if command.expires_at else None}


def mark_delivered(db: Session, device: ClassroomDevice, command_id: str) -> ClassroomDeviceCommand | None:
    command = db.get(ClassroomDeviceCommand, command_id)
    if not command or command.device_id != device.id or command.status != "authorized":
        return None
    command.status = "delivered"
    command.delivered_at = now()
    _sync_broadcast(db, command, display="delivered", speak="delivered")
    db.commit()
    return command


def record_result(db: Session, device: ClassroomDevice, result: CommandChannelResult) -> dict:
    expire_overdue(db)
    command = db.get(ClassroomDeviceCommand, result.command_id)
    if not command or command.device_id != device.id:
        raise AppError("COMMAND_NOT_FOUND", "命令不存在或不属于本终端", 404, {"command_id": result.command_id})
    if command.status in TERMINAL_STATUSES:
        # 回执丢失后终端可能重发；已终结的命令不再改写，避免重复播报或翻案。
        return {"type": "command_ack", "command_id": command.id, "status": command.status, "duplicate": True}
    timestamp = now()
    if result.state == "started":
        command.status = "executing"
        command.started_at = command.started_at or timestamp
        _sync_broadcast(db, command, display="executing", speak="executing")
    else:
        command.status = result.state
        command.completed_at = timestamp
        command.result_json = {"display": result.display, "speak": result.speak, "state": result.state}
        command.error_code = result.error_code or None
        command.error_message = result.error_message or None
        fallback = "unknown" if result.state in {"succeeded", "unknown"} else "failed"
        _sync_broadcast(db, command,
                        display="unknown" if result.display == "pending" else _DISPLAY_OUTCOME.get(result.display or "", fallback),
                        speak="unknown" if result.speak == "pending" else _SPEAK_OUTCOME.get(result.speak or "", fallback))
        if command.status == "unknown":
            command.error_code = command.error_code or "COMMAND_RESULT_UNKNOWN"
    db.commit()
    return {"type": "command_ack", "command_id": command.id, "status": command.status, "duplicate": False}


def _sync_broadcast(db: Session, command: ClassroomDeviceCommand, *, display: str | None, speak: str | None) -> None:
    if not command.broadcast_id or command.kind != "broadcast.show":
        return
    broadcast = db.get(ClassroomBroadcast, command.broadcast_id)
    if not broadcast:
        return
    if display:
        broadcast.display_status = display
    if speak:
        broadcast.speak_status = speak
    if broadcast.display_status in TERMINAL_STATUSES and broadcast.speak_status in TERMINAL_STATUSES:
        broadcast.finished_at = now()


def expire_overdue(db: Session, *, commit: bool = True) -> int:
    """Give every pending command a definite outcome.

    Two triggers: the TTL passed, or the terminal is no longer on the control channel.
    The second one also covers a server restart, when the in-memory registry is empty.
    已开始执行却没有回执的记为结果未知，绝不自动重播。
    """
    if db.info.get("read_only"):
        # GET 使用读连接；到期维护仍须经过全局单写者，避免并发清扫覆盖回执。
        with writer_session() as writer:
            changed = expire_overdue(writer)
        db.expire_all()
        return changed
    current = now()
    rows = list(db.scalars(
        select(ClassroomDeviceCommand).where(ClassroomDeviceCommand.status.in_(PENDING_STATUSES))
    ))
    changed = 0
    for command in rows:
        expires = _aware(command.expires_at)
        ttl_passed = expires is not None and expires <= current
        if not ttl_passed and classroom_channel.is_online(command.device_id):
            continue
        if command.status == "executing":
            command.status = "unknown"
            command.error_code = "COMMAND_RESULT_UNKNOWN"
            command.error_message = "终端已开始执行但未回执，结果未知；不会自动重播"
        elif ttl_passed:
            command.status = "expired"
            command.error_code = "COMMAND_EXPIRED"
            command.error_message = "终端未在有效期内回执"
        else:
            command.status = "expired"
            command.error_code = "DEVICE_DISCONNECTED"
            command.error_message = "控制连接已结束，命令未确认执行结果"
        command.completed_at = current
        _sync_broadcast(db, command, display=command.status, speak=command.status)
        changed += 1
    if changed and commit:
        db.commit()
    return changed


def expire_device_commands(db: Session, device_id: str, reason: str, *, commit: bool = True) -> int:
    current = now()
    rows = list(db.scalars(
        select(ClassroomDeviceCommand).where(
            ClassroomDeviceCommand.device_id == device_id, ClassroomDeviceCommand.status.in_(PENDING_STATUSES))
    ))
    for command in rows:
        command.status = "unknown" if command.status == "executing" else "expired"
        command.completed_at = current
        command.error_code = reason
        command.error_message = "终端连接结束，命令结果未确认"
        _sync_broadcast(db, command, display=command.status, speak=command.status)
    if rows and commit:
        db.commit()
    return len(rows)


def capability_warnings(device: ClassroomDevice) -> list[str]:
    """能力缺失要明确提示，但屏幕显示与播报是两件事，不互相阻塞。"""
    capabilities = device.capabilities_json or {}
    warnings = []
    if not capabilities.get("display", False):
        warnings.append("终端未报告可用显示屏，屏幕显示可能失败")
    if not capabilities.get("speak", False):
        warnings.append("终端未报告可用语音输出，播报可能失败")
    elif not capabilities.get("chinese_tts", False):
        warnings.append("终端未确认可用中文语音，请先在 Windows 安装中文 TTS")
    return warnings


def set_volume(db: Session, class_id: str, data: VolumeSetRequest, *, requested_by: str | None = None,
               source_type: str = "web", commit: bool = True) -> dict:
    """调整教室 Windows 系统音量。返回登记状态；设备实际值只能来自终端回执。"""
    ceiling = settings.classroom.volume_ceiling
    if data.volume is not None and data.volume > ceiling:
        raise AppError("VOLUME_ABOVE_CEILING", f"教室音量上限为 {ceiling}%，请降低后再试", 422,
                       {"volume": data.volume, "ceiling": ceiling})
    device = require_online(db, class_id)
    if not (device.capabilities_json or {}).get("volume_control", False):
        raise AppError("VOLUME_UNSUPPORTED", "教室终端未报告可调音量能力，无法执行音量操作", 409,
                       {"device_id": device.id})
    command = send_command(db, device, "volume.set",
                           {"volume": data.volume, "mute": data.mute,
                            "restore_after_broadcast": data.restore_after_broadcast},
                           requested_by=requested_by, source_type=source_type)
    if commit:
        db.commit()
        require_delivered(dispatch_queued(db))
    return {"class_id": class_id, "command_id": command.id, "command": public_command(command),
            "requested_volume": data.volume, "requested_mute": data.mute,
            "reported_volume": device.volume_level, "reported_muted": device.muted,
            "note": "已登记音量指令；设备实际值以终端回执为准，不将登记成功当作已生效"}


def test_device(db: Session, class_id: str, kind: str, *, requested_by: str | None = None) -> dict:
    """显示测试与播报测试。测试文本由服务器固定生成，不接受自由指令。"""
    device = require_online(db, class_id)
    command_kind = "device.test_display" if kind == "display" else "device.test_speak"
    text = "ClassClaw 教室终端显示测试" if kind == "display" else "ClassClaw 教室终端播报测试"
    command = send_command(db, device, command_kind, {"text": text}, requested_by=requested_by)
    db.commit()
    require_delivered(dispatch_queued(db))
    return {"class_id": class_id, "kind": kind, "text": text,
            "warnings": capability_warnings(device), "command": public_command(command)}


def configure_output(db: Session, class_id: str, data: DeviceOutputRequest, *, requested_by: str | None = None) -> dict:
    """预选显示屏与扬声器。取值必须来自终端上报的清单，不接受任意字符串。"""
    device = require_online(db, class_id)
    inventory = device.inventory_json or {}
    reported_displays = set((device.capabilities_json or {}).get("displays") or [])
    displays = _reported_names(inventory.get("displays")) | reported_displays
    speakers = _reported_names(inventory.get("speakers"))
    payload: dict[str, str | None] = {}
    if data.display_name:
        if data.display_name not in displays:
            raise AppError("DEVICE_OUTPUT_UNAVAILABLE", "所选显示屏不在终端上报的清单中", 422,
                           {"display_name": data.display_name, "available": sorted(displays)})
        payload["display_name"] = data.display_name
    if data.audio_output_name:
        if data.audio_output_name not in speakers:
            raise AppError("DEVICE_OUTPUT_UNAVAILABLE", "所选扬声器不在终端上报的清单中", 422,
                           {"audio_output_name": data.audio_output_name, "available": sorted(speakers)})
        payload["audio_output_name"] = data.audio_output_name
    command = send_command(db, device, "device.configure", payload, requested_by=requested_by)
    device.config_revision += 1
    db.commit()
    require_delivered(dispatch_queued(db))
    return {"class_id": class_id, **payload, "config_revision": device.config_revision,
            "command": public_command(command),
            "note": "已下发输出设备设置；实际生效值以终端下一次心跳回报为准"}


def _reported_names(rows) -> set[str]:
    """Inventory entries are dicts with a label; tolerate plain strings too."""
    names = set()
    for row in rows or []:
        if isinstance(row, str):
            names.add(row)
        elif isinstance(row, dict):
            for key in ("name", "label", "identifier", "id"):
                value = row.get(key)
                if isinstance(value, str) and value:
                    names.add(value)
                    break
    return names


def public_command(command: ClassroomDeviceCommand) -> dict:
    return {"command_id": command.id, "kind": command.kind, "status": command.status,
            "broadcast_id": command.broadcast_id, "expires_at": command.expires_at,
            "delivered_at": command.delivered_at, "started_at": command.started_at,
            "completed_at": command.completed_at, "result": command.result_json,
            "error_code": command.error_code, "error_message": command.error_message,
            "requested_by": command.requested_by, "source_type": command.source_type,
            "created_at": command.created_at}


def revoke(db: Session, class_id: str, *, operator_id: str | None = None, reason: str = "revoked") -> dict:
    """撤销终端凭据：断开控制连接、撤销观看租约、终止未执行命令。不影响班级 Agent。"""
    device = require_device(db, class_id)
    classroom_channel.close(device.id)
    _revoke_media(db, device, reason)
    expire_device_commands(db, device.id, "DEVICE_REVOKED", commit=False)
    camera = db.scalar(select(ClassroomCamera).where(ClassroomCamera.class_id == class_id))
    if camera and camera.device_id == device.id and camera.status != "disconnected":
        camera.status = "failed"
        camera.disconnected_at = now()
        camera.last_error = "教室终端凭据已撤销"
    device.pairing_status = "revoked"
    device.credential_hash = None
    device.pairing_code_hash = None
    device.pairing_expires_at = None
    device.revoked_at = now()
    device.last_error = "凭据已撤销"
    device.config_revision += 1
    audit(db, "revoke", "classroom_device", device.id, operator_id=operator_id)
    db.commit()
    return public_device(device, online=False)


def _revoke_media(db: Session, device: ClassroomDevice, reason: str) -> int:
    camera = db.scalar(select(ClassroomCamera).where(ClassroomCamera.class_id == device.class_id))
    if not camera:
        return 0
    rows = list(db.scalars(select(ClassroomMediaSession).where(
        ClassroomMediaSession.camera_id == camera.id, ClassroomMediaSession.status == "active")))
    for row in rows:
        row.status = "revoked"
        row.released_at = now()
        row.revoke_reason = reason
    return len(rows)


def unbind(db: Session, class_id: str, *, operator_id: str | None = None) -> dict:
    """解绑终端记录本身；班级 Agent、记忆与其他 Channels 不受影响。"""
    device = require_device(db, class_id)
    if device.pairing_status == "paired":
        revoke(db, class_id, operator_id=operator_id, reason="unbound")
        device = require_device(db, class_id)
    classroom_channel.close(device.id)
    _revoke_media(db, device, "device_unbound")
    camera = db.scalar(select(ClassroomCamera).where(ClassroomCamera.class_id == class_id))
    if camera and camera.device_id == device.id:
        camera.device_id = None
    db.delete(device)
    audit(db, "unbind", "classroom_device", device.id, operator_id=operator_id)
    db.commit()
    return {"class_id": class_id, "unbound": True}


def release_class(db: Session, class_id: str) -> dict:
    """班级删除的业务阶段调用：先断连并撤销，再由调用方删除记录。"""
    closed = classroom_channel.close_class(class_id)
    device = get_device(db, class_id)
    revoked = 0
    if device:
        revoked = _revoke_media(db, device, "class_deleted")
        expire_device_commands(db, device.id, "CLASS_DELETED", commit=False)
    get_logger("classroom").info("Classroom release: class=%s sockets=%d media=%d", class_id[:8], closed, revoked)
    return {"sockets_closed": closed, "media_sessions_revoked": revoked}


def device_summary(db: Session, class_id: str) -> dict:
    device = get_device(db, class_id)
    if not device:
        return {"class_id": class_id, "paired": False, "device": None}
    expire_overdue(db)
    return {"class_id": class_id, "paired": device.pairing_status == "paired",
            "device": public_device(device, online=is_online(db, device))}
