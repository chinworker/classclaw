"""每班唯一摄像头的登记与更换。

绑定只能从网页发起；唯一性由数据库约束和这里的服务校验共同保证。摄像头凭据不
入库明文、不回传浏览器、不写日志，只保存指向服务器侧配置的引用名。
"""
from __future__ import annotations

import ipaddress
import os
import re
import socket
from urllib.parse import urlparse

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.errors import AppError, not_found
from app.core.logging import get_logger
from app.models.entities import ClassroomCamera, ClassroomDevice
from app.schemas.classroom import CameraConnectRequest
from app.services import classroom_devices, classroom_media
from app.services.class_student import get_class
from app.services.common import audit

CREDENTIAL_REF_PATTERN = re.compile(r"^[A-Za-z0-9_]{1,64}$")
# 服务器可能直读网络摄像头；登记时就拒绝环回与云元数据地址，避免把服务器自身当成摄像头。
FORBIDDEN_HOSTS = {"localhost", "127.0.0.1", "::1", "0.0.0.0", "169.254.169.254", "metadata.google.internal"}
STATUS_NOTES = {
    "registered": "已登记，尚未收到采集或转发的连通回执",
    "connected": "摄像头画面可用",
    "disconnected": "摄像头已断开",
}


def credential_env_name(credential_ref: str) -> str:
    return f"CLASSCLAW_CLASSROOM_CAMERA_{credential_ref.upper()}"


def resolve_credential(credential_ref: str | None) -> str | None:
    """Read a camera secret from server-side configuration only. Never returned to callers."""
    if not credential_ref:
        return None
    if not CREDENTIAL_REF_PATTERN.match(credential_ref):
        raise AppError("VALIDATION_ERROR", "凭据引用只能是字母、数字和下划线", 422, {"credential_ref": credential_ref})
    value = os.environ.get(credential_env_name(credential_ref))
    if not value:
        raise AppError("CAMERA_CREDENTIAL_MISSING",
                       f"服务器未配置摄像头凭据 {credential_env_name(credential_ref)}", 503,
                       {"credential_ref": credential_ref})
    return value


def validate_location(location: str, protocol: str) -> str:
    try:
        parsed = urlparse(location)
        host = (parsed.hostname or "").strip().lower().rstrip(".")
        _ = parsed.port
    except ValueError as exc:
        raise AppError("VALIDATION_ERROR", "流地址格式或端口无效", 422) from exc
    if parsed.scheme != protocol:
        raise AppError("VALIDATION_ERROR", f"流地址协议与所选协议 {protocol} 不一致", 422, {"scheme": parsed.scheme})
    if parsed.username or parsed.password:
        raise AppError("CAMERA_CREDENTIAL_IN_URL", "流地址不能内嵌账号或密码，请改用服务器侧凭据引用", 422)
    if not host:
        raise AppError("VALIDATION_ERROR", "流地址缺少主机名", 422)
    address = None
    try:
        address = ipaddress.ip_address(host.split("%", 1)[0])
    except ValueError:
        # inet_aton 不做 DNS 查询；兼容检测 127.1、十六进制和整数形式的 IPv4。
        try:
            address = ipaddress.IPv4Address(socket.inet_aton(host))
        except OSError:
            pass
    if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped:
        address = address.ipv4_mapped
    if (host in FORBIDDEN_HOSTS or host.endswith(".localhost")
            or (address and (address.is_loopback or address.is_link_local or address.is_unspecified or address.is_multicast))):
        raise AppError("CAMERA_LOCATION_FORBIDDEN", "流地址不能指向服务器本机或云元数据地址", 422, {"host": host})
    return location


def get_camera(db: Session, class_id: str) -> ClassroomCamera | None:
    return db.scalar(select(ClassroomCamera).where(ClassroomCamera.class_id == class_id))


def require_camera(db: Session, class_id: str) -> ClassroomCamera:
    camera = get_camera(db, class_id)
    if not camera:
        raise not_found("教室摄像头", class_id)
    return camera


