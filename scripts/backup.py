from __future__ import annotations

import json
import shutil
import sqlite3
import sys
import time
from contextlib import closing
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import settings
from app.utils.time import now


def database_path(url: str) -> Path:
    if not url.startswith("sqlite:///") or url.endswith(":memory:"):
        raise RuntimeError("备份恢复仅支持文件型 SQLite 数据库")
    return Path(url.removeprefix("sqlite:///")).resolve()


def validate_sqlite(path: Path) -> set[str]:
    if not path.is_file():
        raise RuntimeError(f"数据库文件缺失：{path.name}")
    with closing(sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True)) as connection:
        if connection.execute("PRAGMA quick_check").fetchall() != [("ok",)]:
            raise RuntimeError(f"数据库完整性检查失败：{path.name}")
        return {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}


def sqlite_snapshot(source: Path, destination: Path) -> None:
    if source.resolve() == destination.resolve() or (destination.exists() and source.samefile(destination)):
        raise RuntimeError("备份源与目标不能是同一个文件")
    destination.parent.mkdir(parents=True, exist_ok=True)
    deadline = time.monotonic() + 30

    def progress(_status, _remaining, _total):
        if time.monotonic() > deadline:
            raise TimeoutError("数据库备份/恢复超时；恢复前请停止服务")

    with closing(sqlite3.connect(f"{source.resolve().as_uri()}?mode=ro", uri=True)) as src, closing(sqlite3.connect(destination)) as dst:
        # Use the backup API for restore too, so stale WAL pages cannot
        # overwrite a newly restored database as they can with shutil.copy2.
        src.backup(dst, pages=256, progress=progress, sleep=0.05)


def backup(target_root: Path) -> Path:
    source = database_path(settings.database_url)
    usage_source = database_path(settings.usage_database_url)
    validate_sqlite(source)
    if usage_source.exists() and "ai_usage_records" not in validate_sqlite(usage_source):
        raise RuntimeError("用量数据库缺少 ai_usage_records 表")
    stamp = now().strftime("%Y%m%d-%H%M%S-%f")
    target = target_root / f"classclaw-backup-{stamp}"
    if target.resolve().is_relative_to(settings.attachment_dir.resolve()):
        raise RuntimeError("备份目录不能位于附件目录内")
    target.mkdir(parents=True, exist_ok=False)
    sqlite_snapshot(source, target / "classclaw.db")
    # Decide legacy status from the snapshot, not a possibly changing live DB.
    legacy = "ai_usage_records" in validate_sqlite(target / "classclaw.db")
    usage_file = None
    if usage_source.exists():
        sqlite_snapshot(usage_source, target / "usage.db")
        validate_sqlite(target / "usage.db")
        usage_file = "usage.db"
    elif not legacy:
        raise RuntimeError("已拆分的用量库缺失，备份未完成；请修复后重新备份")
    if settings.attachment_dir.exists():
        shutil.copytree(settings.attachment_dir, target / "attachments")
    (target / "manifest.json").write_text(json.dumps({
        "version": 2, "created_at": now().isoformat(),
        "databases": {"core": "classclaw.db", "usage": usage_file},
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    return target


if __name__ == "__main__":
    root = Path(sys.argv[1] if len(sys.argv) > 1 else "./backups")
    root.mkdir(parents=True, exist_ok=True)
    print(backup(root))
