from __future__ import annotations

import json
import shutil
import sqlite3
import sys
import tempfile
from contextlib import closing
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import settings
from scripts.backup import database_path, sqlite_snapshot, validate_sqlite


def restore(backup_dir: Path) -> None:
    backup_dir = backup_dir.resolve()
    database_backup = backup_dir / "classclaw.db"
    tables = validate_sqlite(database_backup)
    if "users" not in tables:
        raise RuntimeError("备份不包含有效的 ClassClaw 业务库")
    usage_backup = backup_dir / "usage.db"
    manifest_path = backup_dir / "manifest.json"
    if manifest_path.exists():
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        databases = manifest.get("databases")
        if manifest.get("version") != 2 or databases not in (
            {"core": "classclaw.db", "usage": "usage.db"}, {"core": "classclaw.db", "usage": None},
        ):
            raise RuntimeError("不支持的备份清单")
        if databases["usage"] == "usage.db" and not usage_backup.is_file():
            raise RuntimeError("双库备份缺少 usage.db，尚未覆盖当前数据")
        if databases["usage"] is None and usage_backup.exists():
            raise RuntimeError("用量库与备份清单不一致")
    if usage_backup.exists():
        if "ai_usage_records" not in validate_sqlite(usage_backup):
            raise RuntimeError("用量库备份缺少 ai_usage_records 表")
    elif "ai_usage_records" not in tables:
        raise RuntimeError("缺少独立用量库且业务库内无旧用量表，备份不完整")
    destination = database_path(settings.database_url)
    usage_destination = database_path(settings.usage_database_url)
    attachment_backup = backup_dir / "attachments"
    attachment_target = settings.attachment_dir.resolve()
    if backup_dir.is_relative_to(attachment_target) or attachment_target.is_relative_to(backup_dir):
        raise RuntimeError("备份目录与当前附件目录不能重叠")
    for path in (destination, usage_destination):
        if path.is_relative_to(backup_dir) or (path.exists() and any(path.samefile(src) for src in (database_backup, usage_backup) if src.exists())):
            raise RuntimeError("恢复目标不能覆盖备份本身")
    # All backup files are checked before the first destructive write. Each DB
    # is restored consistently, but the two replacements are not jointly atomic.
    sqlite_snapshot(database_backup, destination)
    if usage_backup.exists():
        sqlite_snapshot(usage_backup, usage_destination)
    else:
        # A legacy backup must not retain unrelated counters from the current
        # deployment. The next startup migrates its legacy rows into this DB.
        with tempfile.TemporaryDirectory(prefix="classclaw-restore-") as temporary:
            empty = Path(temporary) / "usage.db"
            with closing(sqlite3.connect(empty)) as connection:
                connection.execute("PRAGMA user_version=0")
            sqlite_snapshot(empty, usage_destination)
    if attachment_backup.exists():
        if settings.attachment_dir.exists():
            shutil.rmtree(settings.attachment_dir)
        shutil.copytree(attachment_backup, settings.attachment_dir)


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit("用法: python scripts/restore.py <备份目录>（恢复前请停止服务）")
    restore(Path(sys.argv[1]))
    print("恢复完成")