def public_camera(camera: ClassroomCamera) -> dict:
    """Never project the credential reference value; the browser only learns one exists."""
    return {
        "camera_id": camera.id, "class_id": camera.class_id, "name": camera.name,
        "access_path": camera.access_path, "source_kind": camera.source_kind,
        "device_id": camera.device_id, "device_identifier": camera.device_identifier,
        "device_label": camera.device_label, "protocol": camera.protocol, "location": camera.location,
        "microphone_identifier": camera.microphone_identifier,
        "credential_configured": bool(camera.credential_ref),
        "video": camera.video_json, "audio_capable": camera.audio_capable,
        "status": camera.status, "config_revision": camera.config_revision,
        "connected_at": camera.connected_at, "disconnected_at": camera.disconnected_at,
        "last_error": camera.last_error, "updated_at": camera.updated_at,
    }


def _target_device(db: Session, class_id: str, data: CameraConnectRequest) -> ClassroomDevice | None:
    if data.source_kind == "network_stream" and data.access_path == "server_direct":
        return None
    device = classroom_devices.get_device(db, class_id)
    if not device or device.pairing_status != "paired":
        raise AppError("DEVICE_NOT_PAIRED", "该接入路径需要已配对的教室终端；请先在网页完成终端配对", 409,
                       {"class_id": class_id})
    return device


def _capture_command(db: Session, kind: str, camera: ClassroomCamera, device: ClassroomDevice | None,
                     *, audio: bool = False) -> bool:
    """Queue a capture command for the terminal that provides the picture.

    Returns False when the terminal is offline: the caller must report that instead of
    claiming the camera was started or stopped.
    """
    if device is None or camera.source_kind != "windows_device":
        return False  # 网络源由媒体状态或服务器采集进程管理，无设备采集命令。
    if not classroom_devices.is_online(db, device):
        return False
    classroom_devices.send_command(
        db, device, kind,
        {"camera_id": camera.id, "config_revision": camera.config_revision, "identifier": camera.device_identifier,
         "access_path": camera.access_path, "audio": audio},
        requested_by=camera.updated_by, source_type="web",
    )
    return True


def connect(db: Session, class_id: str, data: CameraConnectRequest, *, operator_id: str | None = None,
            requested_by: str | None = None) -> dict:
    """Register the class camera, or replace it when the caller states the current revision."""
    get_class(db, class_id)
    if data.credential_ref:
        resolve_credential(data.credential_ref)
    location = validate_location(data.location, data.protocol) if data.location else None
    # 先解析终端，再构造待插入的行：任何查询都会触发 autoflush。
    device = _target_device(db, class_id, data)
    if data.source_kind == "windows_device" and data.audio_capable:
        microphones = (device.inventory_json or {}).get("microphones", []) if device else []
        if not data.microphone_identifier or not any(row.get("identifier") == data.microphone_identifier for row in microphones):
            raise AppError("CAMERA_MICROPHONE_REQUIRED", "接收现场声音前，请选择终端上报的麦克风", 422)
    if data.access_path == "server_direct" and data.protocol != "rtsp":
        raise AppError("CAMERA_PROTOCOL_UNSUPPORTED", "服务器直读仅支持 RTSP；其他来源请选择 Windows 中转", 422)
    existing = get_camera(db, class_id)
    replaced = existing is not None
    revoked_media = 0
    old_stopped = True
    if existing:
        # 更换必须显式指认替换对象；网页限制不能代替服务端约束。
        if data.expected_revision is None:
            raise AppError("CAMERA_ALREADY_CONNECTED", "本班已连接摄像头；如需更换请确认替换对象并带上当前配置版本", 409,
                           {"current": public_camera(existing)})
        if data.expected_revision != existing.config_revision:
            raise AppError("CAMERA_REVISION_CONFLICT", "摄像头配置已被其他操作更新，请重新载入后再更换", 409,
                           {"expected_revision": data.expected_revision, "current": public_camera(existing)})
        # 先撤销旧媒体会话并停止旧连接，再启用新连接；两条命令按顺序下发。
        revoked_media = classroom_media.revoke_camera(db, existing.id, "camera_replaced", commit=False)
        old_device = db.get(ClassroomDevice, existing.device_id) if existing.device_id else None
        old_stopped = _capture_command(db, "camera.stop", existing, old_device)
        camera = existing
    else:
        camera = ClassroomCamera(class_id=class_id)
        db.add(camera)

    camera.name = data.name
    camera.access_path = data.access_path
    camera.source_kind = data.source_kind
    camera.device_id = device.id if device else None
    camera.device_identifier = data.device_identifier
    camera.device_label = data.device_label
    camera.microphone_identifier = data.microphone_identifier
    camera.protocol = data.protocol
    camera.location = location
    camera.credential_ref = data.credential_ref or None
    camera.video_json = data.video
    camera.audio_capable = data.audio_capable
    camera.status = "registered"
    camera.connected_at = None
    camera.disconnected_at = None
    camera.updated_by = requested_by
    camera.last_error = None
    if replaced:
        camera.config_revision += 1
    db.flush()
    # Media-enabled deployments derive actual start/stop from live viewing leases.
    from app.services.classroom_streaming import configured
    started = False if configured() else _capture_command(db, "camera.start", camera, device)
    if not started and not configured():
        camera.last_error = ("网络流媒体适配器尚未接入，仅保存配置" if camera.source_kind == "network_stream"
                             else "教室终端离线，尚未开始采集；终端恢复后需重新连接摄像头")
    get_logger("classroom").info(
        "Camera %s: class=%s revision=%d revoked_media=%d old_stopped=%s started=%s",
        "replace" if replaced else "connect", class_id[:8], camera.config_revision, revoked_media, old_stopped, started,
    )
    audit(db, "replace" if replaced else "connect", "classroom_camera", camera.id, operator_id=operator_id)
    db.commit()
    classroom_devices.require_delivered(classroom_devices.dispatch_queued(db))
    return {**public_camera(camera), "replaced": replaced, "old_connection_stopped": False if replaced else None,
            "stop_requested": old_stopped if replaced else False, "capture_requested": started,
            "capture_started": False, "revoked_media_sessions": revoked_media}


