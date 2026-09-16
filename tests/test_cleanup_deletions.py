from __future__ import annotations

import asyncio
from contextlib import contextmanager
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.config import settings
from app.models.entities import AuditLog, ClassAgentBinding
from scripts import cleanup_deletions as cleanup


def test_broad_unowned_resource_deletion_is_refused():
    for key in ("state_dirs", "attachments"):
        args = SimpleNamespace(state_dirs=False, attachments=False)
        setattr(args, key, True)
        with pytest.raises(RuntimeError):
            cleanup.validate_apply_selection(args)


def test_unknown_file_cleanup_preserves_unrelated_payloads_and_names(tmp_path):
    root = settings.attachment_dir
    root.mkdir()
    matched = root / f"{uuid4()}.csv"
    matched.write_text("学号,姓名\n001,张三\n")
    personal = root / f"{uuid4()}.csv"
    personal.write_text("真实名单")
    named = root / "名单.csv"
    named.write_bytes(matched.read_bytes())
    assert cleanup.confirmed_test_file(matched)
    assert not cleanup.confirmed_test_file(personal)
    assert not cleanup.confirmed_test_file(named)


def test_missing_binding_does_not_make_live_class_mapping_stale(db, sample):
    cls, _other, _ = sample
    config = {"plugins": {"entries": {"classclaw": {"config": {"agentClasses": {"live": cls.id, "unknown": "unknown"}}}}}}
    assert cleanup.stale_mappings(db, config) == {}


def test_maintenance_never_logs_out_an_account_with_a_live_claim(db, sample):
    cls, _, _ = sample
    db.add(ClassAgentBinding(class_id=cls.id, agent_name="live", workspace_path=str(settings.openclaw_class_workspace_root / cls.id),
                            channel_account_id="abc-im-bot"))
    db.commit()
    with pytest.raises(RuntimeError, match="引用"):
        cleanup.check_retired_accounts(db, {}, ["abc-im-bot"])


def test_maintenance_readback_detects_ignored_mapping_tombstone(db, monkeypatch):
    class_id = str(uuid4())
    db.add(AuditLog(operator_type="admin", action="hard_delete", entity_type="class", entity_id=class_id))
    db.commit()
    config = {"plugins": {"entries": {"classclaw": {"config": {"agentClasses": {"old-agent": class_id}}}}}}
    @contextmanager
    def reader():
        yield db
    async def rpc(method, params=None):
        if method == "config.get":
            return {"hash": "hash", "config": config}
        if method == "config.patch":
            return {"ok": True}  # A lying/no-op Gateway must never count as success.
        raise AssertionError(method)
    async def no_wait(_seconds):
        pass
    monkeypatch.setattr(cleanup, "reader_session", reader)
    monkeypatch.setattr(cleanup.openclaw_provisioning, "admin_rpc", rpc)
    monkeypatch.setattr(cleanup.asyncio, "sleep", no_wait)
    with pytest.raises(RuntimeError, match="残留映射"):
        asyncio.run(cleanup.apply_gateway(SimpleNamespace(), {"old-agent": class_id}, []))
