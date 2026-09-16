from __future__ import annotations

import asyncio

import pytest
from sqlalchemy import select

from app.config import settings
from app.core.errors import AppError
from app.models.entities import Attachment, AttachmentLink, ClassRoom, DeletionOperation
from app.services import ai_tasks, deletion_files, deletion_runtime, openclaw_bridge
from app.utils.time import today


def _pending_job(db, cls, *, phase="business") -> DeletionOperation:
    job = DeletionOperation(target_type="class", target_id=cls.id, status="failed", phase=phase,
                            resources_json={}, result_json={})
    db.add(job)
    db.commit()
    return job


def test_pending_class_deletion_blocks_writes_and_delete_resumes(client, db, sample):
    cls, _other, _students = sample
    job = _pending_job(db, cls)

    blocked = client.post("/api/v1/students", json={"class_id": cls.id, "student_no": "099", "name": "新生"})
    assert blocked.status_code == 409, blocked.text
    assert blocked.json()["error"]["code"] == "DELETION_IN_PROGRESS"

    # Reads stay available while cleanup is unfinished.
    assert client.get(f"/api/v1/classes/{cls.id}/summary").status_code == 200

    resumed = client.delete(f"/api/v1/classes/{cls.id}")
    assert resumed.status_code == 200, resumed.text
    assert resumed.json()["data"]["cleanup_status"] == "complete"
    assert db.get(ClassRoom, cls.id) is None
    assert db.get(DeletionOperation, job.id).status == "complete"


def test_proposal_creation_rejected_while_class_deletion_pending(client, db, sample):
    cls, _other, _students = sample
    _pending_job(db, cls)

    response = client.post("/api/v1/write-proposals", json={
        "operation_type": "attendance.set",
        "payload": {"class_id": cls.id, "student_no": "001", "attendance_date": str(today()),
                    "period": "morning", "status": "late"},
    })
    assert response.status_code == 409, response.text
    assert response.json()["error"]["code"] == "DELETION_IN_PROGRESS"


def test_proposal_confirmation_rejected_while_class_deletion_pending(client, db, sample):
    cls, _other, _students = sample
    created = client.post("/api/v1/write-proposals", json={
        "operation_type": "attendance.set",
        "payload": {"class_id": cls.id, "student_no": "001", "attendance_date": str(today()),
                    "period": "morning", "status": "late"},
    })
    assert created.status_code == 201, created.text
    proposal_id = created.json()["data"]["id"]

    # The deletion starts after the preview was frozen; execution must still stop.
    _pending_job(db, cls)

    confirmed = client.post(f"/api/v1/write-proposals/{proposal_id}/confirm", json={"revision": 1, "confirmed_by": "班主任"})
    assert confirmed.status_code == 409, confirmed.text
    assert confirmed.json()["error"]["code"] == "DELETION_IN_PROGRESS"
    batched = client.post("/api/v1/write-proposals/confirm-batch",
                          json={"items": [{"proposal_id": proposal_id, "revision": 1}], "confirmed_by": "班主任"})
    assert batched.status_code == 409, batched.text
    assert batched.json()["error"]["code"] == "DELETION_IN_PROGRESS"


def test_route_guards_block_proposal_create_and_confirm_when_service_check_is_bypassed(client, db, sample, monkeypatch):
    cls, _other, _students = sample
    payload = {"class_id": cls.id, "student_no": "001", "attendance_date": str(today()), "period": "morning", "status": "late"}
    created = client.post("/api/v1/write-proposals", json={"operation_type": "attendance.set", "payload": payload})
    assert created.status_code == 201, created.text
    proposal_id = created.json()["data"]["id"]

    _pending_job(db, cls)
    # Disable the service-layer guard to prove the route-layer defense fires on its own.
    monkeypatch.setattr("app.services.deletions.require_available", lambda *args, **kwargs: None)

    blocked = client.post("/api/v1/write-proposals", json={"operation_type": "attendance.set", "payload": payload})
    assert blocked.status_code == 409, blocked.text
    assert blocked.json()["error"]["code"] == "DELETION_IN_PROGRESS"

    confirmed = client.post(f"/api/v1/write-proposals/{proposal_id}/confirm", json={"revision": 1, "confirmed_by": "班主任"})
    assert confirmed.status_code == 409, confirmed.text
    assert confirmed.json()["error"]["code"] == "DELETION_IN_PROGRESS"

    batched = client.post("/api/v1/write-proposals/confirm-batch",
                          json={"items": [{"proposal_id": proposal_id, "revision": 1}], "confirmed_by": "班主任"})
    assert batched.status_code == 409, batched.text
    assert batched.json()["error"]["code"] == "DELETION_IN_PROGRESS"