def disconnect(db: Session, class_id: str, *, expected_revision: int | None = None,
               operator_id: str | None = None) -> dict:
    """断开后点名广播和音量功能仍然可用。"""
    camera = require_camera(db, class_id)
    if expected_revision is not None and expected_revision != camera.config_revision:
        raise AppError("CAMERA_REVISION_CONFLICT", "摄像头配置已被其他操作更新，请重新载入后再断开", 409,
                       {"expected_revision": expected_revision, "current": public_camera(camera)})
    device = db.get(ClassroomDevice, camera.device_id) if camera.device_id else None
    revoked = classroom_media.revoke_camera(db, camera.id, "camera_disconnected", commit=False)
    stopped = _capture_command(db, "camera.stop", camera, device)
    summary = {"camera_id": camera.id, "class_id": class_id, "name": camera.name,
               "config_revision": camera.config_revision, "old_connection_stopped": False, "stop_requested": stopped,
               "revoked_media_sessions": revoked}
    audit(db, "disconnect", "classroom_camera", camera.id, operator_id=operator_id)
    db.delete(camera)
    db.commit()
    classroom_devices.dispatch_queued(db)
    return summary


def status(db: Session, class_id: str) -> dict:
    camera = get_camera(db, class_id)
    if not camera:
        return {"registered": False, "connected": False, "camera": None,
                "note": "本班尚未连接摄像头；连接只能从网页发起，每班最多一路"}
    device = db.get(ClassroomDevice, camera.device_id) if camera.device_id else None
    note = STATUS_NOTES.get(camera.status, camera.status)
    if camera.status == "failed":
        note = f"摄像头不可用：{camera.last_error or '原因未知'}"
    connected = camera.status == "connected" and (device is None or classroom_devices.is_online(db, device))
    if camera.device_id and not connected and camera.status == "connected":
        note = "教室终端离线，采集状态未知"
    return {"registered": True, "connected": connected, "camera": public_camera(camera),
            "device_online": classroom_devices.is_online(db, device) if device else None, "note": note}


def release_class(db: Session, class_id: str) -> dict:
    """班级删除的业务阶段：撤销观看租约，记录由调用方删除。"""
    camera = get_camera(db, class_id)
    if not camera:
        return {"cameras": 0, "media_sessions_revoked": 0}
    return {"cameras": 1, "media_sessions_revoked": classroom_media.revoke_camera(db, camera.id, "class_deleted")}
