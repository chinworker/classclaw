"""点名广播：把「称谓＋时间＋事项」冻结成完整句子，再登记终端命令。

这不是考勤：不判断到场情况，不生成值日安排，也不记录任务已完成。屏幕显示与
扬声器播报使用同一段冻结文本和同一顺序，发送后客户端不能再补全或改写。
"""
from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import settings
from app.core.errors import AppError, not_found
from app.models.entities import ClassroomBroadcast, ClassroomDeviceCommand, Student
from app.schemas.classroom import BroadcastComposeRequest
from app.services import classroom_devices
from app.services.class_student import get_class
from app.services.student_ordering import student_order_by

TEMPLATE = "请{称谓}{时间}{谓词}。"
DEFAULT_SALUTATION = "同学"
DEFAULT_TIME_PHRASE = "现在"
STOP_KINDS = {"broadcast.stop", "broadcast.clear"}


def _roster(db: Session, class_id: str, student_ids: list[str]) -> list[Student]:
    rows = list(db.scalars(
        select(Student)
        .where(Student.class_id == class_id, Student.deleted_at.is_(None), Student.id.in_(student_ids))
        .order_by(*student_order_by())
    ))
    if len(rows) != len(set(student_ids)):
        found = {row.id for row in rows}
        missing = [student_id for student_id in student_ids if student_id not in found]
        raise AppError("STUDENT_NOT_FOUND", "所选学生不存在、已删除或不属于本班", 404, {"student_ids": missing})
    return rows


def _recipients(rows: list[Student]) -> list[dict[str, str]]:
    # 终端只收到学号和姓名，不收到学生 UUID。
    return [{"student_no": row.student_no, "name": row.name} for row in rows]


def _salutation_for(rows: list[Student], salutation: str) -> str:
    return "、".join(row.name for row in rows) + salutation


def compose(db: Session, class_id: str, data: BroadcastComposeRequest) -> dict:
    """Build the frozen segments. Deterministic: the same input yields the same sentences."""
    get_class(db, class_id)
    limits = settings.classroom
    segments: list[dict] = []
    salutation = time_phrase = predicate = None
    if data.mode == "custom":
        text = data.text
        if not text:
            raise AppError("VALIDATION_ERROR", "自定义句子不能为空", 422)
        segments = [{"index": 0, "text": text, "recipients": []}]
    else:
        rows = _roster(db, class_id, data.student_ids)
        salutation = data.salutation
        time_phrase = data.time_phrase if data.time_phrase else ""
        predicate = data.predicate
        if data.merge_mode == "per_student":
            segments = [
                {"index": index, "text": TEMPLATE.format(称谓=_salutation_for([row], salutation), 时间=time_phrase, 谓词=predicate),
                 "recipients": _recipients([row])}
                for index, row in enumerate(rows)
            ]
        else:
            segments = [{"index": 0, "text": TEMPLATE.format(称谓=_salutation_for(rows, salutation), 时间=time_phrase, 谓词=predicate),
                         "recipients": _recipients(rows)}]
    if len(segments) > limits.broadcast_max_segments:
        raise AppError("BROADCAST_TOO_MANY_SEGMENTS",
                       f"逐人播报一次最多 {limits.broadcast_max_segments} 句，请减少所选学生或改用合并播报", 422,
                       {"segment_count": len(segments)})
    for segment in segments:
        if not segment["text"]:
            raise AppError("VALIDATION_ERROR", "广播句子不能为空", 422)
        if len(segment["text"]) > limits.broadcast_max_chars:
            raise AppError("BROADCAST_TOO_LONG",
                           f"广播句子最多 {limits.broadcast_max_chars} 字，当前 {len(segment['text'])} 字", 422,
                           {"length": len(segment["text"]), "limit": limits.broadcast_max_chars})
    display_seconds = data.display_seconds if data.display_seconds is not None else limits.display_seconds_default
    if display_seconds > limits.display_seconds_max:
        raise AppError("VALIDATION_ERROR", f"停留时长最多 {limits.display_seconds_max} 秒", 422, {"display_seconds": display_seconds})
    if data.repeat_count > limits.speak_repeat_max:
        raise AppError("VALIDATION_ERROR", f"播报次数最多 {limits.speak_repeat_max} 次", 422, {"repeat_count": data.repeat_count})
    if data.volume is not None and data.volume > limits.volume_ceiling:
        raise AppError("VALIDATION_ERROR", f"教室音量上限为 {limits.volume_ceiling}%", 422, {"volume": data.volume})
    return {
        "class_id": class_id, "mode": data.mode, "merge_mode": data.merge_mode,
        "salutation": salutation, "time_phrase": time_phrase, "predicate": predicate,
        "segments": segments, "display_seconds": display_seconds, "repeat_count": data.repeat_count,
        "gap_seconds": data.gap_seconds, "volume": data.volume, "target_screen": data.target_screen,
    }