def test_admin_retry_endpoint_completes_failed_deletion(client, db, sample):
    cls, _other, _students = sample
    job = _pending_job(db, cls, phase="business")

    listed = client.get("/api/v1/admin/deletions")
    assert listed.status_code == 200
    ids = [row["id"] for row in listed.json()["data"]]
    assert job.id in ids

    retried = client.post(f"/api/v1/admin/deletions/{job.id}/retry")
    assert retried.status_code == 200, retried.text
    assert retried.json()["data"]["cleanup_status"] == "complete"
    assert db.get(ClassRoom, cls.id) is None
    assert db.get(DeletionOperation, job.id).status == "complete"
    assert client.get("/api/v1/admin/deletions").json()["data"] == []


def test_validate_config_rejects_shared_wechat_account():
    plan = {"class_id": "class-a", "agent_id": "agent-a",
            "workspace": str(settings.openclaw_class_workspace_root / "ws"),
            "channel": settings.openclaw_wechat_channel, "account_ids": ["acc"]}
    config = {
        "agents": {"list": [{"id": "agent-a", "workspace": plan["workspace"]}]},
        "bindings": [
            {"agentId": "agent-a", "match": {"channel": settings.openclaw_wechat_channel, "accountId": "acc"}},
            {"agentId": "agent-b", "match": {"channel": settings.openclaw_wechat_channel, "accountId": "acc"}},
        ],
        "plugins": {"entries": {"classclaw": {"config": {"agentClasses": {"agent-a": "class-a"}}}}},
    }
    with pytest.raises(AppError) as caught:
        deletion_runtime.validate_config(plan, config)
    assert caught.value.code == "DELETION_RESOURCE_SHARED"


def test_validate_config_rejects_ambiguous_class_mapping():
    plan = {"class_id": "class-a", "agent_id": "agent-a",
            "workspace": str(settings.openclaw_class_workspace_root / "ws"), "channel": settings.openclaw_wechat_channel,
            "account_ids": []}
    config = {
        "agents": {"list": [{"id": "agent-a"}, {"id": "agent-b"}]},
        "bindings": [],
        "plugins": {"entries": {"classclaw": {"config": {"agentClasses": {"agent-a": "class-a", "agent-b": "class-a"}}}}},
    }
    with pytest.raises(AppError) as caught:
        deletion_runtime.validate_config(plan, config)
    assert caught.value.code == "DELETION_RESOURCE_SHARED"


@pytest.mark.parametrize("config", [None, {"agents": None}, {"bindings": "oops"}, {"plugins": []},
                                    {"agents": {"list": [None]}}, {"channels": {settings.openclaw_wechat_channel: None}}])
def test_validate_config_refuses_malformed_gateway_config(config):
    plan = {"class_id": "class-a", "agent_id": "agent-a",
            "workspace": str(settings.openclaw_class_workspace_root / "ws"),
            "channel": settings.openclaw_wechat_channel, "account_ids": []}
    with pytest.raises(AppError) as caught:
        deletion_runtime.validate_config(plan, config)
    assert caught.value.code == "DELETION_CONFIG_INVALID"


