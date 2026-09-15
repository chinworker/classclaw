from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace
from typing import ClassVar

from app.config import settings
from app.models.entities import Attachment, AttachmentLink
from app.services import agent_models, agent_thinking, openclaw_bridge, openclaw_provisioning, operations
from tests.helpers import make_binding, teacher_for_class


def test_web_chat_saves_file_and_targets_class_agent(client, db, sample, tmp_path, monkeypatch):
    cls = sample[0]
    binding = make_binding(db, cls.id, tmp_path / "workspace", openclaw_agent_id="class-agent-web", agent_name="ClassClaw 助理")

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
    binding = make_binding(db, cls.id, tmp_path / "workspace", openclaw_agent_id="class-agent-web", agent_name="ClassClaw 助理")
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
    headers = teacher_for_class(client, owned, db, "chatteacher", admin_headers=admin_headers)

    response = client.post(
        f"/api/v1/classes/{other.id}/agent-chat/messages",
        headers={"Authorization": f"Bearer {headers['Authorization'].split()[-1]}", "X-ClassClaw-Surface": "web"},
        data={"conversation_id": "97d646a7-153d-4370-aa17-bc49d3ba5593", "text": "查询其他班"},
    )

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "CLASS_ACCESS_DENIED"

    models = client.get(
        f"/api/v1/classes/{other.id}/agent-chat/models",
        headers={"Authorization": f"Bearer {headers['Authorization'].split()[-1]}", "X-ClassClaw-Surface": "web"},
    )
    assert models.status_code == 403
    assert models.json()["error"]["code"] == "CLASS_ACCESS_DENIED"


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

    async def thinking(key, level):
        captured["thinking"] = {"key": key, "level": level}

    async def thinking_options(agent_id):
        assert agent_id == "class-agent-web"
        return {"levels": [{"id": "high", "label": "高"}], "default_level": "high"}

    monkeypatch.setattr(openclaw_bridge, "connection_status", connected)
    monkeypatch.setattr(agent_thinking, "agent_thinking_options", thinking_options)
    monkeypatch.setattr(openclaw_provisioning, "set_web_session_thinking", thinking)
    monkeypatch.setattr(openclaw_bridge, "get_http_client", lambda: Client())
    monkeypatch.setattr(openclaw_bridge, "record_openclaw_usage", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        openclaw_bridge,
        "settings",
        SimpleNamespace(
            openclaw_gateway_url="http://gateway.test",
            openclaw_gateway_token="secret",
            openclaw_timeout_seconds=120,
            openclaw_class_agent_thinking="high",
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
            model_override="provider/vision-model",
        )
    )

    assert result == {"reply": "我已看到通知，请确认是否登记。", "response_id": "resp-1"}
    assert captured["json"]["model"] == "openclaw/class-agent-web"
    assert captured["json"]["user"].endswith(":97d646a7-153d-4370-aa17-bc49d3ba5593")
    assert captured["json"]["input"][0]["content"][1]["type"] == "input_file"
    assert "attachment-web-1" in captured["json"]["instructions"]
    assert "不要再次调用 classclaw_upload_file" in captured["json"]["instructions"]
    assert captured["headers"]["x-openclaw-message-channel"] == "web"
    assert captured["headers"]["x-openclaw-model"] == "provider/vision-model"
    assert captured["thinking"]["level"] == "high"
    assert captured["headers"]["x-openclaw-session-key"] == captured["thinking"]["key"]


def test_class_agent_models_are_scoped_validated_and_persisted(client, db, sample, tmp_path, monkeypatch):
    cls = sample[0]
    binding = make_binding(db, cls.id, tmp_path / "workspace", openclaw_agent_id="class-agent-web", agent_name="ClassClaw 助理")
    patches = []
    runtime_rows = [{"id": binding.openclaw_agent_id, "workspace": binding.workspace_path}]

    async def fake_rpc(method, params=None):
        if method == "config.get":
            return {
                "hash": "models-hash",
                "config": {"agents": {"defaults": {"model": {"primary": "provider/text-model"}}, "list": runtime_rows}},
            }
        if method == "models.list":
            return {
                "models": [
                    {"id": "text-model", "name": "Text", "provider": "provider", "input": ["text"], "available": True},
                    {"id": "vision-model", "name": "Vision", "provider": "provider", "input": ["text", "image"], "available": True},
                ]
            }
        if method == "models.authStatus":
            return {"providers": [{"provider": "openai", "status": "static"}]}
        if method == "config.patch":
            patches.append(params)
            return {"ok": True}
        raise AssertionError(method)

    monkeypatch.setattr(openclaw_provisioning, "admin_rpc", fake_rpc)

    settings_response = client.get(f"/api/v1/classes/{cls.id}/agent-chat/models")
    assert settings_response.status_code == 200
    settings = settings_response.json()["data"]
    assert [item["id"] for item in settings["models"]] == ["provider/text-model", "provider/vision-model"]
    assert [item["id"] for item in settings["image_models"]] == ["provider/vision-model"]

    update = client.patch(
        f"/api/v1/classes/{cls.id}/agent-chat/models",
        json={
            "main_model": "provider/text-model",
            "image_model": "provider/vision-model",
            "speech_model": "openai/gpt-4o-transcribe",
        },
    )
    assert update.status_code == 200
    assert update.json()["data"]["restart_requested"] is True
    db.refresh(binding)
    assert (binding.main_model, binding.image_model, binding.speech_model) == (
        "provider/text-model",
        "provider/vision-model",
        "openai/gpt-4o-transcribe",
    )
    patched = json.loads(patches[-1]["raw"])
    assert patched["agents"]["list"][0]["model"] == "provider/text-model"

    invalid = client.patch(
        f"/api/v1/classes/{cls.id}/agent-chat/models",
        json={"image_model": "provider/text-model"},
    )
    assert invalid.status_code == 422
    assert invalid.json()["error"]["code"] == "MODEL_UNAVAILABLE"


