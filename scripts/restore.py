from __future__ import annotations

import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import settings


def restore(backup_dir: Path) -> None:
    database_backup = backup_dir / "classclaw.db"
    if not database_backup.is_file() or not settings.database_url.startswith("sqlite:///"):
        raise RuntimeError("备份目录无有效classclaw.db，或当前数据库不是SQLite")
    destination = Path(settings.database_url.removeprefix("sqlite:///"))
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(database_backup, destination)
    attachment_backup = backup_dir / "attachments"
    if attachment_backup.exists():
        if settings.attachment_dir.exists():
            shutil.rmtree(settings.attachment_dir)
        shutil.copytree(attachment_backup, settings.attachment_dir)


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit("用法: python scripts/restore.py <备份目录>（恢复前请停止服务）")
    restore(Path(sys.argv[1]))
    print("恢复完成")
