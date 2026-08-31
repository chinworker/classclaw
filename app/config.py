from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv


BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env", override=False)


def _env_flag(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None or not value.strip():
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True, slots=True)
class Settings:
    app_name: str = "ClassClaw 班级管理后端"
    api_prefix: str = "/api/v1"
    database_url: str = os.getenv(
        "CLASSCLAW_DATABASE_URL", f"sqlite:///{BASE_DIR / 'data' / 'classclaw.db'}"
    )
    attachment_dir: Path = Path(
        os.getenv("CLASSCLAW_ATTACHMENT_DIR", str(BASE_DIR / "data" / "attachments"))
    )
    max_attachment_bytes: int = int(os.getenv("CLASSCLAW_MAX_ATTACHMENT_BYTES", "20971520"))
    timezone: str = os.getenv("CLASSCLAW_TIMEZONE", "Asia/Shanghai")
    log_level: str = os.getenv("CLASSCLAW_LOG_LEVEL", "INFO")
    log_file: Path = Path(os.getenv("CLASSCLAW_LOG_FILE", str(BASE_DIR / "data" / "logs" / "classclaw.log")))
    api_token: str | None = os.getenv("CLASSCLAW_API_TOKEN") or None
    auth_session_hours: int = int(os.getenv("CLASSCLAW_AUTH_SESSION_HOURS", "24"))
    default_admin_username: str = os.getenv("CLASSCLAW_DEFAULT_ADMIN_USERNAME", "admin")
    default_admin_password: str = os.getenv("CLASSCLAW_DEFAULT_ADMIN_PASSWORD", "32767")
    openclaw_gateway_url: str = os.getenv("CLASSCLAW_OPENCLAW_GATEWAY_URL", "http://127.0.0.1:18789").rstrip("/")
    openclaw_gateway_token: str | None = os.getenv("CLASSCLAW_OPENCLAW_GATEWAY_TOKEN") or None
    openclaw_agent_id: str = os.getenv("CLASSCLAW_OPENCLAW_AGENT_ID", "main")
    openclaw_extractor_agent_id: str = os.getenv("CLASSCLAW_OPENCLAW_EXTRACTOR_AGENT_ID", "classclaw-extractor")
    openclaw_extractor_enabled: bool = _env_flag("CLASSCLAW_OPENCLAW_EXTRACTOR_ENABLED", True)
    openclaw_timeout_seconds: float = float(os.getenv("CLASSCLAW_OPENCLAW_TIMEOUT_SECONDS", "120"))
    openclaw_class_workspace_root: Path = Path(
        os.getenv("CLASSCLAW_OPENCLAW_CLASS_WORKSPACE_ROOT", str(BASE_DIR / "data" / "openclaw-agents"))
    )
    openclaw_state_dir: Path = Path(
        os.getenv("CLASSCLAW_OPENCLAW_STATE_DIR", os.getenv("OPENCLAW_STATE_DIR", str(Path.home() / ".openclaw")))
    )
    openclaw_wechat_channel: str = os.getenv("CLASSCLAW_OPENCLAW_WECHAT_CHANNEL", "openclaw-weixin")


settings = Settings()
