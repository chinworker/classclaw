"""Gateway cleanup using explicit merge-patch deletions and read-back verification."""
from __future__ import annotations

import asyncio
import json
import re
from pathlib import Path

from app.core.errors import AppError
from app.services import openclaw_provisioning as gateway
from app.services import wechat_login
from app.services.deletion_files import remove_tree, safe_path


def _object(value):
    if not isinstance(value, dict):
        raise AppError("DELETION_CONFIG_INVALID", "Gateway 配置格式异常，无法安全删除", 503)
    return value


def _parts(config):
    """Absent sections are empty; malformed sections must never become empty patches."""
    config = _object(config)
    agents = _object(config.get("agents", {})).get("list", [])
    bindings = config.get("bindings", [])
    if any(not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows) for rows in (agents, bindings)):
        raise AppError("DELETION_CONFIG_INVALID", "Gateway 列表格式异常，无法安全删除", 503)
    mapping = config
    for key in ("plugins", "entries", "classclaw", "config", "agentClasses"):
        mapping = _object(mapping.get(key, {}))
    if any(not isinstance(value, str) for value in mapping.values()):
        raise AppError("DELETION_CONFIG_INVALID", "Gateway 班级映射格式异常", 503)
    for row in bindings:
        _object(row.get("match"))
    return agents, bindings, mapping


def _channel_accounts(config, channel):
    return _object(_object(_object(config.get("channels", {})).get(channel, {})).get("accounts", {}))


def _overlaps(left, right):
    return left == right or left in right.parents or right in left.parents


def account_key(value):
    if not isinstance(value, str) or not value.strip():
        raise AppError("DELETION_CONFIG_INVALID", "微信账号标识无效", 503)
    return re.sub(r"[^a-z0-9_-]", "-", value.strip().lower())


def validate_paths(plan):
    settings = gateway.settings
    if plan["agent_id"] in {"main", settings.openclaw_agent_id, settings.openclaw_extractor_agent_id}:
        raise AppError("DELETION_RESOURCE_SHARED", "系统智能体不能作为班级资源删除", 409)
    safe_path(settings.openclaw_class_workspace_root, Path(plan["workspace"]))
    safe_path(settings.openclaw_state_dir / "agents", settings.openclaw_state_dir / "agents" / plan["agent_id"])


def validate_config(plan, config):
    validate_paths(plan)
    agents, bindings, mapping = _parts(config)
    _channel_accounts(config, plan["channel"])
    agent_id, class_id = plan["agent_id"], plan["class_id"]
    if mapping.get(agent_id) not in (None, class_id):
        raise AppError("DELETION_RESOURCE_SHARED", "智能体已被其他班级使用", 409)
    # Multiple agent claims require repair before a destructive operation.
    if any(key != agent_id and value == class_id for key, value in mapping.items()):
        raise AppError("DELETION_RESOURCE_SHARED", "班级存在多个智能体映射，请先修复归属", 409)
    workspace = Path(plan["workspace"]).resolve()
    for row in agents:
        row_id = row.get("id") or row.get("agentId")
        if row.get("workspace"):
            other = Path(row["workspace"]).expanduser().resolve()
            overlaps = _overlaps(other, workspace)
            if (row_id != agent_id and overlaps) or (row_id == agent_id and other != workspace):
                raise AppError("DELETION_RESOURCE_SHARED", "智能体工作目录归属不一致或与其他智能体重叠", 409)
        if row.get("agentDir"):
            state = (gateway.settings.openclaw_state_dir / "agents" / agent_id).expanduser().resolve()
            other_state = Path(row["agentDir"]).expanduser().resolve()
            if row_id != agent_id and _overlaps(state, other_state):
                raise AppError("DELETION_RESOURCE_SHARED", "智能体状态目录正被其他智能体使用", 409)
    accounts = {account_key(account) for account in plan.get("account_ids", [])}
    for row in bindings:
        match = row.get("match") or {}
        if row.get("agentId") == agent_id:
            if match.get("channel") != plan["channel"] or not match.get("accountId") or "*" in match["accountId"]:
                raise AppError("DELETION_RESOURCE_SHARED", "智能体存在非专属频道路由，需先核对", 409)
            accounts.add(account_key(match["accountId"]))
    for row in bindings:
        match = row.get("match") or {}
        if match.get("channel") == plan["channel"] and account_key(match.get("accountId", "*")) in accounts and row.get("agentId") != agent_id:
            raise AppError("DELETION_RESOURCE_SHARED", "微信账号正被其他智能体使用", 409)
    return {**plan, "account_ids": sorted(accounts)}


async def preflight(plan):
    snapshot = await gateway.admin_rpc("config.get")
    if not snapshot.get("hash"):
        raise AppError("DELETION_CONFIG_INVALID", "Gateway 未返回配置版本，无法安全删除", 503)
    return validate_config(plan, snapshot.get("config"))


