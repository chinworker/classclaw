from __future__ import annotations

import asyncio
import json
import mimetypes
import shutil
import tempfile
import time
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

from fastapi import UploadFile
from sqlalchemy.orm import Session

from app.config import settings
from app.core.errors import AppError
from app.schemas.agent_chat import ClassAgentModelUpdate
from app.services import openclaw_provisioning
from app.services.common import audit

_AUDIO_MIMES = {
    "audio/aac",
    "audio/flac",
    "audio/m4a",
    "audio/mp4",
    "audio/mpeg",
    "audio/ogg",
    "audio/wav",
    "audio/webm",
    "audio/x-m4a",
    "audio/x-wav",
}
_AUDIO_SUFFIXES = {".aac", ".flac", ".m4a", ".mp3", ".mp4", ".oga", ".ogg", ".wav", ".webm"}
_SPEECH_DEFAULTS = {
    "deepgram": "nova-3",
    "elevenlabs": "scribe_v2",
    "google": "gemini-3-flash-preview",
    "mistral": "voxtral-mini-latest",
    "openai": "gpt-4o-transcribe",
    "openrouter": "openai/whisper-large-v3-turbo",
    "senseaudio": "senseaudio-asr-pro-1.5-260319",
    "xai": "grok-stt",
}


def _model_primary(value: Any) -> str | None:
    if isinstance(value, str):
        return value
    if isinstance(value, dict) and value.get("primary"):
        return str(value["primary"])
    return None


def _full_model_ref(row: dict[str, Any]) -> str | None:
    model_id = str(row.get("id") or "").strip()
    provider = str(row.get("provider") or "").strip()
    if not model_id:
        return None
    return model_id if "/" in model_id else f"{provider}/{model_id}" if provider else None


def _safe_models(payload: Any) -> list[dict[str, Any]]:
    rows = payload.get("models") if isinstance(payload, dict) else payload
    result: list[dict[str, Any]] = []
    for row in rows if isinstance(rows, list) else []:
        if not isinstance(row, dict):
            continue
        model_ref = _full_model_ref(row)
        if not model_ref:
            continue
        inputs = [str(item) for item in row.get("input") or []]
        result.append(
            {
                "id": model_ref,
                "name": str(row.get("name") or row.get("id") or model_ref),
                "provider": str(row.get("provider") or model_ref.split("/", 1)[0]),
                "input": inputs,
                "available": row.get("available") is not False,
            }
        )
    return result


async def _finish_process(
    process: asyncio.subprocess.Process,
    *,
    timeout_seconds: float,
    cancelled: Callable[[], Awaitable[bool]] | None = None,
) -> tuple[bytes, bytes]:
    task = asyncio.create_task(process.communicate())
    deadline = time.monotonic() + timeout_seconds
    try:
        while True:
            done, _ = await asyncio.wait({task}, timeout=0.1)
            if task in done:
                return task.result()
            if cancelled and await cancelled():
                process.terminate()
                await process.wait()
                task.cancel()
                raise AppError("REQUEST_CANCELLED", "语音识别已取消", 499)
            if time.monotonic() >= deadline:
                process.kill()
                await process.wait()
                task.cancel()
                raise AppError("OPENCLAW_PROCESSING_FAILED", "语音识别超时，请缩短录音后重试", 504)
    finally:
        if not task.done():
            task.cancel()