def describe(composed: dict) -> dict:
    """Preview text for the teacher and the agent: the exact sentences, in frozen order."""
    texts = [segment["text"] for segment in composed["segments"]]
    recipients = [row for segment in composed["segments"] for row in segment["recipients"]]
    unique = {row["student_no"]: row for row in recipients}
    return {
        "texts": texts,
        "text": "\n".join(texts),
        "segment_count": len(texts),
        "recipients": list(unique.values()),
        "merge_mode": composed["merge_mode"],
        "display_seconds": composed["display_seconds"],
        "repeat_count": composed["repeat_count"],
        "gap_seconds": composed["gap_seconds"],
        "volume": composed["volume"],
        "target_screen": composed["target_screen"],
    }


def preview(db: Session, class_id: str, data: BroadcastComposeRequest) -> dict:
    """网页实时预览：组合但不写入，也不登记命令。"""
    composed = compose(db, class_id, data)
    return {"class_id": class_id, **describe(composed), "mode": composed["mode"]}


def prepare(db: Session, payload: dict) -> tuple[dict, dict]:
    """Freeze an agent-proposed broadcast at preview time; confirmation may not rewrite it."""
    data = BroadcastComposeRequest.model_validate(payload)
    composed = compose(db, data.class_id, data)
    summary = describe(composed)
    confirmation = (
        f"将在教室屏幕显示并用扬声器播报以下 {summary['segment_count']} 句点名广播，内容已冻结：\n"
        + "\n".join(summary["texts"])
        + "\n确认后立即发出。这不是考勤，不会登记到场情况，也不会生成值日或任务记录。"
    )
    return composed, {
        "ready": True, "title": "教室点名广播", "summary": summary, "missing_fields": [], "validation_errors": [],
        "confirmation_message": confirmation,
    }


def _payload(composed: dict) -> dict:
    return {"segments": composed["segments"], "display_seconds": composed["display_seconds"],
            "repeat_count": composed["repeat_count"], "gap_seconds": composed["gap_seconds"],
            "volume": composed["volume"], "target_screen": composed["target_screen"]}


