"""教室概况聚合：终端、摄像头、观看与最近点名的一次性只读视图。

只基于数据库事实。终端不在线就说不在线，媒体转发没接入就说没有画面，不猜测。
"""
from __future__ import annotations

from sqlalchemy.orm import Session

from app.config import settings
from app.services import classroom_broadcast, classroom_camera, classroom_devices, classroom_media
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
        "recent_broadcasts": classroom_broadcast.recent(db, class_id),
        "note": note,
    }
