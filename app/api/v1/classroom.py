"""教室终端与实时监控的网页/Agent 侧接口。

路由只做 HTTP、身份和班级归属校验；组合句子、冻结文本、命令账本、摄像头唯一性
与观看租约都在 Service 层。
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy.orm import Session

from app.core.responses import ok
from app.core.security import principal_from_request, require_owned_class
from app.database import get_db
from app.schemas.classroom import (
    BroadcastComposeRequest,
    CameraConnectRequest,
    DeviceOutputRequest,
    DeviceTestRequest,
    MediaSessionCreate,
    PairingIssueRequest,
    VolumeSetRequest,
)
from app.services import classroom, classroom_broadcast, classroom_camera, classroom_devices, classroom_media

router = APIRouter(prefix="/classes/{class_id}/classroom", tags=["教室终端与实时监控"])


def _actor(request: Request) -> str:
    principal = principal_from_request(request)
    return principal.username


# ---------- 概况 ----------


@router.get("/status")
def classroom_status(request: Request, class_id: str, db: Session = Depends(get_db)):
    require_owned_class(request, class_id)
    return ok(request, classroom.overview(db, class_id))


# ---------- 终端配对与凭据 ----------


@router.post("/pairing", status_code=201)
def pairing_issue(request: Request, class_id: str, body: PairingIssueRequest, db: Session = Depends(get_db)):
    require_owned_class(request, class_id)
    principal = principal_from_request(request)
    result = classroom_devices.issue_pairing(db, class_id, body.name, operator_id=principal.user_id)
    return ok(request, result, "配对码已生成，请在教室终端输入；只显示一次", 201)


@router.delete("/pairing")
def pairing_cancel(request: Request, class_id: str, db: Session = Depends(get_db)):
    require_owned_class(request, class_id)
    principal = principal_from_request(request)
    return ok(request, classroom_devices.cancel_pairing(db, class_id, operator_id=principal.user_id), "待配对码已作废")


@router.post("/device/revoke")
def device_revoke(request: Request, class_id: str, db: Session = Depends(get_db)):
    require_owned_class(request, class_id)
    principal = principal_from_request(request)
    return ok(request, classroom_devices.revoke(db, class_id, operator_id=principal.user_id),
              "终端凭据已撤销，既有连接与观看会话已终止")


@router.delete("/device")
def device_unbind(request: Request, class_id: str, db: Session = Depends(get_db)):
    require_owned_class(request, class_id)
    principal = principal_from_request(request)
    return ok(request, classroom_devices.unbind(db, class_id, operator_id=principal.user_id),
              "终端已解绑；班级 Agent、记忆与其他渠道不受影响")


@router.post("/device/output")
def device_output(request: Request, class_id: str, body: DeviceOutputRequest, db: Session = Depends(get_db)):
    require_owned_class(request, class_id)
    return ok(request, classroom_devices.configure_output(db, class_id, body, requested_by=_actor(request)),
              "输出设备设置已下发")


@router.post("/device/test")
def device_test(request: Request, class_id: str, body: DeviceTestRequest, db: Session = Depends(get_db)):
    require_owned_class(request, class_id)
    return ok(request, classroom_devices.test_device(db, class_id, body.kind, requested_by=_actor(request)),
              "测试指令已下发；结果以终端回执为准")


# ---------- 点名广播 ----------


@router.post("/broadcasts/preview")
def broadcast_preview(request: Request, class_id: str, body: BroadcastComposeRequest, db: Session = Depends(get_db)):
    require_owned_class(request, class_id)
    device = classroom_devices.get_device(db, class_id)
    result = classroom_broadcast.preview(db, class_id, body)
    result["warnings"] = classroom_devices.capability_warnings(device) if device else ["本班尚未配对教室终端"]
    result["device_online"] = classroom_devices.is_online(db, device) if device else False
    return ok(request, result, "预览已生成，尚未下发终端")


@router.post("/broadcasts", status_code=201)
def broadcast_send(request: Request, class_id: str, body: BroadcastComposeRequest, db: Session = Depends(get_db)):
    require_owned_class(request, class_id)
    result = classroom_broadcast.send(db, class_id, body, requested_by=_actor(request))
    return ok(request, result, "已下发教室终端；显示与播报结果以终端回执为准", 201)


@router.get("/broadcasts")
def broadcast_list(request: Request, class_id: str, page: int = Query(1, ge=1),
                   page_size: int = Query(20, ge=1, le=100), db: Session = Depends(get_db)):
    require_owned_class(request, class_id)
    return ok(request, classroom_broadcast.list_broadcasts(db, class_id, page=page, page_size=page_size))


@router.get("/broadcasts/{broadcast_id}")
def broadcast_get(request: Request, class_id: str, broadcast_id: str, db: Session = Depends(get_db)):
    require_owned_class(request, class_id)
    classroom_devices.expire_overdue(db)
    return ok(request, classroom_broadcast.public_broadcast(db, classroom_broadcast.get_broadcast(db, class_id, broadcast_id)))


@router.post("/broadcasts/{broadcast_id}/stop")
def broadcast_stop(request: Request, class_id: str, broadcast_id: str, db: Session = Depends(get_db)):
    require_owned_class(request, class_id)
    classroom_broadcast.get_broadcast(db, class_id, broadcast_id)
    return ok(request, classroom_broadcast.control(db, class_id, "broadcast.stop", broadcast_id=broadcast_id,
                                                   requested_by=_actor(request)), "停止播报指令已下发")


@router.post("/broadcasts/{broadcast_id}/clear")
def broadcast_clear(request: Request, class_id: str, broadcast_id: str, db: Session = Depends(get_db)):
    require_owned_class(request, class_id)
    classroom_broadcast.get_broadcast(db, class_id, broadcast_id)
    return ok(request, classroom_broadcast.control(db, class_id, "broadcast.clear", broadcast_id=broadcast_id,
                                                   requested_by=_actor(request)), "清除屏幕指令已下发")


# ---------- 音量 ----------


@router.post("/volume")
def volume_set(request: Request, class_id: str, body: VolumeSetRequest, db: Session = Depends(get_db)):
    require_owned_class(request, class_id)
    return ok(request, classroom_devices.set_volume(db, class_id, body, requested_by=_actor(request)),
              "音量指令已下发；设备实际值以终端回执为准")


# ---------- 摄像头 ----------


@router.get("/camera")
def camera_get(request: Request, class_id: str, db: Session = Depends(get_db)):
    require_owned_class(request, class_id)
    return ok(request, classroom_camera.status(db, class_id))


@router.post("/camera", status_code=201)
def camera_connect(request: Request, class_id: str, body: CameraConnectRequest, db: Session = Depends(get_db)):
    require_owned_class(request, class_id)
    principal = principal_from_request(request)
    result = classroom_camera.connect(db, class_id, body, operator_id=principal.user_id, requested_by=principal.username)
    message = "摄像头已更换；旧观看会话已撤销" if result["replaced"] else "摄像头已登记"
    return ok(request, result, message, 201)


@router.delete("/camera")
def camera_disconnect(request: Request, class_id: str, expected_revision: int | None = Query(None, ge=1),
                      db: Session = Depends(get_db)):
    require_owned_class(request, class_id)
    principal = principal_from_request(request)
    return ok(request, classroom_camera.disconnect(db, class_id, expected_revision=expected_revision,
                                                   operator_id=principal.user_id), "摄像头已断开；点名广播与音量仍可用")


# ---------- 观看会话 ----------


@router.get("/media-sessions")
def media_list(request: Request, class_id: str, db: Session = Depends(get_db)):
    require_owned_class(request, class_id)
    return ok(request, classroom_media.list_sessions(db, class_id))


@router.post("/media-sessions", status_code=201)
def media_issue(request: Request, class_id: str, body: MediaSessionCreate, db: Session = Depends(get_db)):
    require_owned_class(request, class_id)
    principal = principal_from_request(request)
    camera = classroom_camera.require_camera(db, class_id)
    result = classroom_media.issue(db, class_id, camera, user_id=principal.user_id, audio=body.audio,
                                   surface=body.surface, created_by=principal.username)
    return ok(request, result, "观看会话已创建；令牌只显示一次", 201)


@router.post("/media-sessions/{session_id}/renew")
def media_renew(request: Request, class_id: str, session_id: str, db: Session = Depends(get_db)):
    require_owned_class(request, class_id)
    principal = principal_from_request(request)
    return ok(request, classroom_media.renew(db, session_id, user_id=principal.user_id, is_admin=principal.is_admin),
              "观看会话已续期")


@router.delete("/media-sessions/{session_id}")
def media_release(request: Request, class_id: str, session_id: str, db: Session = Depends(get_db)):
    require_owned_class(request, class_id)
    principal = principal_from_request(request)
    return ok(request, classroom_media.release(db, session_id, user_id=principal.user_id, is_admin=principal.is_admin),
              "已离开观看；最后一名观看者离开后将停止转发")
