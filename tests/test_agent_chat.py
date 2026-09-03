from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace
from typing import ClassVar

from app.config import settings
from app.models.entities import Attachment, AttachmentLink, ClassAgentBinding
from app.services import openclaw_bridge, openclaw_provisioning, operations


def _binding(db, class_id: str, workspace: Path) -> ClassAgentBinding:
    value = ClassAgentBinding(
        class_id=class_id,
        openclaw_agent_id="class-agent-web",
        agent_name="ClassClaw 助理",
        workspace_path=str(workspace),
        status="agent_created",
    )
    db.add(value)
    db.commit()
    return value


def test_web_chat_saves_file_and_targets_class_agent(client, db, sample, tmp_path, monkeypatch):
    cls = sample[0]
    binding = _binding(db, cls.id, tmp_path / "workspace")

    async def ensure_runtime(*_args, **_kwargs):
        return binding

    monkeypatch.setattr(openclaw_provisioning, "ensure_class_agent_runtime", ensure_runtime)
    monkeypatch.setattr(
        operations,
        "settings",
        SimpleNamespace(attachment_dir=tmp_path / "attachments", max_attachment_bytes=20 * 1024 * 1024),
    )
    captured = {}

    async def chat(**kwargs):
        captured.update(kwargs)
        return {"reply": "已读取文件。是否按预览内容保存？", "response_id": "resp-web-1"}

    monkeypatch.setattr(openclaw_bridge, "chat_with_class_agent", chat)
    conversation_id = "97d646a7-153d-4370-aa17-bc49d3ba5593"
    response = client.post(
        f"/api/v1/classes/{cls.id}/agent-chat/messages",
        data={"conversation_id": conversation_id, "text": "请整理这份名单"},
        files={"files": ("名单.csv", "学号,姓名\n001,张三", "text/csv")},
    )

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["reply"] == "已读取文件。是否按预览内容保存？"
    assert data["conversation_id"] == conversation_id
    assert data["attachments"] == [{"name": "名单.csv", "mime_type": "text/csv", "file_size": 24}]
    assert captured["agent_id"] == "class-agent-web"
    assert captured["class_id"] == cls.id
    assert captured["conversation_id"] == conversation_id
    assert captured["text"] == "请整理这份名单"
    assert len(captured["attachments"]) == 1
    assert captured["cancelled"] is not None
    link = db.query(AttachmentLink).filter_by(attachment_id=captured["attachments"][0].id).one()
    assert link.entity_type == "class" and link.entity_id == cls.id


def test_web_chat_rejects_empty_message_and_unprovisioned_agent(client, db, sample, tmp_path):
    cls = sample[0]
    binding = _binding(db, cls.id, tmp_path / "workspace")
    empty = client.post(
        f"/api/v1/classes/{cls.id}/agent-chat/messages",
        data={"conversation_id": "97d646a7-153d-4370-aa17-bc49d3ba5593", "text": "  "},
    )
    assert empty.status_code == 422

    binding.openclaw_agent_id = None
    db.commit()
    missing = client.post(
        f"/api/v1/classes/{cls.id}/agent-chat/messages",
        data={"conversation_id": "97d646a7-153d-4370-aa17-bc49d3ba5593", "text": "你好"},
    )
    assert missing.status_code == 409
    assert missing.json()["error"]["code"] == "OPENCLAW_AGENT_REQUIRED"


def test_web_chat_cannot_target_another_teachers_class(client, db, sample):
    owned, other, _ = sample
    admin_headers = {"Authorization": f"Bearer {settings.api_token}", "X-ClassClaw-Surface": "web"}
    user = client.post(
        "/api/v1/admin/users",
        json={"username": "chatteacher"},
        headers=admin_headers,
    ).json()["data"]
    owned.owner_user_id = user["id"]
    db.commit()
    token = client.post("/api/v1/auth/login", json={"username": "chatteacher", "password": "32767"}).json()["data"]["access_token"]

    response = client.post(
        f"/api/v1/classes/{other.id}/agent-chat/messages",
        headers={"Authorization": f"Bearer {token}", "X-ClassClaw-Surface": "web"},
        data={"conversation_id": "97d646a7-153d-4370-aa17-bc49d3ba5593", "text": "查询其他班"},
    )

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "CLASS_ACCESS_DENIED"


def test_gateway_chat_uses_persistent_class_session_and_prestaged_files(db, tmp_path, monkeypatch):
    attachment_root = tmp_path / "attachments"
    attachment_root.mkdir()
    stored = attachment_root / "note.txt"
    stored.write_text("明天交回执", encoding="utf-8")
    attachment = Attachment(
        id="attachment-web-1",
        original_name="通知.txt",
        stored_name="note.txt",
        stored_path="attachments/note.txt",
        mime_type="text/plain",
        file_size=stored.stat().st_size,
        sha256="0" * 64,
    )
    captured = {}

    class Response:
        status_code = 200
        headers: ClassVar = {"content-type": "application/json"}

        @staticmethod
        def json():
            return {
                "id": "resp-1",
                "model": "openclaw/class-agent-web",
                "output": [{"content": [{"type": "output_text", "text": "我已看到通知，请确认是否登记。"}]}],
            }

    class Client:
        async def post(self, url, **kwargs):
            captured.update({"url": url, **kwargs})
            return Response()

    async def connected(force: bool = False):
        return {"gateway_live": True, "plugin_ready": True}

    monkeypatch.setattr(openclaw_bridge, "connection_status", connected)
    monkeypatch.setattr(openclaw_bridge, "get_http_client", lambda: Client())
    monkeypatch.setattr(openclaw_bridge, "record_openclaw_usage", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        openclaw_bridge,
        "settings",
        SimpleNamespace(
            openclaw_gateway_url="http://gateway.test",
            openclaw_gateway_token="secret",
            openclaw_timeout_seconds=120,
            attachment_dir=attachment_root,
            max_attachment_bytes=20 * 1024 * 1024,
        ),
    )
    result = asyncio.run(
        openclaw_bridge.chat_with_class_agent(
            db=db,
            agent_id="class-agent-web",
            class_id="class-1",
            conversation_id="97d646a7-153d-4370-aa17-bc49d3ba5593",
            message_id="message-1",
            sender_id="teacher-1",
            requested_by="teacher",
            text="请处理通知",
            attachments=[attachment],
        )
    )

    assert result == {"reply": "我已看到通知，请确认是否登记。", "response_id": "resp-1"}
    assert captured["json"]["model"] == "openclaw/class-agent-web"
    assert captured["json"]["user"].endswith(":97d646a7-153d-4370-aa17-bc49d3ba5593")
    assert captured["json"]["input"][0]["content"][1]["type"] == "input_file"
    assert "attachment-web-1" in captured["json"]["instructions"]
    assert "不要再次调用 classclaw_upload_file" in captured["json"]["instructions"]
    assert captured["headers"]["x-openclaw-message-channel"] == "web"


def test_agent_chat_page_has_chatbot_voice_file_and_cancel_controls(client):
    app = client.get("/app/app.js").text
    page = client.get("/app/js/pages/agent.js").text
    styles = client.get("/app/styles.css").text

    assert "班级 Agent 对话" in app
    assert "SpeechRecognition" in page and "webkitSpeechRecognition" in page
    assert "FormData" in page and 'body.append("files", file)' in page
    assert "createAiTaskId" in page and "activeController?.abort()" in page
    assert "新对话" in page and "Shift+Enter 换行" in page
    assert ".agent-chat-panel" in styles and ".agent-chat-composer" in styles
