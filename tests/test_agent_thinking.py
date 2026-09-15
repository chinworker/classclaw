from __future__ import annotations

import asyncio
from dataclasses import replace

import pytest

from app.config import settings
from app.core.errors import AppError
from app.models.entities import ClassAgentBinding
from app.services import agent_thinking, openclaw_bridge, openclaw_provisioning
from tests.helpers import teacher_for_class


@pytest.fixture
def thinking_gateway(monkeypatch):
    agent = {"id": "class-agent", "model": {"primary": "kimi/kimi-for-coding"},
             "thinkingLevels": [{"id": "off", "label": "off"}, {"id": "low", "label": "on"}], "thinkingDefault": "off"}
    calls = []

    async def rpc(method, params=None):
        calls.append((method, params))
        assert method == "agents.list"
        return {"agents": [{"id": "foreign-agent", "workspace": "private-path"}, agent]}

    monkeypatch.setattr(openclaw_provisioning, "admin_rpc", rpc)
    monkeypatch.setattr(agent_thinking, "settings", replace(settings, openclaw_class_agent_thinking="off"))
    return agent, calls


def test_thinking_options_expose_only_bound_model_capabilities(client, db, sample, tmp_path, thinking_gateway):
    cls = sample[0]
    db.add(ClassAgentBinding(class_id=cls.id, openclaw_agent_id="class-agent", agent_name="测试助手", workspace_path=str(tmp_path)))
    db.commit()
    response = client.get(f"/api/v1/classes/{cls.id}/agent-chat/thinking")
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    assert response.json()["data"] == {
        "agent_id": "class-agent", "model": "kimi/kimi-for-coding",
        "levels": [{"id": "off", "label": "关闭"}, {"id": "low", "label": "开启"}],
        "default_level": "off", "configured_default_level": "off", "default_adjusted": False,
    }
    assert "foreign-agent" not in response.text and "private-path" not in response.text
    assert thinking_gateway[1] == [("agents.list", None)]


@pytest.mark.parametrize("levels", [
    [{"id": "off", "label": "off"}],
    [{"id": "low", "label": "low"}, {"id": "high", "label": "high"}, {"id": "xhigh", "label": "xhigh"}],
    [{"id": "adaptive", "label": "adaptive"}, {"id": "max", "label": "max"}],
    [{"id": "low", "label": "节能思考"}, {"id": "future-level", "label": "新能力"}],
])
def test_profiles_come_from_gateway_not_model_name_or_fixed_matrix(thinking_gateway, levels):
    agent, _ = thinking_gateway
    agent["model"] = {"primary": "custom/same-name"}
    agent["thinkingLevels"] = levels
    profile = asyncio.run(agent_thinking.agent_thinking_options("class-agent"))
    assert [item["id"] for item in profile["levels"]] == [item["id"] for item in levels if item["id"] != "future-level"]
    assert profile["model"] == "custom/same-name"


def test_default_adapts_only_when_configured_level_is_unsupported(thinking_gateway, monkeypatch):
    agent, _ = thinking_gateway
    monkeypatch.setattr(agent_thinking, "settings", replace(settings, openclaw_class_agent_thinking="high"))
    profile = asyncio.run(agent_thinking.agent_thinking_options("class-agent"))
    assert profile["default_level"] == "off" and profile["default_adjusted"]
    assert asyncio.run(agent_thinking.resolve_thinking_level("class-agent", None)) == "off"
    assert asyncio.run(agent_thinking.resolve_thinking_level("class-agent", "low")) == "low"
    agent["thinkingLevels"].append({"id": "high", "label": "high"})
    profile = asyncio.run(agent_thinking.agent_thinking_options("class-agent"))
    assert profile["default_level"] == "high" and not profile["default_adjusted"]


def test_invalid_explicit_choice_never_silently_changes_level(thinking_gateway):
    with pytest.raises(AppError) as caught:
        asyncio.run(agent_thinking.resolve_thinking_level("class-agent", "high"))
    assert caught.value.code == "CHAT_THINKING_UNSUPPORTED"
    assert caught.value.status_code == 422
    assert caught.value.details["supported_levels"] == ["off", "low"]
    assert "关闭、开启" in caught.value.message


def test_unknown_default_requires_supported_explicit_choice(thinking_gateway, monkeypatch):
    agent, _ = thinking_gateway
    monkeypatch.setattr(agent_thinking, "settings", replace(settings, openclaw_class_agent_thinking="high"))
    agent["thinkingDefault"] = "unsupported"
    assert asyncio.run(agent_thinking.agent_thinking_options("class-agent"))["default_level"] is None
    with pytest.raises(AppError, match="配置默认"):
        asyncio.run(agent_thinking.resolve_thinking_level("class-agent", None))
    assert asyncio.run(agent_thinking.resolve_thinking_level("class-agent", "off")) == "off"


@pytest.mark.parametrize("levels", [None, [], 42, [{"id": "future-level", "label": "unknown"}], [None, {"id": []}]])
def test_missing_metadata_never_invents_supported_levels(thinking_gateway, levels):
    thinking_gateway[0]["thinkingLevels"] = levels
    with pytest.raises(AppError) as caught:
        asyncio.run(agent_thinking.agent_thinking_options("class-agent"))
    assert caught.value.code == "CHAT_THINKING_OPTIONS_UNAVAILABLE"


def test_thinking_options_require_auth_and_class_ownership(client, db, sample, thinking_gateway):
    cls, other, _ = sample
    path = f"/api/v1/classes/{cls.id}/agent-chat/thinking"
    assert client.get(path, headers={"Authorization": ""}).status_code == 401
    headers = teacher_for_class(client, cls, db, "thinking-teacher")
    denied = client.get(f"/api/v1/classes/{other.id}/agent-chat/thinking", headers=headers)
    assert denied.status_code == 403
    assert thinking_gateway[1] == []


def test_chat_rechecks_model_before_patching_session_or_calling_model(db, thinking_gateway, monkeypatch):
    async def connected():
        return {"gateway_live": True, "plugin_ready": True}

    async def no_patch_or_model(*args, **kwargs):
        pytest.fail("Unsupported choice must be rejected before session changes or model calls")

    monkeypatch.setattr(openclaw_bridge, "connection_status", connected)
    monkeypatch.setattr(openclaw_provisioning, "set_web_session_thinking", no_patch_or_model)
    monkeypatch.setattr(openclaw_bridge, "_request_responses", no_patch_or_model)
    with pytest.raises(AppError) as caught:
        asyncio.run(openclaw_bridge.chat_with_class_agent(
            db=db, agent_id="class-agent", class_id="class-a", conversation_id="conversation-a", message_id="message-a",
            sender_id="teacher-a", requested_by="teacher-a", text="测试", attachments=[], thinking_level="high",
        ))
    assert caught.value.code == "CHAT_THINKING_UNSUPPORTED"


def test_capabilities_timeout_cancels_lookup_instead_of_delaying_model_request(monkeypatch):
    cancelled = []

    async def slow_rpc(*args):
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.append(True)

    monkeypatch.setattr(openclaw_provisioning, "admin_rpc", slow_rpc)
    monkeypatch.setattr(agent_thinking, "_OPTIONS_TIMEOUT_SECONDS", 0.01)
    with pytest.raises(AppError) as caught:
        asyncio.run(agent_thinking.resolve_thinking_level("class-agent", "low"))
    assert caught.value.code == "CHAT_THINKING_OPTIONS_UNAVAILABLE"
    assert cancelled == [True]
