"""Targeted cleanup for deletion residue. Reports by default; --apply deletes.

Covers the leftovers a failed or interrupted deletion can leave behind:
orphaned exam statistics caches, agent state directories without a binding,
unreferenced attachments, unowned files in the attachment directory, stale
Gateway agentClasses mappings and retired WeChat accounts.

Run from the project root with the service stopped for --apply.
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import select
from sqlalchemy.exc import OperationalError

from app.config import settings
from app.core.errors import AppError
from app.database import reader_session, writer_session
from app.models.entities import (
    AnalysisCache,
    Attachment,
    AttachmentLink,
    AuditLog,
    ClassAgentBinding,
    ClassRoom,
    DeletionOperation,
    Exam,
)
from app.services import deletion_files, deletion_runtime, openclaw_provisioning
from app.services.class_student import referenced_attachment_ids

CACHE_PREFIX = "exam_statistics:"


def _default_agent_ids() -> set[str]:
    return {"main", *filter(None, (settings.openclaw_agent_id, settings.openclaw_extractor_agent_id))}


def orphan_exam_caches(db) -> list[AnalysisCache]:
    rows = list(db.scalars(select(AnalysisCache).where(AnalysisCache.kind.like(f"{CACHE_PREFIX}%"))))
    exam_ids = set(db.scalars(select(Exam.id)))
    return [row for row in rows if row.kind.removeprefix(CACHE_PREFIX) not in exam_ids]


def orphan_state_dirs(db) -> list[Path]:
    root = settings.openclaw_state_dir / "agents"
    if not root.is_dir():
        return []
    keep = _default_agent_ids() | {row.openclaw_agent_id for row in db.scalars(select(ClassAgentBinding)) if row.openclaw_agent_id}
    return [child for child in sorted(root.iterdir()) if child.is_dir() and child.name not in keep]


def orphan_attachments(db) -> list[Attachment]:
    linked = set(db.scalars(select(AttachmentLink.attachment_id)))
    rows = [row for row in db.scalars(select(Attachment)) if row.id not in linked]
    if not rows:
        return []
    referenced = referenced_attachment_ids(db, {row.id for row in rows})
    return [row for row in rows if row.id not in referenced]


def disk_only_files(db) -> list[Path]:
    root = settings.attachment_dir
    if not root.is_dir():
        return []
    known = {row.stored_path for row in db.scalars(select(Attachment))}
    found = []
    for path in sorted(root.rglob("*")):
        if not path.is_file() or path.is_symlink() or path.name in {".gitkeep", ".DS_Store"}:
            continue
        relative = str(path.relative_to(root.parent))
        if relative not in known:
            found.append(path)
    return found


def unfinished_deletions(db) -> list[DeletionOperation]:
    return list(db.scalars(select(DeletionOperation).where(DeletionOperation.status != "complete")))


async def gateway_snapshot() -> dict:
    snapshot = await openclaw_provisioning.admin_rpc("config.get")
    if not snapshot.get("hash"):
        raise RuntimeError("Gateway 未返回配置版本")
    return snapshot


def deleted_class_ids(db) -> set[str]:
    return set(db.scalars(select(AuditLog.entity_id).where(AuditLog.entity_type == "class", AuditLog.action == "hard_delete")))


def stale_mappings(db, config: dict) -> dict[str, str]:
    _agents, _bindings, mapping = deletion_runtime._parts(config)
    class_ids = set(db.scalars(select(ClassRoom.id)))
    agent_ids = {row.openclaw_agent_id for row in db.scalars(select(ClassAgentBinding)) if row.openclaw_agent_id}
    deleted = deleted_class_ids(db)
    return {key: value for key, value in mapping.items()
            if key not in agent_ids and key not in _default_agent_ids() and value not in class_ids and value in deleted}


def check_retired_accounts(db, config, accounts):
    """An explicit account ID alone is not evidence that its owner was deleted."""
    _agents, bindings, _mapping = deletion_runtime._parts(config)
    claimed = {row.channel_account_id for row in db.scalars(select(ClassAgentBinding)) if row.channel_account_id}
    claimed.update(row["match"].get("accountId") for row in bindings if row["match"].get("channel") == settings.openclaw_wechat_channel)
    if {deletion_runtime.account_key(value) for value in claimed if value}.intersection(deletion_runtime.account_key(value) for value in accounts):
        raise RuntimeError("微信账号仍有数据库或 Gateway 路由引用，拒绝维护脚本登出")
    deleted = deleted_class_ids(db) - set(db.scalars(select(ClassRoom.id)))
    proven = set()
    # Read only historical routing metadata. Never print or retain config secrets.
    for path in settings.openclaw_state_dir.glob("openclaw.json.bak*"):
        if path.is_symlink() or not path.is_file():
            continue
        try:
            _agents, old_bindings, old_mapping = deletion_runtime._parts(json.loads(path.read_text()))
        except (OSError, ValueError, AppError):
            continue
        for row in old_bindings:
            match = row["match"]
            if match.get("channel") == settings.openclaw_wechat_channel and old_mapping.get(row.get("agentId")) in deleted:
                proven.add(match.get("accountId"))
    if {deletion_runtime.account_key(value) for value in accounts} - {deletion_runtime.account_key(value) for value in proven if value}:
        raise RuntimeError("无法从删除审计及历史绑定证明微信账号归属，拒绝清理")


# Exact upload payloads from the two historical tests that wrote into data/.
# Only UUID-named, metadata-free files matching these bytes are eligible.
_TEST_PAYLOADS = {".csv": {"星期,节次,科目\n周一,1,语文", "学号,姓名\n001,张三\n"}, ".txt": {"张三 李四"}}
_TEST_HASHES = {suffix: {hashlib.sha256(value.encode()).hexdigest() for value in values} for suffix, values in _TEST_PAYLOADS.items()}


def confirmed_test_file(path):
    try:
        uuid.UUID(path.stem)
        deletion_files.safe_path(settings.attachment_dir, path)
        return path.stat().st_size < 1024 and hashlib.sha256(path.read_bytes()).hexdigest() in _TEST_HASHES.get(path.suffix, set())
    except (ValueError, OSError, AppError):
        return False


def validate_apply_selection(args):
    if args.state_dirs or args.attachments:
        raise RuntimeError("禁止按无绑定/无引用条件批量删除；请使用 --state-dir 或 --attachment 指定已核实目标")


async def validate_state_targets(args, config):
    with reader_session() as db:
        deleted = deleted_class_ids(db) - set(db.scalars(select(ClassRoom.id)))
        available = {path.name for path in orphan_state_dirs(db)}
    for name in args.state_dir:
        class_id = name.removeprefix("classclaw-")
        if name != f"classclaw-{class_id}" or class_id not in deleted or name in _default_agent_ids():
            raise RuntimeError("状态目录缺少明确的班级删除审计，拒绝清理")
        if name not in available:
            continue
        plan = {"class_id": class_id, "agent_id": name, "workspace": str(settings.openclaw_class_workspace_root / class_id),
                "channel": settings.openclaw_wechat_channel, "account_ids": []}
        deletion_runtime.validate_config(plan, config)
        agents, bindings, _mapping = deletion_runtime._parts(config)
        if any((row.get("id") or row.get("agentId")) == name for row in agents) or any(row.get("agentId") == name for row in bindings):
            raise RuntimeError("Agent 仍在 Gateway 配置中，拒绝清理会话目录")
        await deletion_runtime.check_idle(plan)


async def apply_gateway(args, stale: dict[str, str], accounts: list[str]) -> None:
    snapshot = await gateway_snapshot()
    config = snapshot.get("config") or {}
    with reader_session() as db:
        current_stale = stale_mappings(db, config)
        if any(current_stale.get(key) != value for key, value in stale.items()):
            raise RuntimeError("待清理映射的归属已变化，请重新检查")
        check_retired_accounts(db, config, accounts)
    patch: dict = {}
    if stale:
        patch.setdefault("plugins", {}).setdefault("entries", {}).setdefault("classclaw", {}).setdefault("config", {})["agentClasses"] = {key: None for key in stale}
    channel = settings.openclaw_wechat_channel
    channel_accounts = ((config.get("channels") or {}).get(channel) or {}).get("accounts")
    if accounts and isinstance(channel_accounts, dict):
        present = [account for account in accounts if account in channel_accounts]
        if present:
            patch.setdefault("channels", {})[channel] = {"accounts": {account: None for account in present}}
    errors: list[str] = []
    for account in accounts:
        try:
            result = await openclaw_provisioning.admin_rpc("channels.logout", {"channel": channel, "accountId": account})
            if not isinstance(result, dict) or result.get("cleared") is not True or result.get("loggedOut") is not True:
                errors.append(f"微信账号 {account} 未完成登出或本地凭据清理")
            else:
                print(f"  已登出微信账号 {account}")
        except (OSError, RuntimeError, AppError) as exc:
            errors.append(f"微信账号 {account}：{exc}")
    if errors:
        raise RuntimeError("；".join(errors))
    if patch:
        await openclaw_provisioning.admin_rpc("config.patch", {
            "raw": json.dumps(patch, ensure_ascii=False),
            "baseHash": snapshot["hash"],
            "note": "ClassClaw targeted deletion cleanup",
            "restartDelayMs": 500,
        })
        print(f"  已清理 Gateway 映射 {len(stale)} 条")
    if stale or accounts:
        for attempt in range(6):
            config = (await gateway_snapshot())["config"]
            remaining_accounts = deletion_runtime._channel_accounts(config, channel)
            if not any(key in deletion_runtime._parts(config)[2] for key in stale) and not any(account in remaining_accounts for account in accounts):
                break
            if attempt == 5:
                errors.append("Gateway 仍有残留映射或账号配置，请核对后重试")
                break
            await asyncio.sleep(0.5)
    if errors:
        raise RuntimeError("；".join(errors))


async def _gateway_cleanup(args) -> None:
    """All Gateway work shares one event loop; the HTTP client is loop-bound."""
    snapshot = await gateway_snapshot()
    with reader_session() as db:
        stale = stale_mappings(db, snapshot.get("config") or {}) if args.gateway else {}
        check_retired_accounts(db, snapshot.get("config") or {}, args.wechat_account)
    await validate_state_targets(args, snapshot.get("config") or {})
    await apply_gateway(args, stale, args.wechat_account)


def report() -> None:
    with reader_session() as db:
        caches = orphan_exam_caches(db)
        states = orphan_state_dirs(db)
        attachments = orphan_attachments(db)
        files = disk_only_files(db)
        pending = unfinished_deletions(db)
        print(f"孤儿考试统计缓存：{len(caches)} 条")
        for row in caches:
            print(f"  {row.kind}")
        print(f"无绑定的 Agent 状态目录：{len(states)} 个")
        for path in states:
            print(f"  {path}")
        print(f"数据库内无引用附件：{len(attachments)} 个")
        for row in attachments:
            print(f"  {row.id} {row.stored_path}")
        print(f"附件目录中无记录的疑似测试残留文件：{len(files)} 个")
        for path in files[:20]:
            print(f"  {path}")
        if len(files) > 20:
            print(f"  … 其余 {len(files) - 20} 个")
        print(f"未完成的删除记录：{len(pending)} 条")
        for row in pending:
            print(f"  {row.id} {row.target_type} {row.target_id} phase={row.phase} status={row.status} error={row.error_code}")


def apply(args) -> None:
    validate_apply_selection(args)
    with writer_session() as db:
        if args.caches:
            caches = orphan_exam_caches(db)
            for row in caches:
                db.delete(row)
            db.commit()
            print(f"已删除孤儿考试统计缓存 {len(caches)} 条")

        if args.state_dirs or args.state_dir:
            available = {path.name: path for path in orphan_state_dirs(db)}
            targets = [path for path in available.values()] if args.state_dirs else [
                available.get(name) for name in args.state_dir
            ]
            removed = 0
            for path in targets:
                if path is None:
                    continue
                if deletion_files.remove_tree(settings.openclaw_state_dir / "agents", path):
                    removed += 1
            missing = [name for name in args.state_dir if name not in available]
            if missing:
                print(f"  未找到或仍有绑定的状态目录：{', '.join(missing)}")
            print(f"已删除 Agent 状态目录 {removed} 个")

        if args.attachments or args.attachment:
            rows = orphan_attachments(db)
            if args.attachment:
                rows = [row for row in rows if row.id in set(args.attachment)]
            removed_files = 0
            for row in rows:
                path = deletion_files.safe_path(settings.attachment_dir, settings.attachment_dir.parent / row.stored_path)
                if path.exists():
                    path.unlink()
                    removed_files += 1
                db.delete(row)
            db.commit()
            print(f"已删除无引用附件 {len(rows)} 个（文件 {removed_files} 个）")

        if args.unknown_files:
            removed = 0
            for path in disk_only_files(db):
                if not confirmed_test_file(path):
                    continue
                try:
                    deletion_files.safe_path(settings.attachment_dir, path)
                except AppError as exc:
                    print(f"  跳过不安全路径 {path}：{exc.message}")
                    continue
                path.unlink()
                removed += 1
            print(f"已删除内容匹配历史测试且无数据库记录的文件 {removed} 个")


def disk_only_files_standalone() -> list[Path]:
    with reader_session() as db:
        return disk_only_files(db)


def main() -> int:
    parser = argparse.ArgumentParser(description="定向清理删除残留；默认只检查，--apply 才执行。")
    parser.add_argument("--apply", action="store_true", help="执行清理（默认只报告）")
    parser.add_argument("--caches", action="store_true", help="删除已删除考试的统计缓存")
    parser.add_argument("--state-dirs", action="store_true", help="已禁用：请用 --state-dir 明确指定目录")
    parser.add_argument("--state-dir", action="append", default=[], help="只删除指定名称的 Agent 状态目录，可重复")
    parser.add_argument("--attachments", action="store_true", help="已禁用：请用 --attachment 明确指定已核实附件")
    parser.add_argument("--attachment", action="append", default=[], help="只删除指定附件 ID，可重复")
    parser.add_argument("--unknown-files", action="store_true", help="仅删除 UUID 文件名、无数据库记录且内容匹配历史测试的文件")
    parser.add_argument("--gateway", action="store_true", help="清理 Gateway 中指向已删除班级/智能体的 agentClasses 映射")
    parser.add_argument("--wechat-account", action="append", default=[], help="需要登出并清除本地凭据的微信账号，可重复")
    args = parser.parse_args()

    try:
        report()
    except OperationalError:
        print("数据库缺少 deletion_operations 等新表，请先运行 alembic upgrade head 再重试。")
        return 1
    if not args.apply:
        print("当前为只检查模式；确认以上内容后加 --apply 与对应清理项执行。")
        return 0
    if not any((args.caches, args.state_dirs, args.state_dir, args.attachments, args.attachment, args.unknown_files, args.gateway, args.wechat_account)):
        print("--apply 需要至少一个清理项（--caches/--state-dirs/--state-dir/--attachments/--attachment/--unknown-files/--gateway/--wechat-account）。")
        return 1

    try:
        validate_apply_selection(args)
    except RuntimeError as exc:
        print(str(exc))
        return 1
    if args.gateway or args.wechat_account or args.state_dir:
        try:
            asyncio.run(_gateway_cleanup(args))
        except (OSError, RuntimeError, AppError) as exc:
            print(f"Gateway 清理失败：{exc}")
            return 1
    apply(args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
