from __future__ import annotations

import shutil
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import settings


def backup(target_root: Path) -> Path:
    if not settings.database_url.startswith("sqlite:///"):
        raise RuntimeError("备份脚本仅支持SQLite")
    source = Path(settings.database_url.removeprefix("sqlite:///"))
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    target = target_root / f"classclaw-backup-{stamp}"
    target.mkdir(parents=True, exist_ok=False)
    with sqlite3.connect(source) as src, sqlite3.connect(target / "classclaw.db") as dst:
        src.backup(dst)
    if settings.attachment_dir.exists():
        shutil.copytree(settings.attachment_dir, target / "attachments")
    return target


if __name__ == "__main__":
    root = Path(sys.argv[1] if len(sys.argv) > 1 else "./backups")
    root.mkdir(parents=True, exist_ok=True)
    print(backup(root))
