"""Bounded private Gateway transport and per-class stale-request protection.

Only opaque attempt identifiers live here. QR secrets and verification codes
are not persisted. All account / class ownership checks remain in API routes.
"""
from __future__ import annotations

import time
import uuid
from collections import OrderedDict
from typing import Any

import httpx

from app.config import settings
from app.core.errors import AppError
from app.services.http_client import gateway_headers, get_http_client

_attempts: OrderedDict[str, tuple[str, float, str | None]] = OrderedDict()


def begin(class_id: str) -> str:
    cutoff = time.monotonic() - 3600
    for key, (_, created, _) in list(_attempts.items()):
        if created < cutoff:
            _attempts.pop(key, None)
    if class_id not in _attempts and len(_attempts) >= 100:
        raise AppError("WECHAT_LOGIN_BUSY", "扫码会话过多，请稍后重试", 503)
    attempt = str(uuid.uuid4())
    _attempts[class_id] = (attempt, time.monotonic(), None)
    return attempt


def capture(class_id: str) -> str | None:
    entry = _attempts.get(class_id)
    return entry[0] if entry else None


def require_current(class_id: str, attempt: str | None) -> None:
    if capture(class_id) != attempt:
        raise AppError("WECHAT_LOGIN_STALE", "二维码已被重新生成，请使用最新二维码", 409)


def accept(class_id: str, attempt: str, login_id: str) -> None:
    require_current(class_id, attempt)
    _attempts[class_id] = (attempt, time.monotonic(), login_id)


def require_login(class_id: str, login_id: str) -> None:
    entry = _attempts.get(class_id)
    # A Gateway attempt may survive a ClassClaw restart; the private route still
    # validates it. But once regeneration begins, reject the preceding login.
    if entry is not None and entry[2] != login_id:
        raise AppError("WECHAT_LOGIN_STALE", "二维码已被重新生成，请使用最新二维码", 409)


async def call(class_id: str, action: str, **params: Any) -> dict[str, Any]:
    headers = gateway_headers()
    timeout_ms = settings.wechat.gateway_start_timeout_seconds * 1000 if action == "start" else settings.wechat.gateway_wait_timeout_seconds * 1000
    body = {"action": action, "classId": class_id, "timeoutMs": timeout_ms, **params}
    try:
        response = await get_http_client().post(
            f"{settings.openclaw_gateway_url}/api/v1/classclaw/wechat-login", headers=headers, json=body,
            timeout=timeout_ms / 1000 + 15,
        )
    except httpx.HTTPError as exc:
        raise AppError("WECHAT_LOGIN_UNAVAILABLE", "微信登录服务暂不可用，请稍后重试", 503) from exc
    if response.status_code == 404:
        raise AppError("WECHAT_PLUGIN_UPDATE_REQUIRED", "请加载新版微信兼容层并重启 Gateway，再重新生成二维码", 503)
    try:
        result = response.json()
    except ValueError as exc:
        raise AppError("WECHAT_RESPONSE_INVALID", "微信登录接口返回了无效响应", 502) from exc
    if not isinstance(result, dict):
        raise AppError("WECHAT_RESPONSE_INVALID", "微信登录接口返回了无效响应", 502)
    if response.status_code != 200 or result.get("ok") is not True:
        # Never echo a provider URL, raw response, QR token or submitted code.
        error = result.get("error")
        code = error.get("code") if isinstance(error, dict) else None
        code = code if isinstance(code, str) else None
        known = {
            "WECHAT_LOGIN_STALE": "二维码已被重新生成，请使用最新二维码",
            "WECHAT_CHALLENGE_STALE": "验证码提示已更新，请刷新绑定状态后重新输入",
            "WECHAT_CODE_INVALID": "请输入手机显示的数字验证码",
            "WECHAT_ACCOUNT_CONFLICT": "该微信账号已用于其他绑定，不能覆盖其消息路由",
            "WECHAT_QR_UNAVAILABLE": "微信未返回可用二维码，请重新生成",
        }
        raise AppError(code if code in known else "WECHAT_LOGIN_UNAVAILABLE", known.get(code, "微信登录暂不可用，请稍后重试"),
                       response.status_code if response.status_code in (400, 409, 422, 503) else 502)
    payload = result.get("payload")
    if not isinstance(payload, dict) or not isinstance(payload.get("loginId"), str):
        raise AppError("WECHAT_RESPONSE_INVALID", "微信登录接口返回了不完整响应", 502)
    try:
        uuid.UUID(payload["loginId"])
    except ValueError as exc:
        raise AppError("WECHAT_RESPONSE_INVALID", "微信登录接口返回了无效登录标识", 502) from exc
    return payload
