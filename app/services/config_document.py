"""Single-worker configuration documents. No live settings mutation and no secret values."""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import stat
import tempfile
import tomllib
from pathlib import Path
from typing import Any

from pydantic import ValidationError
from sqlalchemy.orm import Session

from app.config import BASE_DIR, DEFAULT_CONFIG_FILE, ConfigurationError, Settings, load_settings, settings
from app.core.errors import AppError
from app.core.security import Principal
from app.schemas.admin import ConfigDocumentCheck, ConfigDocumentRollback, ConfigDocumentUpdate
from app.services.common import audit
from app.services.configuration_catalog import FIELDS, catalog
from app.utils.time import now

_write_lock = asyncio.Lock()
_secret = re.compile(r"(?:token|password)$", re.IGNORECASE)
# Keep the selected path (before resolve) so a symlink cannot silently change the write target.
CONFIG_PATH = Path(os.environ.get("CLASSCLAW_CONFIG_FILE") or DEFAULT_CONFIG_FILE).expanduser()


def _path() -> Path:
    selected = CONFIG_PATH if CONFIG_PATH.is_absolute() else BASE_DIR / CONFIG_PATH
    if selected.is_symlink() or any(parent.is_symlink() for parent in selected.parents):
        raise AppError("CONFIG_WRITE_FAILED", "配置路径不能包含符号链接", 403)
    path = selected.parent.resolve() / selected.name
    if path.exists() and not path.is_file():
        raise AppError("CONFIG_WRITE_FAILED", "配置路径必须是普通文件", 403)
    return path


def _flatten(value: dict, prefix: str = "") -> dict[str, Any]:
    result = {}
    for key, item in value.items():
        path = f"{prefix}.{key}" if prefix else key
        if isinstance(item, dict):
            result.update(_flatten(item, path))
        else:
            result[path] = item
    return result


def _parse(text: str) -> dict:
    try:
        raw = tomllib.loads(text)
    except tomllib.TOMLDecodeError as exc:
        match = re.search(r"at line (\d+), column (\d+)", str(exc))
        line = getattr(exc, "lineno", None) or (int(match[1]) if match else text.count("\n") + 1)
        # Parser exceptions can include user content; only return our own message and location.
        raise AppError("CONFIG_TOML_INVALID", "TOML 语法错误，请检查标记行", 422,
                       {"errors": [{"line": line, "message": "TOML 语法错误"}]}) from exc
    def check_secrets(value: dict) -> None:
        for key, item in value.items():
            if _secret.search(key):
                raise AppError("CONFIG_SECRET_FORBIDDEN", "密钥只能通过服务器环境变量配置", 403)
            if isinstance(item, dict):
                check_secrets(item)
    check_secrets(raw)
    return raw


def _load(path: Path, text: str | None) -> Settings:
    try:
        return load_settings(path, toml_text=text, environ=dict(os.environ))
    except ConfigurationError as exc:
        cause = exc.__cause__
        if isinstance(cause, ValidationError):
            errors = [{"config_path": ".".join(map(str, item["loc"])), "message": item["msg"]}
                      for item in cause.errors(include_input=False, include_url=False)]
        else:
            paths = [path for path in FIELDS if path in str(exc)]
            errors = [{"config_path": path, "message": str(exc)} for path in paths] or [{"message": str(exc)}]
        raise AppError("VALIDATION_ERROR", "配置校验失败", 422, {"errors": errors}) from exc


def _digest(text: str | None) -> str:
    return hashlib.sha256((text or "").encode()).hexdigest()


def _read(path: Path) -> tuple[str | None, Settings, dict]:
    try:
        text = path.read_text(encoding="utf-8") if path.exists() else None
    except (OSError, UnicodeError) as exc:
        raise AppError("CONFIG_WRITE_FAILED", "无法读取配置文件", 500) from exc
    raw = _parse(text or "")
    # An absent default document uses startup defaults (including their original path bases).
    value = _load(path, text or "")
    return text, value, raw


def text_layout(text: str) -> dict:
    """Locate whole valid TOML statements, including multiline strings and inline tables.

    The browser only replaces these spans; it does not implement a TOML parser.
    """
    lines = text.splitlines(keepends=True)
    offset = 0
    index = 0
    header = ""
    scope = ""
    spans = []
    tables = {"": 0}
    while index < len(lines):
        line = lines[index]
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            offset += len(line)
            index += 1
            continue
        if stripped.startswith("["):
            header = line
            marker = next(iter(_flatten(tomllib.loads(header + "\n__classclaw_marker__ = true"))))
            scope = marker.removesuffix(".__classclaw_marker__")
            tables[scope] = offset + len(line)
            offset += len(line)
            index += 1
            continue
        start = offset
        chunk = line
        while True:
            try:
                values = _flatten(tomllib.loads(header + "\n" + chunk))
                break
            except tomllib.TOMLDecodeError:
                index += 1
                chunk += lines[index]
        # A quoted key may itself contain '='. Let TOML identify the assignment boundary.
        for separator in re.finditer("=", chunk):
            try:
                root = next(iter(_flatten(tomllib.loads(header + "\n" + chunk[:separator.start()] + "= 0"))))
                break
            except tomllib.TOMLDecodeError:
                continue
        offset += len(chunk)
        spans.append({"start": start, "end": offset, "scope": scope, "root": root, "values": values})
        index += 1
    return {"spans": spans, "tables": tables}