async def _run_json_command(
    arguments: list[str],
    *,
    timeout_seconds: float = 30,
    cancelled: Callable[[], Awaitable[bool]] | None = None,
) -> Any:
    executable = shutil.which("openclaw")
    if not executable:
        raise AppError("OPENCLAW_PROCESSING_FAILED", "服务器未安装 OpenClaw 命令行工具", 503)
    process = await asyncio.create_subprocess_exec(
        executable,
        *arguments,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await _finish_process(process, timeout_seconds=timeout_seconds, cancelled=cancelled)
    if process.returncode != 0:
        detail = stderr.decode("utf-8", errors="replace").strip()[-1000:]
        raise AppError("OPENCLAW_PROCESSING_FAILED", "OpenClaw 语音识别失败，请检查模型凭据和模型名称", 502, {"error": detail})
    try:
        return json.loads(stdout.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise AppError("OPENCLAW_PROCESSING_FAILED", "OpenClaw 返回了无法解析的结果", 502) from exc


def _speech_models(config: dict[str, Any], auth_payload: Any) -> list[dict[str, Any]]:
    auth_rows = auth_payload.get("providers") if isinstance(auth_payload, dict) else []
    configured_providers = {
        str(row.get("provider") or "")
        for row in auth_rows or []
        if isinstance(row, dict) and row.get("provider") and row.get("status") not in {"error", "expired", "missing"}
    }
    audio = (((config.get("tools") or {}).get("media") or {}).get("audio") or {})
    configured_entries = audio.get("models") if isinstance(audio.get("models"), list) else []
    choices = dict(_SPEECH_DEFAULTS)
    for row in configured_entries:
        if not isinstance(row, dict):
            continue
        provider = str(row.get("provider") or "").strip()
        model = str(row.get("model") or "").strip()
        if provider and model:
            choices[provider] = model
            configured_providers.add(provider)
    return [
        {
            "id": f"{provider}/{model}",
            "provider": provider,
            "name": model,
            "configured": provider in configured_providers,
            "available": True,
        }
        for provider, model in choices.items()
    ]


def _agent_row(config: dict[str, Any], agent_id: str) -> dict[str, Any] | None:
    for row in (config.get("agents") or {}).get("list") or []:
        if isinstance(row, dict) and str(row.get("id") or row.get("agentId")) == agent_id:
            return row
    return None


async def class_agent_model_settings(db: Session, class_id: str) -> dict[str, Any]:
    binding = openclaw_provisioning.get_binding(db, class_id)
    if not binding.openclaw_agent_id:
        raise AppError("OPENCLAW_AGENT_REQUIRED", "本班专属 Agent 尚未创建", 409)
    snapshot, models_payload, auth_payload = await asyncio.gather(
        openclaw_provisioning.admin_rpc("config.get"),
        openclaw_provisioning.admin_rpc("models.list"),
        openclaw_provisioning.admin_rpc("models.authStatus"),
    )
    config = snapshot.get("config") or {}
    row = _agent_row(config, binding.openclaw_agent_id)
    if not row:
        raise AppError("OPENCLAW_AGENT_NOT_FOUND", "OpenClaw 配置中找不到本班 Agent", 409)
    models = _safe_models(models_payload)
    defaults = (config.get("agents") or {}).get("defaults") or {}
    explicit_main = _model_primary(row.get("model"))
    default_main = _model_primary(defaults.get("model"))
    return {
        "agent_id": binding.openclaw_agent_id,
        "agent_name": binding.agent_name,
        "configured": {
            "main_model": binding.main_model or explicit_main,
            "image_model": binding.image_model,
            "speech_model": binding.speech_model,
        },
        "effective": {
            "main_model": binding.main_model or explicit_main or default_main,
            "image_model": binding.image_model or "跟随主模型或 OpenClaw 全局图片模型",
            "speech_model": binding.speech_model or "浏览器语音识别",
        },
        "models": models,
        "image_models": [item for item in models if "image" in item["input"]],
        "speech_models": _speech_models(config, auth_payload),
        "config_hash": snapshot.get("hash"),
    }


async def update_class_agent_models(
    db: Session,
    class_id: str,
    data: ClassAgentModelUpdate,
    *,
    operator_id: str | None,
) -> dict[str, Any]:
    changes = data.model_dump(exclude_unset=True)
    current = await class_agent_model_settings(db, class_id)
    binding = openclaw_provisioning.get_binding(db, class_id)
    models = {item["id"]: item for item in current["models"] if item["available"]}
    if data.main_model and data.main_model not in models:
        raise AppError("MODEL_UNAVAILABLE", "所选主模型不在 OpenClaw 当前可用模型中", 422, {"model": data.main_model})
    if data.image_model:
        image = models.get(data.image_model)
        if not image or "image" not in image["input"]:
            raise AppError("MODEL_UNAVAILABLE", "所选图片模型当前不可用或不支持图片输入", 422, {"model": data.image_model})
    if data.speech_model:
        provider = data.speech_model.split("/", 1)[0]
        configured = any(item["provider"] == provider and item["configured"] and item["available"] for item in current["speech_models"])
        if not configured:
            raise AppError(
                "MODEL_UNAVAILABLE",
                "所选语音识别 Provider 尚未配置凭据",
                422,
                {"model": data.speech_model, "provider": provider},
            )

    restart_requested = False
    if "main_model" in changes and data.main_model != current["configured"]["main_model"]:
        snapshot = await openclaw_provisioning.admin_rpc("config.get")
        config = snapshot.get("config") or {}
        rows = list((config.get("agents") or {}).get("list") or [])
        found = False
        for index, row in enumerate(rows):
            if not isinstance(row, dict) or str(row.get("id") or row.get("agentId")) != binding.openclaw_agent_id:
                continue
            updated = dict(row)
            if data.main_model:
                updated["model"] = data.main_model
            else:
                updated.pop("model", None)
            rows[index] = updated
            found = True
            break
        if not found:
            raise AppError("OPENCLAW_AGENT_NOT_FOUND", "OpenClaw 配置中找不到本班 Agent", 409)
        params: dict[str, Any] = {
            "raw": json.dumps({"agents": {"list": rows}}, ensure_ascii=False),
            "replacePaths": ["agents.list"],
            "note": f"Update ClassClaw chat models for {binding.openclaw_agent_id}",
            "restartDelayMs": 500,
        }
        if snapshot.get("hash"):
            params["baseHash"] = snapshot["hash"]
        await openclaw_provisioning.admin_rpc("config.patch", params)
        restart_requested = True

    before = {name: getattr(binding, name) for name in ("main_model", "image_model", "speech_model")}
    for name in ("main_model", "image_model", "speech_model"):
        if name in changes:
            setattr(binding, name, changes[name])
    after = {name: getattr(binding, name) for name in before}
    audit(db, "update_agent_models", "class_agent_binding", binding.id, before=before, after=after, operator_id=operator_id)
    db.commit()
    return {"configured": after, "restart_requested": restart_requested}


def _audio_type(upload: UploadFile) -> tuple[str, str]:
    filename = Path(upload.filename or "voice.webm").name
    suffix = Path(filename).suffix.lower()
    mime = (upload.content_type or mimetypes.guess_type(filename)[0] or "").split(";", 1)[0].lower()
    if mime not in _AUDIO_MIMES or suffix not in _AUDIO_SUFFIXES:
        raise AppError("VALIDATION_ERROR", "只接受 AAC、FLAC、M4A、MP3、MP4、OGG、WAV 或 WebM 录音", 415)
    return filename, suffix


async def transcribe_upload(
    upload: UploadFile,
    *,
    model: str,
    cancelled: Callable[[], Awaitable[bool]] | None = None,
) -> dict[str, str]:
    _, suffix = _audio_type(upload)
    path: Path | None = None
    size = 0
    try:
        with tempfile.NamedTemporaryFile(prefix="classclaw-voice-", suffix=suffix, delete=False) as target:
            path = Path(target.name)
            while chunk := await upload.read(1024 * 1024):
                size += len(chunk)
                if size > settings.max_attachment_bytes:
                    raise AppError("VALIDATION_ERROR", "录音超过大小限制", 413, {"max_bytes": settings.max_attachment_bytes})
                target.write(chunk)
        if size < 1024:
            raise AppError("VALIDATION_ERROR", "录音太短或没有声音，请重新录制", 422)
        payload = await _run_json_command(
            ["infer", "audio", "transcribe", "--file", str(path), "--model", model, "--language", "zh", "--json"],
            timeout_seconds=float(settings.openclaw_timeout_seconds),
            cancelled=cancelled,
        )
        text = payload.get("text") if isinstance(payload, dict) else None
        if not isinstance(text, str) or not text.strip():
            raise AppError("OPENCLAW_PROCESSING_FAILED", "语音识别没有返回文字，请重新录制", 502)
        return {"text": text.strip(), "model": model}
    finally:
        if path:
            path.unlink(missing_ok=True)


async def transcribe_for_class(
    db: Session,
    class_id: str,
    upload: UploadFile,
    *,
    cancelled: Callable[[], Awaitable[bool]] | None = None,
) -> dict[str, str]:
    binding = openclaw_provisioning.get_binding(db, class_id)
    if not binding.speech_model:
        raise AppError("SPEECH_MODEL_REQUIRED", "请先在模型设置中选择可用的语音识别模型", 409)
    return await transcribe_upload(upload, model=binding.speech_model, cancelled=cancelled)
