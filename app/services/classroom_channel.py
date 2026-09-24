"""终端控制通道的进程内注册表。

SQLite 是唯一的执行账本；这里只保存当前在线的 WebSocket，用于即时投递命令。
注册表不缓存命令，也不做离线积压：终端不在线时上层直接拒绝实时操作。
"""
from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import datetime

from app.core.logging import get_logger
from app.utils.time import now


@dataclass(slots=True)
class TerminalConnection:
    device_id: str
    class_id: str
    send: Callable[[dict], Awaitable[None]]
    loop: asyncio.AbstractEventLoop
    notify: asyncio.Event = field(default_factory=asyncio.Event)
    closing: asyncio.Event = field(default_factory=asyncio.Event)
    connected_at: datetime = field(default_factory=now)


_connections: dict[str, TerminalConnection] = {}


def register(connection: TerminalConnection) -> TerminalConnection | None:
    """Attach a terminal. A second connection for the same device replaces the first."""
    previous = _connections.get(connection.device_id)
    _connections[connection.device_id] = connection
    if previous is not None and previous is not connection:
        _request_close(previous)
    return previous


def unregister(device_id: str, connection: TerminalConnection) -> None:
    if _connections.get(device_id) is connection:
        _connections.pop(device_id, None)


def get(device_id: str) -> TerminalConnection | None:
    return _connections.get(device_id)


def is_online(device_id: str) -> bool:
    return device_id in _connections


def online_device_ids() -> set[str]:
    return set(_connections)


def _on_loop(connection: TerminalConnection, action: Callable[[], None]) -> None:
    """Request handlers run in a worker thread; never touch the loop directly."""
    try:
        connection.loop.call_soon_threadsafe(action)
    except RuntimeError:
        get_logger("classroom").warning("Terminal loop already closed: device=%s", connection.device_id[:8])


def notify(device_id: str) -> bool:
    """Wake the terminal's channel so it pushes newly authorized commands."""
    connection = _connections.get(device_id)
    if connection is None:
        return False
    _on_loop(connection, connection.notify.set)
    return True


def _request_close(connection: TerminalConnection) -> None:
    # 只请求关闭，由通道自己发出关闭帧：直接取消任务会让终端收不到关闭原因。
    _on_loop(connection, connection.closing.set)


def close(device_id: str) -> bool:
    """Ask a terminal to disconnect after its credential is revoked or its class is deleted.

    The registry entry is dropped immediately so the device counts as offline at once;
    the handler then sends the close frame and unregisters itself as a no-op.
    """
    connection = _connections.pop(device_id, None)
    if connection is None:
        return False
    _request_close(connection)
    return True


def close_class(class_id: str) -> int:
    targets = [item.device_id for item in _connections.values() if item.class_id == class_id]
    for device_id in targets:
        close(device_id)
    return len(targets)
