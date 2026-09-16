from __future__ import annotations

from app.config import settings
from app.models.entities import Attachment, AttachmentLink


def test_class_attachment_list_and_delete(client, sample):
    cls, _, _ = sample
    created = client.post("/api/v1/attachments", data={"class_id": cls.id}, files={"file": ("班级资料.txt", b"hello")})
    assert created.status_code == 201, created.text
    attachment = created.json()["data"]
    path = settings.attachment_dir.parent / attachment["stored_path"]
    assert path.exists()

    listed = client.get(f"/api/v1/classes/{cls.id}/attachments")
    assert listed.status_code == 200, listed.text
    rows = listed.json()["data"]
    assert [row["id"] for row in rows] == [attachment["id"]]
    assert rows[0]["original_name"] == "班级资料.txt"
    assert rows[0]["file_size"] == 5

    deleted = client.delete(f"/api/v1/attachments/{attachment['id']}")
    assert deleted.status_code == 200, deleted.text
    assert deleted.json()["data"]["file_removed"] is True
    assert not path.exists()
    assert client.get(f"/api/v1/classes/{cls.id}/attachments").json()["data"] == []


def test_delete_attachment_without_class_link_is_rejected(client, db):
    row = Attachment(original_name="orphan.txt", stored_name="orphan.txt", stored_path="attachments/orphan.txt",
                     mime_type="text/plain", file_size=1, sha256="a" * 64)
    db.add(row)
    db.commit()

    response = client.delete(f"/api/v1/attachments/{row.id}")
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "CLASS_SCOPE_VIOLATION"


def test_shared_attachment_cannot_be_deleted(client, db, sample):
    cls, _, _ = sample
    created = client.post("/api/v1/attachments", data={"class_id": cls.id}, files={"file": ("证明.jpg", b"image")})
    attachment = created.json()["data"]
    db.add(AttachmentLink(attachment_id=attachment["id"], entity_type="student_event", entity_id="event-1"))
    db.commit()

    response = client.delete(f"/api/v1/attachments/{attachment['id']}")
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "ATTACHMENT_IN_USE"
    assert (settings.attachment_dir.parent / attachment["stored_path"]).exists()
    assert db.get(Attachment, attachment["id"]) is not None


def test_attachment_shared_by_two_classes_cannot_be_deleted(client, db, sample):
    cls, other, _ = sample
    created = client.post("/api/v1/attachments", data={"class_id": cls.id}, files={"file": ("共享.txt", b"shared")}).json()["data"]
    db.add(AttachmentLink(attachment_id=created["id"], entity_type="class", entity_id=other.id))
    db.commit()
    response = client.delete(f"/api/v1/attachments/{created['id']}")
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "ATTACHMENT_IN_USE"
    assert db.get(Attachment, created["id"]) is not None
    assert (settings.attachment_dir.parent / created["stored_path"]).exists()


def test_attachment_file_failure_keeps_metadata_and_retry_succeeds(client, db, sample, monkeypatch):
    from pathlib import Path

    cls, _, _ = sample
    created = client.post("/api/v1/attachments", data={"class_id": cls.id}, files={"file": ("重试.txt", b"retry")}).json()["data"]
    path = settings.attachment_dir.parent / created["stored_path"]
    unlink = Path.unlink

    def fail(target, *args, **kwargs):
        if target == path:
            raise PermissionError("locked")
        return unlink(target, *args, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(Path, "unlink", fail)
        response = client.delete(f"/api/v1/attachments/{created['id']}")
    assert response.status_code == 503
    assert response.json()["success"] is False
    assert response.json()["error"]["code"] == "ATTACHMENT_DELETE_FAILED"
    assert db.get(Attachment, created["id"]) is not None
    assert path.exists()
    retried = client.delete(f"/api/v1/attachments/{created['id']}")
    assert retried.status_code == 200
    assert not path.exists()
    assert db.get(Attachment, created["id"]) is None


def test_attachment_path_rejection_keeps_metadata_and_external_file(client, db, sample, tmp_path):
    cls, _, _ = sample
    outside = tmp_path / "keep.txt"
    outside.write_text("keep")
    attachment = Attachment(original_name="unsafe.txt", stored_name="unsafe.txt", stored_path=str(outside),
                            mime_type="text/plain", file_size=4, sha256="b" * 64)
    db.add(attachment)
    db.flush()
    db.add(AttachmentLink(attachment_id=attachment.id, entity_type="class", entity_id=cls.id))
    db.commit()
    response = client.delete(f"/api/v1/attachments/{attachment.id}")
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "DELETION_UNSAFE_PATH"
    assert db.get(Attachment, attachment.id) is not None
    assert outside.read_text() == "keep"


def test_attachment_missing_file_can_be_retried_after_interruption(client, db, sample):
    cls, _, _ = sample
    created = client.post("/api/v1/attachments", data={"class_id": cls.id}, files={"file": ("中断.txt", b"retry")}).json()["data"]
    (settings.attachment_dir.parent / created["stored_path"]).unlink()
    response = client.delete(f"/api/v1/attachments/{created['id']}")
    assert response.status_code == 200
    assert db.get(Attachment, created["id"]) is None