def test_safe_path_rejects_traversal_and_symlinks(tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    with pytest.raises(AppError) as caught:
        deletion_files.safe_path(root, tmp_path / "outside")
    assert caught.value.code == "DELETION_UNSAFE_PATH"

    link = root / "link"
    link.symlink_to(tmp_path, target_is_directory=True)
    with pytest.raises(AppError) as caught:
        deletion_files.safe_path(root, link / "child")
    assert caught.value.code == "DELETION_UNSAFE_PATH"


def test_analysis_links_staged_attachments_to_the_class(client, db, sample, monkeypatch):
    cls, _other, _students = sample
    attachment = Attachment(original_name="late.pdf", stored_name="late.pdf",
                            stored_path="attachments/2026/01/late.pdf", file_size=3, sha256="a" * 64)
    db.add(attachment)
    db.commit()

    async def analyze(**_kwargs):
        return {"status": "no_action", "confidence": 1, "reasons": ["只是资料"], "operations": []}

    monkeypatch.setattr(openclaw_bridge, "analyze_interaction", analyze)
    response = client.post("/api/v1/interaction-analyses", json={
        "channel": "web", "class_id": cls.id, "text": "这份材料先存着", "attachment_ids": [attachment.id],
    })
    assert response.status_code == 201, response.text
    link = db.scalar(select(AttachmentLink).where(
        AttachmentLink.attachment_id == attachment.id,
        AttachmentLink.entity_type == "class",
        AttachmentLink.entity_id == cls.id,
    ))
    assert link is not None


def test_has_active_scopes_by_class_and_user():
    ai_tasks.reset_for_tests()
    ai_tasks._active_tasks["task-1"] = ai_tasks._ActiveTask(owner="user:u1", event=asyncio.Event(), class_id="c1")
    try:
        assert ai_tasks.has_active(class_id="c1") is True
        assert ai_tasks.has_active(class_id="c2") is False
        assert ai_tasks.has_active(user_id="u1") is True
        assert ai_tasks.has_active(user_id="u2") is False
        assert ai_tasks.has_active() is False
    finally:
        ai_tasks.reset_for_tests()


def test_teacher_can_retry_file_cleanup_after_class_row_is_gone(client, db, sample, monkeypatch):
    from app.models.entities import User
    from app.schemas.auth import UserCreate
    from app.services import accounts
    cls, other, _ = sample
    user = accounts.create_teacher(db, UserCreate(username="retry-owner"))
    cls.owner_user_id = user.id
    db.commit()
    token = client.post("/api/v1/auth/login", json={"username": user.username, "password": "32767"}).json()["data"]["access_token"]
    job = _pending_job(db, cls)
    job.owner_user_id = user.id
    db.commit()
    class_id = cls.id
    from app.services import class_student
    class_student.hard_delete_class(db, class_id, operation=job)
    job.status = "failed"
    db.commit()
    headers = {"Authorization": f"Bearer {token}"}
    response = client.delete(f"/api/v1/classes/{class_id}", headers=headers)
    assert response.status_code == 200, response.text
    assert response.json()["data"]["cleanup_status"] == "complete"
    assert client.delete(f"/api/v1/classes/{other.id}", headers=headers).status_code == 403
    assert db.get(User, user.id) is not None


def test_pending_user_deletion_rejects_new_writes_but_allows_logout(client, db):
    from app.schemas.auth import UserCreate
    from app.services import accounts
    user = accounts.create_teacher(db, UserCreate(username="deleting-user"))
    token = client.post("/api/v1/auth/login", json={"username": user.username, "password": "32767"}).json()["data"]["access_token"]
    db.add(DeletionOperation(target_type="user", target_id=user.id, phase="business", status="failed"))
    db.commit()
    headers = {"Authorization": f"Bearer {token}"}
    blocked = client.post("/api/v1/class-onboarding/sessions", json={}, headers=headers)
    assert blocked.status_code == 409, blocked.text
    assert blocked.json()["error"]["code"] == "DELETION_IN_PROGRESS"
    assert client.get("/api/v1/auth/me", headers=headers).status_code == 200
    assert client.post("/api/v1/auth/logout", headers=headers).status_code == 200


def test_file_retry_rechecks_workspaces_adopted_by_other_classes(client, db, sample, monkeypatch):
    from app.models.entities import ClassAgentBinding
    cls, other, _ = sample
    workspace = settings.openclaw_class_workspace_root / cls.id
    workspace.mkdir(parents=True)
    marker = workspace / "keep.txt"
    marker.write_text("adopted")
    plan = {"class_id": cls.id, "agent_id": "old-agent", "workspace": str(workspace),
            "channel": settings.openclaw_wechat_channel, "account_ids": []}
    job = _pending_job(db, cls, phase="files")
    job.resources_json = {"runtime": plan}
    job.result_json = {"deleted": True}
    db.add(ClassAgentBinding(class_id=other.id, agent_name="new", workspace_path=str(workspace)))
    db.commit()
    async def verify(_plan):
        pass
    monkeypatch.setattr(deletion_runtime, "verify", verify)
    response = client.post(f"/api/v1/admin/deletions/{job.id}/retry")
    assert response.status_code == 409, response.text
    assert marker.read_text() == "adopted"


@pytest.mark.parametrize("replacement", ["workspace", "agentDir"])
def test_file_verification_refuses_gateway_resources_reassigned_to_another_agent(monkeypatch, replacement):
    plan = {"class_id": "gone", "agent_id": "gone-agent", "workspace": str(settings.openclaw_class_workspace_root / "gone"),
            "channel": settings.openclaw_wechat_channel, "account_ids": []}
    other = {"id": "new-owner", replacement: plan["workspace"] if replacement == "workspace"
             else str(settings.openclaw_state_dir / "agents" / "gone-agent" / "agent")}
    async def rpc(method, params=None):
        return {"config": {"agents": {"list": [other]}}}
    monkeypatch.setattr("app.services.openclaw_provisioning.admin_rpc", rpc)
    with pytest.raises(AppError) as caught:
        asyncio.run(deletion_runtime.verify(plan))
    assert caught.value.code == "DELETION_RESOURCE_SHARED"


def test_file_verification_detects_channel_account_patch_that_did_not_apply(monkeypatch):
    plan = {"class_id": "gone", "agent_id": "gone-agent", "workspace": str(settings.openclaw_class_workspace_root / "gone"),
            "channel": settings.openclaw_wechat_channel, "account_ids": ["acc"]}
    async def rpc(method, params=None):
        return {"config": {"channels": {plan["channel"]: {"accounts": {"acc": {}}}}}}
    async def no_wait(_seconds):
        pass
    monkeypatch.setattr("app.services.openclaw_provisioning.admin_rpc", rpc)
    monkeypatch.setattr(deletion_runtime.asyncio, "sleep", no_wait)
    with pytest.raises(AppError) as caught:
        asyncio.run(deletion_runtime.verify(plan))
    assert caught.value.code == "DELETION_CONFIG_INCOMPLETE"


def test_upload_owns_its_class_before_any_analysis_and_deletion_removes_file(client, db, sample):
    cls, _, _ = sample
    result = client.post("/api/v1/attachments", data={"class_id": cls.id}, files={"file": ("note.txt", b"class file")})
    assert result.status_code == 201, result.text
    attachment_id = result.json()["data"]["id"]
    row = db.get(Attachment, attachment_id)
    path = settings.attachment_dir.parent / row.stored_path
    assert path.exists()
    assert db.scalar(select(AttachmentLink).where(AttachmentLink.attachment_id == attachment_id, AttachmentLink.entity_id == cls.id))
    _pending_job(db, cls)
    deleted = client.delete(f"/api/v1/classes/{cls.id}")
    assert deleted.status_code == 200, deleted.text
    assert not path.exists()


def test_upload_database_failure_does_not_leave_a_disk_orphan(db, sample, monkeypatch):
    from io import BytesIO

    from fastapi import UploadFile

    from app.services import operations
    cls, _, _ = sample
    def fail():
        raise RuntimeError("simulated disk full during db commit")
    monkeypatch.setattr(db, "commit", fail)
    with pytest.raises(RuntimeError):
        operations.save_attachment(db, UploadFile(file=BytesIO(b"payload"), filename="a.txt"), None, None, class_id=cls.id)
    assert not list(settings.attachment_dir.rglob("*.txt"))
    assert not list(db.scalars(select(Attachment)))
    assert not list(db.scalars(select(AttachmentLink)))


def test_gateway_retry_replans_newly_discovered_accounts_before_logout(client, db, sample, monkeypatch):
    from app.services import deletions
    cls, _, _ = sample
    job = _pending_job(db, cls, phase="gateway")
    plan = deletions.class_plan(db, cls.id)
    job.resources_json = {"runtime": plan}
    db.commit()
    async def cancelled(_plan):
        return {**_plan, "account_ids": ["just-saved-im-bot"]}
    async def preflight(_plan):
        return _plan
    async def cleanup(_plan):
        # Cleanup must already have a durable record of every credential target.
        assert db.get(DeletionOperation, job.id).resources_json["runtime"]["account_ids"] == ["just-saved-im-bot"]
    async def verify(_plan):
        pass
    monkeypatch.setattr(deletion_runtime, "stop_login", cancelled)
    monkeypatch.setattr(deletion_runtime, "preflight", preflight)
    monkeypatch.setattr(deletion_runtime, "cleanup", cleanup)
    monkeypatch.setattr(deletion_runtime, "verify", verify)
    result = client.post(f"/api/v1/admin/deletions/{job.id}/retry")
    assert result.status_code == 200, result.text


def test_alias_wechat_route_is_also_a_shared_resource():
    plan = {"class_id": "class-a", "agent_id": "agent-a", "workspace": str(settings.openclaw_class_workspace_root / "a"),
            "channel": settings.openclaw_wechat_channel, "account_ids": ["abc-im-bot"]}
    config = {"bindings": [{"agentId": "agent-b", "match": {"channel": plan["channel"], "accountId": "abc@im.bot"}}]}
    with pytest.raises(AppError) as caught:
        deletion_runtime.validate_config(plan, config)
    assert caught.value.code == "DELETION_RESOURCE_SHARED"


def test_attachment_unlink_failure_is_durable_and_retryable(client, db, sample, monkeypatch):
    from pathlib import Path
    cls, _, _ = sample
    upload = client.post("/api/v1/attachments", data={"class_id": cls.id}, files={"file": ("note.txt", b"keep until retry")})
    path = settings.attachment_dir.parent / upload.json()["data"]["stored_path"]
    job = _pending_job(db, cls)
    class_id, job_id = cls.id, job.id
    real_unlink = Path.unlink
    def denied(target, *args, **kwargs):
        if target == path:
            raise PermissionError("simulated permission failure")
        return real_unlink(target, *args, **kwargs)
    monkeypatch.setattr(Path, "unlink", denied)
    failed = client.delete(f"/api/v1/classes/{class_id}")
    assert failed.status_code == 503, failed.text
    assert failed.json()["error"]["details"]["deleted"] is True
    assert db.get(ClassRoom, class_id) is None
    job = db.get(DeletionOperation, job_id)
    assert (job.status, job.phase) == ("failed", "files")
    assert job.resources_json["attachment_paths"] == [str(path.relative_to(settings.attachment_dir.parent))]
    assert path.exists()
    monkeypatch.setattr(Path, "unlink", real_unlink)
    response = client.post(f"/api/v1/admin/deletions/{job_id}/retry")
    assert response.status_code == 200, response.text
    assert not path.exists()


def test_admin_cannot_reassign_a_class_while_its_deletion_is_pending(client, db, sample):
    cls, _, _ = sample
    _pending_job(db, cls)
    response = client.patch(f"/api/v1/admin/classes/{cls.id}/owner", json={"owner_user_id": None})
    assert response.status_code == 409, response.text
    assert response.json()["error"]["code"] == "DELETION_IN_PROGRESS"


def test_preflight_refuses_a_missing_config_document_even_with_a_hash(monkeypatch):
    plan = {"class_id": "class-a", "agent_id": "agent-a", "workspace": str(settings.openclaw_class_workspace_root / "a"),
            "channel": settings.openclaw_wechat_channel, "account_ids": []}
    async def rpc(_method, _params=None):
        return {"hash": "hash", "config": None}
    monkeypatch.setattr("app.services.openclaw_provisioning.admin_rpc", rpc)
    with pytest.raises(AppError) as caught:
        asyncio.run(deletion_runtime.preflight(plan))
    assert caught.value.code == "DELETION_CONFIG_INVALID"
