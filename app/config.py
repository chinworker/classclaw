from __future__ import annotations

import hashlib
import json
import os
import tomllib
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Literal
from urllib.parse import urlparse
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from dotenv import load_dotenv
from pydantic import BaseModel, ConfigDict, Field, ValidationError

BASE_DIR = Path(__file__).resolve().parent.parent
DEFAULT_CONFIG_FILE = BASE_DIR / "config" / "classclaw.toml"
load_dotenv(BASE_DIR / ".env", override=False)
ThinkingLevel = Literal["off", "minimal", "low", "medium", "high", "xhigh", "adaptive", "max"]


class ConfigurationError(RuntimeError):
    """Raised before application startup when static configuration is invalid."""


class _StrictSection(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True, str_strip_whitespace=True)


class StorageConfig(_StrictSection):
    database_url: str = "sqlite:///./data/classclaw.db"
    usage_database_url: str = "sqlite:///./data/usage.db"
    attachment_dir: str = "./data/attachments"
    max_attachment_bytes: int = Field(default=20 * 1024 * 1024, ge=1, le=1024 * 1024 * 1024)
    class_workspace_root: str = "./data/openclaw-agents"
    openclaw_state_dir: str = "~/.openclaw"


class RuntimeConfig(_StrictSection):
    timezone: str = "Asia/Shanghai"
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = "INFO"
    log_file: str = "./data/logs/classclaw.log"
    auth_session_hours: int = Field(default=24, ge=1, le=8760)
    analysis_cache_max_entries: int = Field(default=500, ge=10, le=100_000)
    # 出站 HTTP 默认不信任 *_PROXY；曾在本机 macOS 出现系统代理误吞 127.0.0.1 请求。
    http_trust_env: bool = False


class ServerConfig(_StrictSection):
    host: str = Field(default="0.0.0.0", min_length=1, max_length=255)
    port: int = Field(default=8000, ge=1, le=65535)
    access_log: bool = False


class BootstrapConfig(_StrictSection):
    default_admin_username: str = Field(default="admin", min_length=1, max_length=100)


class OpenClawConfig(_StrictSection):
    gateway_url: str = "http://127.0.0.1:18789"
    agent_id: str = Field(default="main", min_length=1, max_length=100)
    extractor_agent_id: str = Field(default="classclaw-extractor", min_length=1, max_length=100)
    extractor_enabled: bool = True
    class_agent_thinking: ThinkingLevel = "off"
    timeout_seconds: float = Field(default=120, gt=0, le=120)
    session_cleanup_hours: float = Field(default=24, ge=0, le=8760)
    cli: str = Field(default="openclaw", min_length=1, max_length=500)


class FeatureConfig(_StrictSection):
    file_analysis: bool = True
    event_ai: bool = True
    wechat_binding: bool = True
    reminders: bool = True


class WebBrandConfig(_StrictSection):
    name: str = Field(default="ClassClaw", min_length=1, max_length=100)
    title: str = Field(default="ClassClaw · 班主任工作台", min_length=1, max_length=200)
    mark: str = Field(default="C", min_length=1, max_length=8)
    subtitle: str = Field(default="班主任工作台", min_length=1, max_length=100)
    admin_name: str = Field(default="ClassClaw Control", min_length=1, max_length=100)
    admin_subtitle: str = Field(default="ADMIN CONSOLE", min_length=1, max_length=100)
    login_description: str = Field(default="班级管理、座位课表、值日作业考勤与班级专属智能体。", max_length=300)


class WebConfig(_StrictSection):
    brand: WebBrandConfig = Field(default_factory=WebBrandConfig)
    ai_request_timeout_seconds: int = Field(default=150, ge=1, le=600)
    usage_window_days: int = Field(default=30, ge=7, le=365)
    database_page_size: int = Field(default=50, ge=10, le=200)