def _values(current: Settings, raw: dict) -> dict:
    # Existing relative paths must stay relative in the editor. Missing paths use resolved defaults.
    return {**{item["config_path"]: item["value"] for item in catalog(current)["items"]}, **_flatten(raw)}


def _locked(value: Settings) -> list[str]:
    return [key for key, source in value.config_sources if source.startswith("environment:")]


def _serialize(raw: dict) -> str:
    # Supported documents have scalar leaves; quoted dotted keys preserve nested structure.
    return "".join(f'{".".join(json.dumps(part, ensure_ascii=False) for part in path.split("."))} = '
                   f'{json.dumps(value, ensure_ascii=False, allow_nan=False)}\n' for path, value in _flatten(raw).items())


def _prepare(path: Path, current: Settings, old_text: str | None, old: dict, body: ConfigDocumentCheck) -> dict:
    explicit = body.changes or {}
    for key in explicit:
        if any(_secret.search(part) for part in key.split(".")):
            raise AppError("CONFIG_SECRET_FORBIDDEN", "密钥只能通过服务器环境变量配置", 403)
        if key not in FIELDS:
            raise AppError("VALIDATION_ERROR", "存在不支持的配置键", 422,
                           {"errors": [{"config_path": key, "message": "不支持的配置键"}]})
    if locked := sorted(set(_locked(current)) & set(explicit)):
        raise AppError("CONFIG_ENV_OVERRIDDEN", "配置被环境变量覆盖，请在服务器移除覆盖后重试", 409,
                       {"errors": [{"config_path": key, "message": "被环境变量覆盖，不可修改"} for key in locked]})
    if body.toml_text is not None:
        text = body.toml_text
    else:
        raw = _parse(old_text or "")
        for key, value in explicit.items():
            group = raw
            parts = key.split(".")
            for part in parts[:-1]:
                group = group.setdefault(part, {})
            group[parts[-1]] = value
        # Validate types before serialization; JSON null/arrays are not supported TOML settings.
        from app.config import ConfigDocument
        try:
            ConfigDocument.model_validate(raw)
        except ValidationError as exc:
            raise AppError("VALIDATION_ERROR", "配置校验失败", 422, {"errors": [
                {"config_path": ".".join(map(str, item["loc"])), "message": item["msg"]}
                for item in exc.errors(include_input=False, include_url=False)
            ]}) from exc
        text = _serialize(raw)
    raw = _parse(text)
    previous, following = _flatten(old), _flatten(raw)
    changed = sorted(key for key in previous.keys() | following.keys() if previous.get(key) != following.get(key))
    locked = sorted(set(_locked(current)) & (set(changed) | set(explicit)))
    if locked:
        raise AppError("CONFIG_ENV_OVERRIDDEN", "配置被环境变量覆盖，请在服务器移除覆盖后重试", 409,
                       {"errors": [{"config_path": key, "message": "被环境变量覆盖，不可修改"} for key in locked]})
    blank = [key for key, value in following.items() if isinstance(value, str) and not value.strip()
             and (key.startswith("storage.") or key == "runtime.log_file")]
    if blank:
        raise AppError("VALIDATION_ERROR", "路径不能为空", 422,
                       {"errors": [{"config_path": key, "message": "路径不能为空"} for key in blank]})
    try:
        candidate = _load(path, text)
    except AppError as exc:
        layout = text_layout(text)
        for issue in exc.details.get("errors", []):
            key = issue.get("config_path", "")
            span = next((item for item in layout["spans"] if key == item["root"]
                         or key.startswith(f'{item["root"]}.') or item["root"].startswith(f"{key}.")), None)
            issue["line"] = text[:span["start"]].count("\n") + 1 if span else 1
        raise
    old_values = {item["config_path"]: item["value"] for item in catalog(current)["items"]}
    new_values = {item["config_path"]: item["value"] for item in catalog(candidate)["items"]}
    old_sources, new_sources = dict(current.config_sources), dict(candidate.config_sources)
    # Creating a file can change relative path interpretation. Include every effective change in preview/audit.
    paths = sorted(set(changed) | {key for key in old_values if old_values[key] != new_values[key]})
    return {
        "toml_text": text, "config_hash": candidate.config_hash, "document_hash": _digest(text),
        "values": _values(candidate, raw), "layout": text_layout(text), "applied": paths,
        "diff": [{"path": key, "old": old_values.get(key), "new": new_values.get(key),
                  "old_source": old_sources.get(key), "new_source": new_sources.get(key)} for key in paths],
    }


