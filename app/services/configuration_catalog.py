"""Admin-only, explicit projection of supported startup configuration; no secret values."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from app.config import CONFIG_ENVIRONMENT_VARIABLES, DEFAULT_CONFIG_FILE, Settings

# section: (category, label, description). The categories are navigation scopes, not TOML tables.
SECTIONS = {
    "server": ("classclaw", "服务监听", "由 python run.py 使用；直接运行 Uvicorn 时需保持命令参数一致。单进程、单 worker。"),
    "storage": ("classclaw", "数据库与附件", "路径修改不会迁移现有数据；迁移前停止服务并备份数据库与附件。"),
    "runtime": ("classclaw", "运行与日志", "业务时区、日志、登录有效期和确定性分析缓存。"),
    "bootstrap": ("classclaw", "账号初始化", "只影响首次创建或完整初始化后的管理员，现有账号通过用户管理维护。"),
    "features": ("classclaw", "功能开关", "控制当前已有的 AI 解析、微信绑定与主动提醒入口，后端同步校验。"),
    "web.brand": ("classclaw", "网页品牌与文案", "网页标题、品牌标记、管理端名称与登录说明。"),
    "web": ("classclaw", "网页交互默认值", "AI 等待时间、管理端统计窗口与数据库分页。"),
    "openclaw": ("openclaw", "Gateway 连接与提取服务", "这是 ClassClaw 连接 OpenClaw 的启动配置；Gateway 自身运行参数另在网关配置页维护。"),
    "workspaces": ("openclaw", "智能体工作区与状态目录", "应与实际 Gateway 状态目录一致；修改路径不会自动搬迁已有班级 Agent。"),
    "wechat": ("openclaw", "微信接入与二维码", "班级 Agent 微信账号绑定的通道、等待与轮询参数。"),
}

# config_path: (section, Settings attribute path, label, description)
FIELDS = {
    "server.host": ("server", "server.host", "监听地址", "同机反向代理通常使用 127.0.0.1。"),
    "server.port": ("server", "server.port", "监听端口", "HTTP 端口，1–65535。"),
    "server.access_log": ("server", "server.access_log", "访问日志", "是否开启 Uvicorn 访问日志。"),
    "storage.database_url": ("storage", "database_url", "SQLite 数据库", "仅支持 sqlite:///；显示当前解析后的实际路径。"),
    "storage.usage_database_url": ("storage", "usage_database_url", "AI 用量数据库", "独立 SQLite 文件与写锁，保存请求数和 Token 用量；修改路径前先迁移该库。"),
    "storage.attachment_dir": ("storage", "attachment_dir", "附件目录", "本地附件保存位置；修改后需要迁移原文件。"),
    "storage.max_attachment_bytes": ("storage", "max_attachment_bytes", "单附件上限（字节）", "网页上传及后端校验共用，范围 1 字节至 1 GiB。"),
    "runtime.timezone": ("runtime", "timezone", "业务时区", "有效 IANA 时区，日期计算和网页时间显示共用。"),
    "runtime.log_level": ("runtime", "log_level", "日志级别", "DEBUG / INFO / WARNING / ERROR / CRITICAL。"),
    "runtime.log_file": ("runtime", "log_file", "日志文件", "后端轮转 JSON 日志；显示解析后的路径。"),
    "runtime.auth_session_hours": ("runtime", "auth_session_hours", "登录有效期（小时）", "新建网页登录会话使用的有效期；不改变已有会话到期时间。"),
    "runtime.analysis_cache_max_entries": ("runtime", "analysis_cache_max_entries", "分析缓存条数上限", "确定性统计缓存的最大条数，超过后清理最旧记录。"),
    "bootstrap.default_admin_username": ("bootstrap", "default_admin_username", "初始管理员用户名", "只影响首次创建或完整初始化后的管理员。"),
    "features.file_analysis": ("features", "features.file_analysis", "文件智能解析", "名单、课表、座位等文件的 AI 解析入口。"),
    "features.event_ai": ("features", "features.event_ai", "学生事件智能分类", "自动判断学生事件子类、倾向与程度。"),
    "features.wechat_binding": ("features", "features.wechat_binding", "微信绑定", "允许生成班级 Agent 微信登录二维码并绑定。"),
    "features.reminders": ("features", "features.reminders", "主动提醒", "控制提醒查询与发送，关闭后已有提醒保留。"),
    "web.brand.name": ("web.brand", "web.brand.name", "品牌名称", "普通网页端显示名称。"),
    "web.brand.title": ("web.brand", "web.brand.title", "浏览器标题", "浏览器标签页的标题。"),
    "web.brand.mark": ("web.brand", "web.brand.mark", "品牌字符标记", "侧栏与登录页的短字符标记。"),
    "web.brand.subtitle": ("web.brand", "web.brand.subtitle", "工作台副标题", "班主任工作台的品牌副标题。"),
    "web.brand.admin_name": ("web.brand", "web.brand.admin_name", "管理端名称", "管理员侧栏品牌名称。"),
    "web.brand.admin_subtitle": ("web.brand", "web.brand.admin_subtitle", "管理端副标题", "管理员侧栏品牌副标题。"),
    "web.brand.login_description": ("web.brand", "web.brand.login_description", "登录页说明", "登录表单上方的说明。"),
    "web.ai_request_timeout_seconds": ("web", "web.ai_request_timeout_seconds", "网页 AI 等待时间（秒）", "不能小于后端 Gateway 请求超时。"),
    "web.usage_window_days": ("web", "web.usage_window_days", "默认统计窗口（天）", "管理员使用量页面首次加载的统计天数。"),
    "web.database_page_size": ("web", "web.database_page_size", "数据库默认分页行数", "数据库查看页默认读取的行数。"),
    "openclaw.gateway_url": ("openclaw", "openclaw_gateway_url", "Gateway 地址", "完整 HTTP/HTTPS 地址，不允许在 URL 中放置凭据。"),
    "openclaw.agent_id": ("openclaw", "openclaw_agent_id", "Main Agent ID", "默认系统智能体；班级 Agent 使用各自独立的 ID。"),
    "openclaw.extractor_agent_id": ("openclaw", "openclaw_extractor_agent_id", "提取 Agent ID", "专用于一次性结构化提取；模型及提示词在系统 Agent 页配置。"),
    "openclaw.extractor_enabled": ("openclaw", "openclaw_extractor_enabled", "独立提取 Agent", "关闭或创建失败时回退 Main Agent。"),
    "openclaw.class_agent_thinking": ("openclaw", "openclaw_class_agent_thinking", "班级 Agent 默认思考强度", "统一用于网页、微信等场景；会话可独立覆盖。off/minimal/low/medium/high/xhigh/adaptive/max，模型须支持所选档位。"),
    "openclaw.timeout_seconds": ("openclaw", "openclaw_timeout_seconds", "Gateway 请求超时（秒）", "大于 0 且不超过 120 秒。"),
    "openclaw.session_cleanup_hours": ("openclaw", "openclaw_session_cleanup_hours", "会话维护间隔（小时）", "按 OpenClaw 维护策略定时调用 CLI；0 关闭自动维护。"),
    "openclaw.cli": ("openclaw", "openclaw_bin", "OpenClaw CLI", "后端执行会话维护时使用的命令或可执行文件路径。"),
    "storage.class_workspace_root": ("workspaces", "openclaw_class_workspace_root", "班级工作区根目录", "存放班级提示词及身份文件；已有班级路径不会随根目录修改自动更新。"),
    "storage.openclaw_state_dir": ("workspaces", "openclaw_state_dir", "OpenClaw 状态目录", "Agent 调用统计等本地读取使用的目录；应与 Gateway 实际目录一致。"),
    "wechat.channel": ("wechat", "openclaw_wechat_channel", "微信通道 ID", "已安装微信兼容层的 channel ID。"),
    "wechat.qr_binding_timeout_seconds": ("wechat", "wechat.qr_binding_timeout_seconds", "扫码总等待时间（秒）", "必须大于单次 Gateway 微信调用超时。"),
    "wechat.qr_initial_poll_ms": ("wechat", "wechat.qr_initial_poll_ms", "首次查询间隔（毫秒）", "必须小于扫码总等待时间。"),
    "wechat.qr_poll_ms": ("wechat", "wechat.qr_poll_ms", "常规查询间隔（毫秒）", "二维码状态的常规轮询节奏。"),
    "wechat.qr_retry_ms": ("wechat", "wechat.qr_retry_ms", "失败重试间隔（毫秒）", "二维码状态查询失败后的重试节奏。"),
    "wechat.gateway_start_timeout_seconds": ("wechat", "wechat.gateway_start_timeout_seconds", "二维码启动超时（秒）", "不能大于 Gateway 请求超时。"),
    "wechat.gateway_wait_timeout_seconds": ("wechat", "wechat.gateway_wait_timeout_seconds", "二维码等待超时（秒）", "不能大于 Gateway 请求超时。"),
}

_LEGACY_KEYS = {
    **{f"features.{name}": f"feature.{name}" for name in ("file_analysis", "event_ai", "wechat_binding", "reminders")},
    "web.usage_window_days": "admin.usage_window_days",
    "web.database_page_size": "admin.database_page_size",
}


def list_settings(settings: Settings) -> list[dict[str, Any]]:
    sources = dict(settings.config_sources)
    rows = []
    for path, (section, attribute, label, description) in FIELDS.items():
        value: Any = settings
        for part in attribute.split("."):
            value = getattr(value, part)
        if isinstance(value, Path):
            value = str(value)
        rows.append({
            "key": _LEGACY_KEYS.get(path, path), "config_path": path,
            "category": SECTIONS[section][0], "section": section,
            "group": "features" if section == "features" else "constants",
            "label": label, "description": description, "value": value,
            "type": "boolean" if isinstance(value, bool) else "integer" if isinstance(value, int) else "number" if isinstance(value, float) else "string",
            "env_var": CONFIG_ENVIRONMENT_VARIABLES[path],
            "source": "startup_config", "value_source": sources.get(path, "unknown"),
            "restart_required": True, "editable": False,
        })
    return rows


def catalog(settings: Settings) -> dict[str, Any]:
    return {
        "config_file": str(settings.config_file) if settings.config_file else None,
        "default_config_file": str(DEFAULT_CONFIG_FILE),
        "config_file_env_var": "CLASSCLAW_CONFIG_FILE",
        "config_hash": settings.config_hash,
        "precedence": "环境变量（含 .env） > TOML 配置文件 > 程序默认值；进程环境变量优先于 .env。",
        "sections": [
            {"id": key, "category": category, "label": label, "description": description}
            for key, (category, label, description) in SECTIONS.items()
        ],
        "items": list_settings(settings),
        "credentials": [
            {"category": category, "label": label, "env_var": env_var, "configured": bool(value), "description": description}
            for category, label, env_var, value, description in (
                ("classclaw", "ClassClaw 服务 Token", "CLASSCLAW_API_TOKEN", settings.api_token,
                 "仅在服务器环境变量或 .env 中修改，并同步 OpenClaw 插件；页面不显示凭据内容。"),
                ("classclaw", "初始管理员密码", "CLASSCLAW_DEFAULT_ADMIN_PASSWORD", settings.default_admin_password != "32767",
                 "状态表示是否替换内置初始密码；用于初始创建，也作为本地 scripts/reset_admin_password.py 的重置目标，不自动覆盖现有密码。"),
                ("openclaw", "Gateway 管理 Token", "CLASSCLAW_OPENCLAW_GATEWAY_TOKEN", settings.openclaw_gateway_token,
                 "仅在服务器环境变量或 .env 中修改，须与 Gateway 配置一致；不会返回 Token 或尾号。"),
            )
        ],
    }