class WechatConfig(_StrictSection):
    channel: str = Field(default="openclaw-weixin", min_length=1, max_length=100)
    qr_binding_timeout_seconds: int = Field(default=300, ge=30, le=1800)
    qr_initial_poll_ms: int = Field(default=1500, ge=250, le=60_000)
    qr_poll_ms: int = Field(default=2000, ge=250, le=60_000)
    qr_retry_ms: int = Field(default=3500, ge=250, le=60_000)
    gateway_start_timeout_seconds: int = Field(default=30, ge=1, le=120)
    gateway_wait_timeout_seconds: int = Field(default=15, ge=1, le=120)


class ConfigDocument(_StrictSection):
    storage: StorageConfig = Field(default_factory=StorageConfig)
    runtime: RuntimeConfig = Field(default_factory=RuntimeConfig)
    server: ServerConfig = Field(default_factory=ServerConfig)
    bootstrap: BootstrapConfig = Field(default_factory=BootstrapConfig)
    openclaw: OpenClawConfig = Field(default_factory=OpenClawConfig)
    features: FeatureConfig = Field(default_factory=FeatureConfig)
    web: WebConfig = Field(default_factory=WebConfig)
    wechat: WechatConfig = Field(default_factory=WechatConfig)


@dataclass(frozen=True, slots=True)
class Settings:
    # Compatibility fields used throughout the existing service layer.
    app_name: str
    api_prefix: str
    database_url: str
    usage_database_url: str
    attachment_dir: Path
    max_attachment_bytes: int
    timezone: str
    log_level: str
    log_file: Path
    http_trust_env: bool
    api_token: str | None
    auth_session_hours: int
    analysis_cache_max_entries: int
    default_admin_username: str
    default_admin_password: str
    openclaw_gateway_url: str
    openclaw_gateway_token: str | None
    openclaw_agent_id: str
    openclaw_extractor_agent_id: str
    openclaw_extractor_enabled: bool
    openclaw_class_agent_thinking: ThinkingLevel
    openclaw_timeout_seconds: float
    openclaw_class_workspace_root: Path
    openclaw_state_dir: Path
    openclaw_wechat_channel: str
    openclaw_bin: str
    openclaw_session_cleanup_hours: float
    # New static configuration groups exposed only through safe projections.
    features: FeatureConfig
    web: WebConfig
    wechat: WechatConfig
    server: ServerConfig
    config_file: Path | None
    config_hash: str
    # Startup provenance only: never retain environment values or credentials here.
    config_sources: tuple[tuple[str, str], ...] = ()


_ENV_PATHS = {
    "CLASSCLAW_ATTACHMENT_DIR": ("storage", "attachment_dir"),
    "CLASSCLAW_OPENCLAW_CLASS_WORKSPACE_ROOT": ("storage", "class_workspace_root"),
    "CLASSCLAW_OPENCLAW_STATE_DIR": ("storage", "openclaw_state_dir"),
    "CLASSCLAW_LOG_FILE": ("runtime", "log_file"),
}

_ENV_STRINGS = {
    "CLASSCLAW_DATABASE_URL": ("storage", "database_url"),
    "CLASSCLAW_USAGE_DATABASE_URL": ("storage", "usage_database_url"),
    "CLASSCLAW_TIMEZONE": ("runtime", "timezone"),
    "CLASSCLAW_LOG_LEVEL": ("runtime", "log_level"),
    "CLASSCLAW_SERVER_HOST": ("server", "host"),
    "CLASSCLAW_DEFAULT_ADMIN_USERNAME": ("bootstrap", "default_admin_username"),
    "CLASSCLAW_OPENCLAW_GATEWAY_URL": ("openclaw", "gateway_url"),
    "CLASSCLAW_OPENCLAW_AGENT_ID": ("openclaw", "agent_id"),
    "CLASSCLAW_OPENCLAW_EXTRACTOR_AGENT_ID": ("openclaw", "extractor_agent_id"),
    "CLASSCLAW_OPENCLAW_CLASS_AGENT_THINKING": ("openclaw", "class_agent_thinking"),
    "CLASSCLAW_OPENCLAW_BIN": ("openclaw", "cli"),
    "CLASSCLAW_OPENCLAW_WECHAT_CHANNEL": ("wechat", "channel"),
    "CLASSCLAW_WEB_NAME": ("web", "brand", "name"),
    "CLASSCLAW_WEB_TITLE": ("web", "brand", "title"),
    "CLASSCLAW_WEB_MARK": ("web", "brand", "mark"),
    "CLASSCLAW_WEB_SUBTITLE": ("web", "brand", "subtitle"),
    "CLASSCLAW_WEB_ADMIN_NAME": ("web", "brand", "admin_name"),
    "CLASSCLAW_WEB_ADMIN_SUBTITLE": ("web", "brand", "admin_subtitle"),
    "CLASSCLAW_WEB_LOGIN_DESCRIPTION": ("web", "brand", "login_description"),
}

