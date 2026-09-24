"""教室终端的配对兑换与 WebSocket 控制通道。

终端用自己的设备凭据鉴权，不持有 ClassClaw 全局共享 Token，也不使用用户会话。
命令只从 SQLite 账本读取并即时投递：这里不缓存命令，也不做离线积压。
"""
from __future__ import annotations

import asyncio
import functools
import json
import time
from contextlib import suppress

import anyio
from fastapi import APIRouter, Depends, Request, WebSocket
from pydantic import ValidationError
from sqlalchemy.orm import Session

from app.config import settings
from app.core.errors import AppError
from app.core.logging import get_logger
from app.core.responses import ok
from app.database import get_db, writer_session
from app.models.entities import ClassroomDevice
from app.schemas.classroom import CommandChannelResult, DeviceHeartbeat, DeviceHello, DevicePairRequest
from app.services import classroom_channel, classroom_devices, deletions

router = APIRouter(prefix="/classroom", tags=["教室终端接入"])

HELLO_TIMEOUT_SECONDS = 20.0
CLOSE_PROTOCOL = 4400
CLOSE_UNAUTHORIZED = 4401
CLOSE_IDLE = 4408


def _in_session(fn, *args, **kwargs):
    # 控制通道是长连接，不能整段持有写锁；每次消息只开一个短会话。
    with writer_session() as db:
        return fn(db, *args, **kwargs)


async def _run(fn, *args, **kwargs):
    return await anyio.to_thread.run_sync(functools.partial(_in_session, fn, *args, **kwargs))


def _hello(db: Session, device_id: str, payload: dict) -> dict:
    data = DeviceHello.model_validate(payload)
    device = classroom_devices.authenticate(db, device_id, data.credential)
    deletions.require_available(db, "class", device.class_id)
    classroom_channel.close(device_id)
    return classroom_devices.hello(db, device, data)


def _heartbeat(db: Session, device_id: str, payload: dict) -> dict:
    device = classroom_devices.require_paired(db, device_id)
    return classroom_devices.heartbeat(db, device, DeviceHeartbeat.model_validate(payload))


def _result(db: Session, device_id: str, payload: dict) -> dict:
    device = classroom_devices.require_paired(db, device_id)
    return classroom_devices.record_result(db, device, CommandChannelResult.model_validate(payload))


def _pending(db: Session, device_id: str) -> list[dict]:
    classroom_devices.require_paired(db, device_id)
    return classroom_devices.pending_commands(db, device_id)


def _delivered(db: Session, device_id: str, command_id: str) -> None:
    device = classroom_devices.require_paired(db, device_id)
    classroom_devices.mark_delivered(db, device, command_id)


def _disconnected(db: Session, device_id: str, reason: str | None) -> None:
    device = db.get(ClassroomDevice, device_id)
    if device:
        classroom_devices.mark_disconnected(db, device, reason)


@router.post("/device/pair", status_code=201)
def device_pair(request: Request, body: DevicePairRequest, db: Session = Depends(get_db)):
    """把一次性配对码换成本设备专用凭据。凭据只返回一次，服务器只存哈希。"""
    client_key = request.client.host if request.client else "unknown"
    result = classroom_devices.pair_device(db, body, client_key=client_key)
    return ok(request, result, "配对成功；凭据只显示一次，请在终端加密保存", 201)


async def _reject(websocket: WebSocket, code: int, error_code: str, message: str) -> None:
    with suppress(Exception):
        await websocket.send_json({"type": "error", "code": error_code, "message": message})
        await websocket.close(code=code)


async def _push_pending(websocket: WebSocket, device_id: str) -> None:
    for command in await _run(_pending, device_id):
        await websocket.send_json(command)
        await _run(_delivered, device_id, command["command_id"])
    if settings.classroom.media_provider == "mediamtx":
        await websocket.send_json(await _run(_media_state, device_id))


def _media_state(db: Session, device_id: str) -> dict:
    from app.services.classroom_streaming import device_state
    return device_state(db, classroom_devices.require_paired(db, device_id))


async def _handle_message(websocket: WebSocket, device_id: str, message: dict) -> None:
    text = message.get("text")
    if text is None:
        await _reject(websocket, CLOSE_PROTOCOL, "PROTOCOL_ERROR", "只接受 JSON 文本消息")
        return
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        await _reject(websocket, CLOSE_PROTOCOL, "PROTOCOL_ERROR", "消息不是有效 JSON")
        return
    if not isinstance(payload, dict):
        await _reject(websocket, CLOSE_PROTOCOL, "PROTOCOL_ERROR", "消息必须是 JSON 对象")
        return
    kind = payload.get("type")
    try:
        if kind == "heartbeat":
            await websocket.send_json(await _run(_heartbeat, device_id, payload))
        elif kind == "command_result":
            await websocket.send_json(await _run(_result, device_id, payload))
        else:
            await websocket.send_json({"type": "error", "code": "PROTOCOL_ERROR", "message": f"不支持的消息类型：{kind}"})
    except AppError as exc:
        await _reject(websocket, CLOSE_UNAUTHORIZED, exc.code, exc.message)
    except ValidationError:
        await websocket.send_json({"type": "error", "code": "VALIDATION_ERROR", "message": "消息字段校验失败"})


