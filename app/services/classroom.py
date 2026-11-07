"""教室概况聚合：终端、摄像头、观看与最近点名的一次性只读视图。

只基于数据库事实。终端不在线就说不在线，媒体转发没接入就说没有画面，不猜测。
"""
from __future__ import annotations

from sqlalchemy.orm import Session

from app.config import settings
from app.core.errors import AppError
from app.schemas.classroom import CameraConnectRequest
from app.services import classroom_broadcast, classroom_camera, classroom_devices, classroom_media, classroom_streaming
from app.services.class_student import get_class
from app.utils.time import now


def overview(db: Session, class_id: str) -> dict:
    get_class(db, class_id)
    classroom_devices.expire_overdue(db)
    classroom_media.expire_overdue(db)
    device = classroom_devices.get_device(db, class_id)
    online = classroom_devices.is_online(db, device) if device else False
    camera_state = classroom_camera.status(db, class_id)
    camera_id = (camera_state.get("camera") or {}).get("camera_id")
    streaming = classroom_media.streaming_state(db, camera_id) if camera_id else None
    if device is None:
        note = "本班尚未配对教室终端；点名广播、音量与监控都需要先完成配对"
    elif device.pairing_status == "revoked":
        note = "教室终端凭据已撤销，请重新配对"
    elif not online:
        note = "教室终端离线；实时点名与音量已拒绝，恢复后不会补播"
    else:
        note = "教室终端在线"
    return {
        "class_id": class_id,
        "server_time": now().isoformat(),
        "device": classroom_devices.public_device(device, online=online) if device else None,
        "device_warnings": classroom_devices.capability_warnings(device) if device else [],
        "camera": camera_state,
        "streaming": streaming,
        "media_provider": settings.classroom.media_provider,
        "media_ready": classroom_streaming.is_ready(),
        "recent_broadcasts": classroom_broadcast.recent(db, class_id),
        "note": note,
    }


def watch(db: Session, class_id: str, *, user_id: str | None, username: str, audio: bool, surface: str, follow_terminal: bool) -> dict:
    camera = classroom_camera.get_camera(db, class_id)
    if follow_terminal and surface == "web":
        if not classroom_streaming.configured():
            raise AppError("MEDIA_NOT_CONFIGURED", "服务器尚未启用媒体转发。Windows 本机预览可独立使用。", 503)
        if not classroom_streaming.is_ready():
            raise AppError("MEDIA_UNAVAILABLE", "服务器媒体服务暂未就绪，稍后自动重试", 503)
        if not camera or camera.source_kind == "windows_device":
            device = classroom_devices.require_online(db, class_id)
            inventory = device.inventory_json or {}
            local = inventory.get("local_capture") or {}
            selected = local.get("camera")
            if selected is not None:
                if not any(row.get("identifier") == selected for row in inventory.get("cameras", [])):
                    raise AppError("CAMERA_SELECTION_REQUIRED", "请在 Classroom Mate 设置中选择可用的摄像头", 409)
                mic = local.get("microphone")
                if not any(row.get("identifier") == mic for row in inventory.get("microphones", [])):
                    mic = None
                if (not camera or camera.device_id != device.id or camera.device_identifier != selected
                        or (camera.microphone_identifier or None) != mic or camera.status == "disconnected"):
                    classroom_camera.connect(db, class_id, CameraConnectRequest(
                        access_path="windows_capture", source_kind="windows_device", device_identifier=selected,
                        microphone_identifier=mic, audio_capable=bool(mic), expected_revision=camera.config_revision if camera else None,
                    ), operator_id=user_id, requested_by=username)
                    camera = classroom_camera.require_camera(db, class_id)
            elif camera is None:
                raise AppError("CAMERA_SELECTION_REQUIRED", "请在 Classroom Mate 设置中选择摄像头", 409)
        elif camera.device_id:
            classroom_devices.require_online(db, class_id)
    if camera is None:
        camera = classroom_camera.require_camera(db, class_id)
    return classroom_media.issue(db, class_id, camera, user_id=user_id, audio=audio, surface=surface, created_by=username)