def test_selected_image_model_overrides_only_image_chat_turn(client, db, sample, tmp_path, monkeypatch):
    cls = sample[0]
    binding = make_binding(db, cls.id, tmp_path / "workspace", openclaw_agent_id="class-agent-web", agent_name="ClassClaw 助理")
    binding.image_model = "provider/vision-model"
    db.commit()

    async def ensure_runtime(*_args, **_kwargs):
        return binding

    captured = {}

    async def chat(**kwargs):
        captured.update(kwargs)
        return {"reply": "图片已读取", "response_id": "resp-image"}

    monkeypatch.setattr(openclaw_provisioning, "ensure_class_agent_runtime", ensure_runtime)
    monkeypatch.setattr(openclaw_bridge, "chat_with_class_agent", chat)
    monkeypatch.setattr(
        operations,
        "settings",
        SimpleNamespace(attachment_dir=tmp_path / "attachments", max_attachment_bytes=20 * 1024 * 1024),
    )
    response = client.post(
        f"/api/v1/classes/{cls.id}/agent-chat/messages",
        data={"conversation_id": "97d646a7-153d-4370-aa17-bc49d3ba5593"},
        files={"files": ("photo.png", b"not-a-real-image", "image/png")},
    )
    assert response.status_code == 200
    assert captured["model_override"] == "provider/vision-model"


def test_model_voice_transcription_uses_temporary_file_and_selected_model(client, db, sample, tmp_path, monkeypatch):
    cls = sample[0]
    binding = make_binding(db, cls.id, tmp_path / "workspace", openclaw_agent_id="class-agent-web", agent_name="ClassClaw 助理")
    binding.speech_model = "openai/gpt-4o-transcribe"
    db.commit()
    captured = {}

    async def fake_command(arguments, **_kwargs):
        path = Path(arguments[arguments.index("--file") + 1])
        captured["path"] = path
        captured["arguments"] = arguments
        assert path.exists()
        return {"text": "明天第一节课改为数学"}

    monkeypatch.setattr(agent_models, "_run_json_command", fake_command)
    response = client.post(
        f"/api/v1/classes/{cls.id}/agent-chat/transcriptions",
        files={"audio": ("voice.webm", b"a" * 2048, "audio/webm")},
    )
    assert response.status_code == 200
    assert response.json()["data"] == {"text": "明天第一节课改为数学", "model": "openai/gpt-4o-transcribe"}
    assert captured["arguments"][captured["arguments"].index("--model") + 1] == binding.speech_model
    assert not captured["path"].exists()


def test_agent_chat_page_has_chatbot_voice_file_and_cancel_controls(client):
    app = client.get("/app/app.js").text
    page = client.get("/app/js/pages/agent.js").text
    account = client.get("/app/js/pages/account.js").text
    store = client.get("/app/js/agentChatStore.js").text
    styles = client.get("/app/styles.css").text

    assert 'label: "班级 Agent"' in app and 'title: "班级 Agent"' in app
    assert "班级 Agent 对话" not in app
    assert "SpeechRecognition" in page and "webkitSpeechRecognition" in page
    assert "MediaRecorder" in page and "agent-chat/transcriptions" in page
    assert "FormData" in store and 'body.append("files", file)' in store
    assert "createAiTaskId" in store and "activeController?.abort()" in store
    assert "agent-conversation-list" in page and "agentChatStore.stopMessage(conversation)" in page
    assert 'addEventListener("hashchange"' not in page
    assert "新对话" in page and "Shift+Enter 换行" in page
    assert "agentModelSettings" not in page and "qrBindingPanel" not in page
    assert 'href: "#/account"' in page
    assert "模型设置" in account and "agentModelSettings" in account and "qrBindingPanel" in account
    assert ".agent-chat-panel" in styles and ".agent-chat-composer" in styles