def register(db: Session, composed: dict, *, requested_by: str | None, source_type: str,
             source_message_id: str | None = None, proposal_id: str | None = None,
             idempotency_key: str | None = None) -> tuple[ClassroomBroadcast, ClassroomDeviceCommand]:
    """Authorize a broadcast and its terminal command in one transaction; does not commit."""
    device = classroom_devices.require_online(db, composed["class_id"])
    if composed["target_screen"] and composed["target_screen"] not in (device.capabilities_json or {}).get("displays", []):
        raise AppError("DEVICE_OUTPUT_UNAVAILABLE", "所选显示屏不在终端上报的清单中", 422)
    existing = classroom_devices.find_command(db, idempotency_key)
    if existing:
        # 重试必须在写入广播记录之前就被挡住，否则只会重复留下记录而不重复播报。
        broadcast = db.get(ClassroomBroadcast, existing.broadcast_id) if existing.broadcast_id else None
        if broadcast is None:
            raise AppError("BROADCAST_IDEMPOTENCY_CONFLICT", "该幂等键已被其他命令使用，请换一个新的键", 409,
                           {"idempotency_key": idempotency_key})
        return broadcast, existing
    broadcast = ClassroomBroadcast(
        class_id=composed["class_id"], mode=composed["mode"], merge_mode=composed["merge_mode"],
        salutation=composed["salutation"], time_phrase=composed["time_phrase"], predicate=composed["predicate"],
        segments_json=composed["segments"], display_seconds=composed["display_seconds"],
        repeat_count=composed["repeat_count"], gap_seconds=composed["gap_seconds"], volume=composed["volume"],
        target_screen=composed["target_screen"], created_by=requested_by, source_type=source_type,
        source_message_id=source_message_id, proposal_id=proposal_id,
    )
    db.add(broadcast)
    db.flush()
    command = classroom_devices.send_command(
        db, device, "broadcast.show", _payload(composed), broadcast_id=broadcast.id,
        idempotency_key=idempotency_key, requested_by=requested_by, source_type=source_type,
    )
    return broadcast, command


def send(db: Session, class_id: str, data: BroadcastComposeRequest, *, requested_by: str | None = None,
         idempotency_key: str | None = None) -> dict:
    """网页确定性操作：直接调用领域接口，不经过智能体。"""
    composed = compose(db, class_id, data)
    if data.expected_texts is not None and data.expected_texts != [segment["text"] for segment in composed["segments"]]:
        raise AppError("BROADCAST_PREVIEW_STALE", "名单或广播内容已变化，请重新预览后发送", 409)
    broadcast, command = register(db, composed, requested_by=requested_by, source_type="web",
                                  idempotency_key=idempotency_key)
    db.commit()
    classroom_devices.require_delivered(classroom_devices.dispatch_queued(db))
    return public_broadcast(db, broadcast, command)


def execute(db: Session, payload: dict, *, proposal_id: str | None = None,
            requested_by: str | None = None, source_message_id: str | None = None) -> dict:
    """Fixed executor for a confirmed proposal. Registration only: it is not proof of sound."""
    composed = {key: payload[key] for key in ("class_id", "mode", "merge_mode", "salutation", "time_phrase",
                                              "predicate", "segments", "display_seconds", "repeat_count",
                                              "gap_seconds", "volume", "target_screen")}
    broadcast, command = register(db, composed, requested_by=requested_by, source_type="agent",
                                  source_message_id=source_message_id, proposal_id=proposal_id,
                                  idempotency_key=f"broadcast:proposal:{proposal_id}" if proposal_id else None)
    db.flush()
    return {"broadcast_id": broadcast.id, "command_id": command.id, "status": command.status,
            "texts": [segment["text"] for segment in composed["segments"]],
            "segment_count": len(composed["segments"]),
            "note": "已登记并准备下发；显示与播报结果以教室终端回执为准"}


def control(db: Session, class_id: str, kind: str, *, broadcast_id: str | None = None,
            requested_by: str | None = None) -> dict:
    """停止播报或清除屏幕内容。两者是不同动作，不能互相代替。

    不回头改写原广播的显示/播报状态：那必须由终端对原命令的回执决定。
    """
    if kind not in STOP_KINDS:
        raise AppError("VALIDATION_ERROR", "不支持的广播控制动作", 400, {"kind": kind})
    device = classroom_devices.require_online(db, class_id)
    broadcast = None
    if broadcast_id:
        broadcast = get_broadcast(db, class_id, broadcast_id)
    original = db.scalar(select(ClassroomDeviceCommand).where(
        ClassroomDeviceCommand.broadcast_id == (broadcast.id if broadcast else None),
        ClassroomDeviceCommand.class_id == class_id, ClassroomDeviceCommand.kind == "broadcast.show",
    ))
    if not original or original.device_id != device.id:
        raise AppError("COMMAND_NOT_FOUND", "原广播命令不存在或属于已解绑的终端", 404)
    command = classroom_devices.send_command(db, device, kind, {"target_command_id": original.id},
                                             broadcast_id=broadcast.id if broadcast else None,
                                             requested_by=requested_by)
    db.commit()
    classroom_devices.require_delivered(classroom_devices.dispatch_queued(db))
    return {"class_id": class_id, "kind": kind, "command": classroom_devices.public_command(command)}