_ENV_INTS = {
    "CLASSCLAW_MAX_ATTACHMENT_BYTES": ("storage", "max_attachment_bytes"),
    "CLASSCLAW_AUTH_SESSION_HOURS": ("runtime", "auth_session_hours"),
    "CLASSCLAW_ANALYSIS_CACHE_MAX_ENTRIES": ("runtime", "analysis_cache_max_entries"),
    "CLASSCLAW_SERVER_PORT": ("server", "port"),
    "CLASSCLAW_WEB_AI_REQUEST_TIMEOUT_SECONDS": ("web", "ai_request_timeout_seconds"),
    "CLASSCLAW_WEB_USAGE_WINDOW_DAYS": ("web", "usage_window_days"),
    "CLASSCLAW_WEB_DATABASE_PAGE_SIZE": ("web", "database_page_size"),
    "CLASSCLAW_WECHAT_QR_BINDING_TIMEOUT_SECONDS": ("wechat", "qr_binding_timeout_seconds"),
    "CLASSCLAW_WECHAT_QR_INITIAL_POLL_MS": ("wechat", "qr_initial_poll_ms"),
    "CLASSCLAW_WECHAT_QR_POLL_MS": ("wechat", "qr_poll_ms"),
    "CLASSCLAW_WECHAT_QR_RETRY_MS": ("wechat", "qr_retry_ms"),
    "CLASSCLAW_WECHAT_GATEWAY_START_TIMEOUT_SECONDS": ("wechat", "gateway_start_timeout_seconds"),
    "CLASSCLAW_WECHAT_GATEWAY_WAIT_TIMEOUT_SECONDS": ("wechat", "gateway_wait_timeout_seconds"),
}

_ENV_FLOATS = {
    "CLASSCLAW_OPENCLAW_TIMEOUT_SECONDS": ("openclaw", "timeout_seconds"),
    "CLASSCLAW_OPENCLAW_SESSION_CLEANUP_HOURS": ("openclaw", "session_cleanup_hours"),
}

_ENV_BOOLEANS = {
    "CLASSCLAW_OPENCLAW_EXTRACTOR_ENABLED": ("openclaw", "extractor_enabled"),
    "CLASSCLAW_SERVER_ACCESS_LOG": ("server", "access_log"),
    "CLASSCLAW_TRUST_ENV": ("runtime", "http_trust_env"),
    "CLASSCLAW_FEATURE_FILE_ANALYSIS": ("features", "file_analysis"),
    "CLASSCLAW_FEATURE_EVENT_AI": ("features", "event_ai"),
    "CLASSCLAW_FEATURE_WECHAT_BINDING": ("features", "wechat_binding"),
    "CLASSCLAW_FEATURE_REMINDERS": ("features", "reminders"),
}

CONFIG_ENVIRONMENT_VARIABLES = {
    ".".join(path): name
    for name, path in {**_ENV_PATHS, **_ENV_STRINGS, **_ENV_INTS, **_ENV_FLOATS, **_ENV_BOOLEANS}.items()
}


