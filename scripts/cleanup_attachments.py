from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import select

from app.config import settings
from app.database import reader_session
from app.models.entities import Attachment, AttachmentLink


def list_orphans() -> list[Attachment]:
    with reader_session() as db:
        linked = select(AttachmentLink.attachment_id)
        return list(db.scalars(select(Attachment).where(Attachment.id.not_in(linked))))


if __name__ == "__main__":
    rows = list_orphans()
    print(f"发现 {len(rows)} 个无引用附件。此脚本默认只报告，不自动删除。")
    for row in rows:
        print(row.id, row.stored_path)