async def read_document() -> dict:
    async with _write_lock:
        path = _path()
        text, value, raw = _read(path)
        return {"toml_text": text, "config_file": str(path), "config_hash": value.config_hash,
                "active_config_hash": settings.config_hash, "document_hash": _digest(text),
                "env_locked_paths": _locked(value), "catalog": catalog(value),
                "values": _values(value, raw), "layout": text_layout(text or "")}


async def check_document(body: ConfigDocumentCheck) -> dict:
    async with _write_lock:
        path = _path()
        text, current, raw = _read(path)
        return _prepare(path, current, text, raw, body)


def _check_base(body: ConfigDocumentUpdate | ConfigDocumentRollback, current: Settings, text: str | None) -> None:
    if body.base_hash != current.config_hash or (body.base_document_hash and body.base_document_hash != _digest(text)):
        raise AppError("CONFIG_CONFLICT", "配置已被其他操作修改，请刷新后重试", 409)


def _atomic_write(path: Path, text: str, mode: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}-", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as handle:
            os.fchmod(handle.fileno(), mode)
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        # Recheck immediately before replace; do not follow a replaced target symlink.
        if _path() != path:
            raise OSError("Configuration target changed")
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def _persist(db: Session, principal: Principal, path: Path, old_text: str | None, current: Settings,
             prepared: dict, *, action: str) -> dict:
    mode = stat.S_IMODE(path.stat().st_mode) if path.exists() else 0o600
    backup = path.with_name(f"{path.name}.bak-{now().strftime('%Y%m%d-%H%M%S-%f')}")
    written = False
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with backup.open("x", encoding="utf-8") as handle:
            os.fchmod(handle.fileno(), mode)
            handle.write(old_text or "")
            handle.flush()
            os.fsync(handle.fileno())
        _atomic_write(path, prepared["toml_text"], mode)
        written = True
        verified = load_settings(path, environ=dict(os.environ))
        if verified.config_hash != prepared["config_hash"]:
            raise ConfigurationError("Configuration changed during save")
        audit(db, action, "configuration", "classclaw", after={
            "paths": prepared["applied"], "old_config_hash": current.config_hash, "new_config_hash": verified.config_hash,
        }, operator_id=principal.user_id, operator_type="service" if principal.is_service else "user")
        db.commit()
    except Exception as exc:
        db.rollback()
        if written:
            try:
                if old_text is None:
                    path.unlink()
                else:
                    _atomic_write(path, old_text, mode)
            except Exception as restore_error:
                raise AppError("CONFIG_WRITE_FAILED", "配置保存失败且自动恢复失败，请从同目录备份恢复", 500) from restore_error
        raise AppError("CONFIG_WRITE_FAILED", "配置保存失败，已保留原配置", 500) from exc
    return {"new_config_hash": verified.config_hash, "document_hash": prepared["document_hash"],
            "restart_required": True, "applied": prepared["applied"], "diff": prepared["diff"]}


async def update_document(db: Session, principal: Principal, body: ConfigDocumentUpdate) -> dict:
    async with _write_lock:
        path = _path()
        text, current, raw = _read(path)
        _check_base(body, current, text)
        prepared = _prepare(path, current, text, raw, body)
        return _persist(db, principal, path, text, current, prepared, action="config_update")


async def rollback_document(db: Session, principal: Principal, body: ConfigDocumentRollback) -> dict:
    async with _write_lock:
        path = _path()
        text, current, raw = _read(path)
        _check_base(body, current, text)
        backups = sorted(path.parent.glob(f"{path.name}.bak-*"), reverse=True)
        if not backups:
            raise AppError("CONFIG_WRITE_FAILED", "没有可恢复的配置备份", 404)
        backup = backups[0]
        if backup.is_symlink() or not backup.is_file() or backup.resolve().parent != path.parent:
            raise AppError("CONFIG_WRITE_FAILED", "配置备份路径不安全", 403)
        try:
            backup_text = backup.read_text(encoding="utf-8")
        except (OSError, UnicodeError) as exc:
            raise AppError("CONFIG_WRITE_FAILED", "无法读取配置备份，未修改当前文件", 500) from exc
        prepared = _prepare(path, current, text, raw, ConfigDocumentCheck(toml_text=backup_text))
        return _persist(db, principal, path, text, current, prepared, action="config_rollback")