def get_broadcast(db: Session, class_id: str, broadcast_id: str) -> ClassroomBroadcast:
    broadcast = db.scalar(select(ClassroomBroadcast).where(
        ClassroomBroadcast.id == broadcast_id, ClassroomBroadcast.class_id == class_id))
    if not broadcast:
        raise not_found("点名广播", broadcast_id)
    return broadcast


def public_broadcast(db: Session, broadcast: ClassroomBroadcast, command: ClassroomDeviceCommand | None = None) -> dict:
    if command is None and broadcast.id:
        command = db.scalar(select(ClassroomDeviceCommand).where(
            ClassroomDeviceCommand.broadcast_id == broadcast.id,
            ClassroomDeviceCommand.kind == "broadcast.show").order_by(ClassroomDeviceCommand.created_at.desc()))
    return {
        "broadcast_id": broadcast.id, "class_id": broadcast.class_id, "mode": broadcast.mode,
        "merge_mode": broadcast.merge_mode, "salutation": broadcast.salutation,
        "time_phrase": broadcast.time_phrase, "predicate": broadcast.predicate,
        "segments": broadcast.segments_json, "texts": [row["text"] for row in broadcast.segments_json],
        "display_seconds": broadcast.display_seconds, "repeat_count": broadcast.repeat_count,
        "gap_seconds": broadcast.gap_seconds, "volume": broadcast.volume,
        "target_screen": broadcast.target_screen, "display_status": broadcast.display_status,
        "speak_status": broadcast.speak_status, "created_by": broadcast.created_by,
        "source_type": broadcast.source_type, "created_at": broadcast.created_at,
        "finished_at": broadcast.finished_at, "status_note": status_note(broadcast),
        "command": classroom_devices.public_command(command) if command else None,
    }


def list_broadcasts(db: Session, class_id: str, *, page: int = 1, page_size: int = 20) -> dict:
    get_class(db, class_id)
    classroom_devices.expire_overdue(db)
    total = db.scalar(select(func.count()).select_from(ClassroomBroadcast).where(ClassroomBroadcast.class_id == class_id)) or 0
    rows = list(db.scalars(
        select(ClassroomBroadcast)
        .where(ClassroomBroadcast.class_id == class_id)
        .order_by(ClassroomBroadcast.created_at.desc(), ClassroomBroadcast.id.desc())
        .offset((page - 1) * page_size).limit(page_size)
    ))
    return {"items": [public_broadcast(db, row) for row in rows], "total": total, "page": page, "page_size": page_size}


def recent(db: Session, class_id: str, *, limit: int = 5) -> list[dict]:
    rows = list(db.scalars(
        select(ClassroomBroadcast).where(ClassroomBroadcast.class_id == class_id)
        .order_by(ClassroomBroadcast.created_at.desc(), ClassroomBroadcast.id.desc()).limit(limit)
    ))
    return [public_broadcast(db, row) for row in rows]


def status_note(broadcast: ClassroomBroadcast) -> str:
    """一句话说明真实结果，避免把「已登记」说成「已播报」。"""
    display = broadcast.display_status
    speak = broadcast.speak_status
    if display == "succeeded" and speak == "succeeded":
        return "教室屏幕已显示，扬声器已播报"
    if display == "succeeded":
        return f"教室屏幕已显示；播报结果为{speak}"
    if speak == "succeeded":
        return f"扬声器已播报；屏幕显示结果为{display}"
    if "unknown" in {display, speak}:
        return "终端已开始执行但未回执，结果未知；不会自动重播"
    if {display, speak} & {"authorized", "delivered", "executing"}:
        return "已下发教室终端，尚未收到显示与播报回执"
    return f"未完成：显示 {display}，播报 {speak}"