async def _pump(websocket: WebSocket, connection: classroom_channel.TerminalConnection) -> None:
    idle_timeout = float(settings.classroom.heartbeat_timeout_seconds)
    last_activity = time.monotonic()
    # receive() 一旦被取消可能丢消息，所以只在消费完成后重建，绝不取消在途的接收。
    receive_task = asyncio.create_task(websocket.receive())
    closing_task = asyncio.create_task(connection.closing.wait())
    try:
        while True:
            remaining = idle_timeout - (time.monotonic() - last_activity)
            if remaining <= 0:
                await _reject(websocket, CLOSE_IDLE, "DEVICE_IDLE", "终端心跳超时，连接已关闭")
                return
            notify_task = asyncio.create_task(connection.notify.wait())
            completed, _ = await asyncio.wait({notify_task, receive_task, closing_task}, timeout=remaining,
                                              return_when=asyncio.FIRST_COMPLETED)
            if closing_task in completed or classroom_channel.get(connection.device_id) is not connection:
                notify_task.cancel()
                with suppress(asyncio.CancelledError):
                    await notify_task
                await _reject(websocket, CLOSE_UNAUTHORIZED, "DEVICE_REVOKED", "终端连接已被撤销或替换")
                return
            if notify_task in completed:
                connection.notify.clear()
                await _push_pending(websocket, connection.device_id)
            else:
                notify_task.cancel()
                with suppress(asyncio.CancelledError):
                    await notify_task
            if closing_task in completed:
                await _reject(websocket, CLOSE_UNAUTHORIZED, "DEVICE_REVOKED", "终端凭据已撤销或班级已删除，连接已关闭")
                return
            if receive_task not in completed:
                continue
            message = receive_task.result()
            if message["type"] == "websocket.disconnect":
                return
            last_activity = time.monotonic()
            await _handle_message(websocket, connection.device_id, message)
            receive_task = asyncio.create_task(websocket.receive())
    finally:
        for task in (receive_task, closing_task):
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task


@router.websocket("/device-channel")
async def device_channel(websocket: WebSocket):
    await websocket.accept()
    log = get_logger("classroom")
    device_id: str | None = None
    connection: classroom_channel.TerminalConnection | None = None
    try:
        raw = await asyncio.wait_for(websocket.receive_json(), timeout=HELLO_TIMEOUT_SECONDS)
        if not isinstance(raw, dict) or raw.get("type") != "hello":
            await _reject(websocket, CLOSE_PROTOCOL, "PROTOCOL_ERROR", "首条消息必须是 hello")
            return
        device_id = str(raw.get("device_id") or "")
        welcome = await _run(_hello, device_id, raw)
        connection = classroom_channel.TerminalConnection(
            device_id=device_id, class_id=welcome["class_id"], send=websocket.send_json,
            loop=asyncio.get_running_loop(),
        )
        classroom_channel.register(connection)
        await websocket.send_json(welcome)
        # hello 已终结旧连接的命令；这里只处理本次连接建立后的即时指令。
        connection.notify.set()
        log.info("Classroom terminal connected: device=%s class=%s", device_id[:8], welcome["class_id"][:8])
        await _pump(websocket, connection)
    except AppError as exc:
        await _reject(websocket, CLOSE_UNAUTHORIZED, exc.code, exc.message)
    except ValidationError:
        await _reject(websocket, CLOSE_PROTOCOL, "VALIDATION_ERROR", "终端消息字段校验失败")
    except (TimeoutError, asyncio.CancelledError):
        pass
    except Exception:
        log.exception("Classroom device channel failed: device=%s", (device_id or "?")[:8])
        with suppress(Exception):
            await websocket.close(code=1011)
    finally:
        owned_connection = connection is not None and classroom_channel.get(device_id) is connection
        if connection is not None:
            classroom_channel.unregister(device_id, connection)
        if owned_connection:
            # 只写诊断信息：命令的最终状态由 expire_overdue 权威判定，因此这里
            # 在取消中可以安全放弃等待，不会把命令永远留在“已登记”。
            with suppress(Exception):
                await asyncio.shield(_run(_disconnected, device_id, "控制连接结束"))