def _configuration_sources(raw: dict, overridden: set[tuple[str, ...]], env: Mapping[str, str]) -> tuple[tuple[str, str], ...]:
    result = []
    for path, env_name in CONFIG_ENVIRONMENT_VARIABLES.items():
        parts = tuple(path.split("."))
        if parts in overridden:
            if path == "storage.openclaw_state_dir" and not env.get(env_name, "").strip():
                env_name = "OPENCLAW_STATE_DIR"
            source = f"environment:{env_name}"
        else:
            value = raw
            for part in parts:
                value = value.get(part) if isinstance(value, dict) else None
            source = "file" if value is not None else "default"
        result.append((path, source))
    return tuple(result)


def _set_nested(target: dict, path: tuple[str, ...], value: object) -> None:
    current = target
    for key in path[:-1]:
        current = current.setdefault(key, {})
    current[path[-1]] = value


def _parse_bool(name: str, value: str) -> bool:
    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise ConfigurationError(f"{name} 必须是 true/false、yes/no、on/off 或 1/0")


def _apply_environment(data: dict, environ: Mapping[str, str]) -> set[tuple[str, ...]]:
    overridden: set[tuple[str, ...]] = set()
    for name, path in {**_ENV_STRINGS, **_ENV_PATHS}.items():
        value = environ.get(name)
        if value is not None and value.strip():
            normalized = value.strip().upper() if name == "CLASSCLAW_LOG_LEVEL" else value.strip()
            _set_nested(data, path, normalized)
            overridden.add(path)
    for name, path in _ENV_INTS.items():
        value = environ.get(name)
        if value is not None and value.strip():
            try:
                parsed = int(value)
            except ValueError as exc:
                raise ConfigurationError(f"{name} 必须是整数") from exc
            _set_nested(data, path, parsed)
            overridden.add(path)
    for name, path in _ENV_FLOATS.items():
        value = environ.get(name)
        if value is not None and value.strip():
            try:
                parsed = float(value)
            except ValueError as exc:
                raise ConfigurationError(f"{name} 必须是数字") from exc
            _set_nested(data, path, parsed)
            overridden.add(path)
    for name, path in _ENV_BOOLEANS.items():
        value = environ.get(name)
        if value is not None and value.strip():
            _set_nested(data, path, _parse_bool(name, value))
            overridden.add(path)
    # OPENCLAW_STATE_DIR remains a compatibility fallback only when the ClassClaw-specific variable is absent.
    if ("storage", "openclaw_state_dir") not in overridden:
        value = environ.get("OPENCLAW_STATE_DIR")
        if value is not None and value.strip():
            _set_nested(data, ("storage", "openclaw_state_dir"), value.strip())
            overridden.add(("storage", "openclaw_state_dir"))
    return overridden


def _resolve_path(value: str, base: Path) -> Path:
    path = Path(value).expanduser()
    if not path.is_absolute():
        path = base / path
    return path.resolve()


def _resolve_database_url(value: str, base: Path, field: str = "storage.database_url") -> str:
    prefix = "sqlite:///"
    if not value.startswith(prefix):
        raise ConfigurationError(f"{field} 只支持 sqlite:/// URL")
    raw_path = value.removeprefix(prefix)
    if "?" in raw_path:
        raise ConfigurationError(f"{field} 不支持 URL 查询参数，请使用普通 SQLite 文件路径")
    if raw_path == ":memory:":
        return value
    path = _resolve_path(raw_path, base)
    return f"{prefix}{path}"


def _is_within(path: Path, root: Path) -> bool:
    return path == root or root in path.parents