async def stop_login(plan):
    payload = await wechat_login.call(plan["class_id"], "cancel")
    accounts = payload.get("accountIds")
    if not isinstance(accounts, list) or any(not isinstance(value, str) for value in accounts):
        raise AppError("WECHAT_PLUGIN_UPDATE_REQUIRED", "请更新微信兼容层后重试删除", 503)
    return {**plan, "account_ids": sorted({account_key(value) for value in [*plan["account_ids"], *accounts]})}


async def check_idle(plan):
    result = await gateway.admin_rpc("tasks.list", {"agentId": plan["agent_id"], "status": ["queued", "running"], "limit": 1})
    if not isinstance(result, dict) or not isinstance(result.get("tasks"), list):
        raise AppError("DELETION_TASK_STATUS_UNKNOWN", "无法确认智能体任务是否结束", 503)
    if result["tasks"]:
        raise AppError("DELETION_BUSY", "本班智能体仍有任务运行，请结束后重试清理", 409)


async def cleanup(plan):
    snapshot = await gateway.admin_rpc("config.get")
    config = snapshot.get("config")
    checked = validate_config(plan, config)
    if checked["account_ids"] != plan["account_ids"]:
        raise AppError("DELETION_CONFIG_CHANGED", "微信绑定发生变化，请重试以重新核对", 409)
    cancelled = await stop_login(plan)
    if cancelled["account_ids"] != plan["account_ids"]:
        raise AppError("DELETION_CONFIG_CHANGED", "发现尚未入库的微信账号，请重试以保存清理清单", 409)
    for account in plan["account_ids"]:
        result = await gateway.admin_rpc("channels.logout", {"channel": plan["channel"], "accountId": account})
        if not isinstance(result, dict) or result.get("cleared") is not True or result.get("loggedOut") is not True:
            raise AppError("DELETION_WECHAT_INCOMPLETE", "微信账号尚未完成停止及本地凭据清理", 503)
    await check_idle(plan)
    # Read a fresh version after stopping the channel; never write an old snapshot.
    snapshot = await gateway.admin_rpc("config.get")
    config = snapshot.get("config")
    if validate_config(plan, config)["account_ids"] != plan["account_ids"] or not snapshot.get("hash"):
        raise AppError("DELETION_CONFIG_CHANGED", "Gateway 配置已变化，请重试", 409)
    agents, bindings, mapping = _parts(config)
    agent_id = plan["agent_id"]
    patch = {
        "agents": {"list": [row for row in agents if (row.get("id") or row.get("agentId")) != agent_id]},
        "bindings": [row for row in bindings if row.get("agentId") != agent_id],
        "plugins": {"entries": {"classclaw": {"config": {"agentClasses": {
            key: None for key, value in mapping.items() if key == agent_id or value == plan["class_id"]
        }}}}},
    }
    channel_accounts = _channel_accounts(config, plan["channel"])
    removed_accounts = {key: None for key in channel_accounts if account_key(key) in plan["account_ids"]}
    if removed_accounts:
        patch["channels"] = {plan["channel"]: {"accounts": removed_accounts}}
    await gateway.admin_rpc("config.patch", {"raw": json.dumps(patch), "baseHash": snapshot["hash"],
        "replacePaths": ["agents.list", "bindings"], "restartDelayMs": 500, "note": "ClassClaw class deletion"})
    await verify(plan)


async def verify(plan):
    for attempt in range(6):
        try:
            config = (await gateway.admin_rpc("config.get"))["config"]
            validate_config(plan, config)
            agents, bindings, mapping = _parts(config)
            channel_accounts = _channel_accounts(config, plan["channel"])
            if (any(account_key(key) in plan["account_ids"] for key in channel_accounts)
                    or any((row.get("id") or row.get("agentId")) == plan["agent_id"] for row in agents)
                    or any(row.get("agentId") == plan["agent_id"] for row in bindings)
                    or plan["agent_id"] in mapping or plan["class_id"] in mapping.values()):
                raise AppError("DELETION_CONFIG_INCOMPLETE", "Gateway 配置仍有本班资源", 503)
            await check_idle(plan)
            return
        except AppError as exc:
            if exc.status_code == 409 or exc.code == "DELETION_CONFIG_INVALID" or attempt == 5:
                raise
            await asyncio.sleep(0.5)


def cleanup_files(plan):
    validate_paths(plan)
    settings = gateway.settings
    workspace = remove_tree(settings.openclaw_class_workspace_root, Path(plan["workspace"]))
    state = remove_tree(settings.openclaw_state_dir / "agents", settings.openclaw_state_dir / "agents" / plan["agent_id"])
    return {"runtime_removed": True, "workspace_removed": workspace, "agent_state_removed": state,
            "agent_id": plan["agent_id"], "wechat_accounts_removed": plan["account_ids"]}
