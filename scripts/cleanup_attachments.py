import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.database import reader_session
from app.services.class_student import list_unreferenced_attachments


if __name__ == "__main__":
    with reader_session() as db:
        rows = list_unreferenced_attachments(db)
    print(f"发现 {len(rows)} 个无引用附件。此脚本默认只报告，不自动删除。")
    for row in rows:
        print(row.id, row.stored_path)