def _validate_paths(
    *,
    database_url: str,
    usage_database_url: str,
    attachment_dir: Path,
    log_file: Path,
    workspace_root: Path,
    state_dir: Path,
    config_file: Path | None,
) -> None:
    home = Path.home().resolve()
    filesystem_root = Path(attachment_dir.anchor)
    destructive = {
        "storage.attachment_dir": attachment_dir,
        "storage.class_workspace_root": workspace_root,
    }
    for name, path in destructive.items():
        if path in {filesystem_root, home, BASE_DIR.resolve()} or len(path.parts) < 3:
            raise ConfigurationError(f"{name} 指向危险目录：{path}")
    if _is_within(attachment_dir, workspace_root) or _is_within(workspace_root, attachment_dir):
        raise ConfigurationError("storage.attachment_dir 与 storage.class_workspace_root 不能重叠")
    if _is_within(state_dir, workspace_root) or _is_within(workspace_root, state_dir):
        raise ConfigurationError("storage.openclaw_state_dir 与 storage.class_workspace_root 不能重叠")
    if _is_within(state_dir, attachment_dir) or _is_within(attachment_dir, state_dir):
        raise ConfigurationError("storage.openclaw_state_dir 与 storage.attachment_dir 不能重叠")

    database_path = None
    if database_url.startswith("sqlite:///") and not database_url.endswith(":memory:"):
        database_path = Path(database_url.removeprefix("sqlite:///")).resolve()
    protected = {
        "数据库": database_path,
        "用量数据库": None if usage_database_url.endswith(":memory:") else Path(usage_database_url.removeprefix("sqlite:///")),
        "日志文件": log_file,
        "配置文件": config_file.resolve() if config_file else None,
    }
    usage_path = protected["用量数据库"]
    if database_path and usage_path and (
        usage_path == database_path or (usage_path.exists() and database_path.exists() and usage_path.samefile(database_path))
    ):
        raise ConfigurationError("storage.usage_database_url 必须与业务数据库使用不同文件")
    if usage_path and usage_path in {log_file, config_file}:
        raise ConfigurationError("storage.usage_database_url 不能覆盖日志或配置文件")
    for root_name, root in destructive.items():
        for target_name, target in protected.items():
            if target is not None and _is_within(target, root):
                raise ConfigurationError(f"{root_name} 不能包含{target_name}：{target}")


def _validate_document(
    document: ConfigDocument,
    *,
    database_url: str,
    usage_database_url: str,
    attachment_dir: Path,
    log_file: Path,
    workspace_root: Path,
    state_dir: Path,
    config_file: Path | None,
) -> None:
    try:
        ZoneInfo(document.runtime.timezone)
    except ZoneInfoNotFoundError as exc:
        raise ConfigurationError(f"runtime.timezone 不是有效时区：{document.runtime.timezone}") from exc
    parsed = urlparse(document.openclaw.gateway_url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ConfigurationError("openclaw.gateway_url 必须是完整的 http:// 或 https:// URL")
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ConfigurationError("openclaw.gateway_url 不能包含账号、密码、查询参数或片段")
    if document.web.ai_request_timeout_seconds < document.openclaw.timeout_seconds:
        raise ConfigurationError("web.ai_request_timeout_seconds 不能小于 openclaw.timeout_seconds")
    qr_ms = document.wechat.qr_binding_timeout_seconds * 1000
    for name, value in {
        "wechat.qr_initial_poll_ms": document.wechat.qr_initial_poll_ms,
        "wechat.qr_poll_ms": document.wechat.qr_poll_ms,
        "wechat.qr_retry_ms": document.wechat.qr_retry_ms,
    }.items():
        if value >= qr_ms:
            raise ConfigurationError(f"{name} 必须小于二维码总等待时间")
    if document.wechat.qr_binding_timeout_seconds <= max(
        document.wechat.gateway_start_timeout_seconds,
        document.wechat.gateway_wait_timeout_seconds,
    ):
        raise ConfigurationError("wechat.qr_binding_timeout_seconds 必须大于单次 Gateway 调用超时")
    if (
        max(document.wechat.gateway_start_timeout_seconds, document.wechat.gateway_wait_timeout_seconds)
        > document.openclaw.timeout_seconds
    ):
        raise ConfigurationError("微信 Gateway 调用超时不能大于 openclaw.timeout_seconds")
    _validate_paths(
        database_url=database_url,
        usage_database_url=usage_database_url,
        attachment_dir=attachment_dir,
        log_file=log_file,
        workspace_root=workspace_root,
        state_dir=state_dir,
        config_file=config_file,
    )


def _field_base(
    raw: dict,
    section: str,
    field: str,
    *,
    config_base: Path,
    overridden: set[tuple[str, ...]],
) -> Path:
    path = (section, field)
    if path in overridden:
        return BASE_DIR
    section_data = raw.get(section)
    if isinstance(section_data, dict) and field in section_data:
        return config_base
    return BASE_DIR


def load_settings(
    config_path: str | Path | None = None,
    *,
    environ: Mapping[str, str] | None = None,
    toml_text: str | None = None,
) -> Settings:
    env = os.environ if environ is None else environ
    explicit_path = config_path or env.get("CLASSCLAW_CONFIG_FILE")
    selected = Path(explicit_path).expanduser() if explicit_path else DEFAULT_CONFIG_FILE
    required = bool(explicit_path)
    if not selected.is_absolute():
        selected = (BASE_DIR / selected).resolve()
    else:
        selected = selected.resolve()
    raw: dict = {}
    config_file: Path | None = None
    if toml_text is not None:
        # The admin dry run uses the real file's base directory without touching disk or os.environ.
        raw = tomllib.loads(toml_text)
        config_file = selected
    elif selected.exists():
        if not selected.is_file():
            raise ConfigurationError(f"配置路径不是普通文件：{selected}")
        try:
            raw = tomllib.loads(selected.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, tomllib.TOMLDecodeError) as exc:
            raise ConfigurationError(f"无法读取配置文件 {selected}：{exc}") from exc
        config_file = selected
    elif required:
        raise ConfigurationError(f"指定的配置文件不存在：{selected}")

    try:
        base_document = ConfigDocument.model_validate(raw)
        merged = base_document.model_dump()
        overridden = _apply_environment(merged, env)
        document = ConfigDocument.model_validate(merged)
    except ValidationError as exc:
        details = "; ".join(f"{'.'.join(str(part) for part in error['loc'])}: {error['msg']}" for error in exc.errors())
        raise ConfigurationError(f"配置校验失败：{details}") from exc

    config_base = config_file.parent if config_file else BASE_DIR
    database_url = _resolve_database_url(
        document.storage.database_url,
        _field_base(raw, "storage", "database_url", config_base=config_base, overridden=overridden),
    )
    usage_database_url = _resolve_database_url(
        document.storage.usage_database_url,
        _field_base(raw, "storage", "usage_database_url", config_base=config_base, overridden=overridden),
        "storage.usage_database_url",
    )
    attachment_dir = _resolve_path(
        document.storage.attachment_dir,
        _field_base(raw, "storage", "attachment_dir", config_base=config_base, overridden=overridden),
    )
    log_file = _resolve_path(
        document.runtime.log_file,
        _field_base(raw, "runtime", "log_file", config_base=config_base, overridden=overridden),
    )
    workspace_root = _resolve_path(
        document.storage.class_workspace_root,
        _field_base(raw, "storage", "class_workspace_root", config_base=config_base, overridden=overridden),
    )
    state_dir = _resolve_path(
        document.storage.openclaw_state_dir,
        _field_base(raw, "storage", "openclaw_state_dir", config_base=config_base, overridden=overridden),
    )
    _validate_document(
        document,
        database_url=database_url,
        usage_database_url=usage_database_url,
        attachment_dir=attachment_dir,
        log_file=log_file,
        workspace_root=workspace_root,
        state_dir=state_dir,
        config_file=config_file,
    )

    hash_payload = {
        "document": document.model_dump(mode="json"),
        "resolved": {
            "database_url": database_url,
            "usage_database_url": usage_database_url,
            "attachment_dir": str(attachment_dir),
            "log_file": str(log_file),
            "class_workspace_root": str(workspace_root),
            "openclaw_state_dir": str(state_dir),
        },
    }
    config_hash = hashlib.sha256(json.dumps(hash_payload, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    return Settings(
        app_name="ClassClaw 班级管理后端",
        api_prefix="/api/v1",
        database_url=database_url,
        usage_database_url=usage_database_url,
        attachment_dir=attachment_dir,
        max_attachment_bytes=document.storage.max_attachment_bytes,
        timezone=document.runtime.timezone,
        log_level=document.runtime.log_level,
        log_file=log_file,
        http_trust_env=document.runtime.http_trust_env,
        api_token=env.get("CLASSCLAW_API_TOKEN") or None,
        auth_session_hours=document.runtime.auth_session_hours,
        analysis_cache_max_entries=document.runtime.analysis_cache_max_entries,
        default_admin_username=document.bootstrap.default_admin_username,
        default_admin_password=env.get("CLASSCLAW_DEFAULT_ADMIN_PASSWORD") or "32767",
        openclaw_gateway_url=document.openclaw.gateway_url.rstrip("/"),
        openclaw_gateway_token=env.get("CLASSCLAW_OPENCLAW_GATEWAY_TOKEN") or None,
        openclaw_agent_id=document.openclaw.agent_id,
        openclaw_extractor_agent_id=document.openclaw.extractor_agent_id,
        openclaw_extractor_enabled=document.openclaw.extractor_enabled,
        openclaw_class_agent_thinking=document.openclaw.class_agent_thinking,
        openclaw_timeout_seconds=document.openclaw.timeout_seconds,
        openclaw_class_workspace_root=workspace_root,
        openclaw_state_dir=state_dir,
        openclaw_wechat_channel=document.wechat.channel,
        openclaw_bin=document.openclaw.cli,
        openclaw_session_cleanup_hours=document.openclaw.session_cleanup_hours,
        features=document.features,
        web=document.web,
        wechat=document.wechat,
        server=document.server,
        config_file=config_file,
        config_hash=config_hash,
        config_sources=_configuration_sources(raw, overridden, env),
    )


def safe_config_summary(value: Settings) -> dict:
    """Return a serializable summary that never contains credentials."""
    return {
        "config_file": str(value.config_file) if value.config_file else None,
        "config_hash": value.config_hash,
        "features": value.features.model_dump(),
        "web": value.web.model_dump(),
        "wechat": value.wechat.model_dump(),
        "runtime": {
            "timezone": value.timezone,
            "log_level": value.log_level,
            "auth_session_hours": value.auth_session_hours,
            "http_trust_env": value.http_trust_env,
        },
        "server": value.server.model_dump(),
        "bootstrap": {"default_admin_username": value.default_admin_username},
        "storage": {
            "database_url": value.database_url,
            "usage_database_url": value.usage_database_url,
            "attachment_dir": str(value.attachment_dir),
            "max_attachment_bytes": value.max_attachment_bytes,
            "class_workspace_root": str(value.openclaw_class_workspace_root),
            "openclaw_state_dir": str(value.openclaw_state_dir),
            "log_file": str(value.log_file),
        },
        "openclaw": {
            "gateway_url": value.openclaw_gateway_url,
            "agent_id": value.openclaw_agent_id,
            "extractor_agent_id": value.openclaw_extractor_agent_id,
            "extractor_enabled": value.openclaw_extractor_enabled,
            "class_agent_thinking": value.openclaw_class_agent_thinking,
            "timeout_seconds": value.openclaw_timeout_seconds,
            "session_cleanup_hours": value.openclaw_session_cleanup_hours,
            "cli": value.openclaw_bin,
            "api_token_configured": bool(value.api_token),
            "gateway_token_configured": bool(value.openclaw_gateway_token),
        },
    }


settings = load_settings()
