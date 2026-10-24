from __future__ import annotations

import asyncio
import json
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
        "class_default_level": None, "system_default_level": "off",
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


@pytest.fixture
def class_thinking_runtime(db, sample, monkeypatch, tmp_path):
    binding = ClassAgentBinding(class_id=sample[0].id, openclaw_agent_id="class-agent", agent_name="测试助手", workspace_path=str(tmp_path))
    db.add(binding)
    db.commit()
    agent = {"id": "class-agent", "model": "provider/model", "thinkingDefault": "off"}
    other = {"id": "other-agent", "thinkingDefault": "high"}
    config = {"agents": {"list": [agent, other]}, "bindings": [{"agentId": "class-agent", "match": {"accountId": "wechat-a"}}]}
    calls = []

    async def rpc(method, params=None):
        calls.append((method, params))
        if method == "config.get":
            return {"hash": "current-hash", "config": config}
        if method == "agents.list":
            return {"agents": [{**config["agents"]["list"][0], "thinkingLevels": [
                {"id": "off", "label": "off"}, {"id": "low", "label": "on"},
            ]}]}
        if method == "models.list":
            return {"models": [{"id": "model", "provider": "provider", "input": ["text", "image"]},
                               {"id": "new", "provider": "provider", "input": ["text"]}]}
        if method == "models.authStatus":
            return {"providers": []}
        if method == "config.patch":
            assert params["baseHash"] == "current-hash"
            patch = json.loads(params["raw"])
            assert set(patch) <= {"agents", "plugins"}
            config["agents"]["list"] = patch["agents"]["list"]
            return {}
        pytest.fail(method)

    monkeypatch.setattr(openclaw_provisioning, "admin_rpc", rpc)
    return binding, config, calls


def test_class_default_save_reset_and_other_agents_are_preserved(client, db, sample, class_thinking_runtime):
    binding, config, calls = class_thinking_runtime
    path = f"/api/v1/classes/{sample[0].id}/agent-chat/thinking"
    saved = client.patch(path, json={"thinking_default": "low"})
    assert saved.status_code == 200, saved.text
    assert binding.thinking_default == "low"
    assert saved.json()["data"]["restart_requested"] is True
    assert config["agents"]["list"] == [
        {"id": "class-agent", "model": "provider/model", "thinkingDefault": "low"},
        {"id": "other-agent", "thinkingDefault": "high"},
    ]
    assert config["bindings"] == [{"agentId": "class-agent", "match": {"accountId": "wechat-a"}}]
    profile = client.get(path).json()["data"]
    assert profile["default_level"] == profile["class_default_level"] == "low"
    assert profile["system_default_level"] == "off"
    assert profile["levels"][1] == {"id": "low", "label": "开启"}
    patches = sum(method == "config.patch" for method, _ in calls)
    assert client.patch(path, json={"thinking_default": "low"}).json()["data"]["restart_requested"] is False
    assert sum(method == "config.patch" for method, _ in calls) == patches
    assert client.patch(path, json={"thinking_default": None}).status_code == 200
    assert binding.thinking_default is None
    assert config["agents"]["list"][0]["thinkingDefault"] == "off"


def test_class_thinking_revalidates_capabilities_and_rejects_cross_class_write(client, db, sample, class_thinking_runtime):
    binding, _, calls = class_thinking_runtime
    path = f"/api/v1/classes/{sample[0].id}/agent-chat/thinking"
    rejected = client.patch(path, json={"thinking_default": "high"})
    assert rejected.status_code == 422
    assert rejected.json()["error"]["code"] == "CHAT_THINKING_UNSUPPORTED"
    assert binding.thinking_default is None
    assert not any(method == "config.patch" for method, _ in calls)
    assert client.patch(path, json={"thinking_default": "invalid"}).status_code == 422
    headers = teacher_for_class(client, sample[1], db, "other-thinking-owner")
    assert client.patch(path, json={"thinking_default": "low"}, headers=headers).status_code == 403
    assert client.patch(path, json={"thinking_default": "low"}, headers={"Authorization": ""}).status_code == 401


def test_failed_gateway_save_keeps_the_persisted_class_default(client, sample, class_thinking_runtime, monkeypatch):
    binding, _, _ = class_thinking_runtime
    original = openclaw_provisioning.admin_rpc

    async def fail_patch(method, params=None):
        if method == "config.patch":
            raise AppError("CONFIG_CONFLICT", "配置已变更", 409)
        return await original(method, params)

    monkeypatch.setattr(openclaw_provisioning, "admin_rpc", fail_patch)
    result = client.patch(f"/api/v1/classes/{sample[0].id}/agent-chat/thinking", json={"thinking_default": "low"})
    assert result.status_code == 409
    assert binding.thinking_default is None


def test_class_default_and_session_override_have_separate_priorities(thinking_gateway):
    assert asyncio.run(agent_thinking.resolve_thinking_level("class-agent", None, configured_default="low")) == "low"
    assert asyncio.run(agent_thinking.resolve_thinking_level("class-agent", "off", configured_default="low")) == "off"


def test_startup_sync_keeps_each_class_default(db, sample, monkeypatch, class_thinking_runtime):
    from contextlib import contextmanager

    binding, config, _ = class_thinking_runtime
    binding.thinking_default = "low"
    db.add(ClassAgentBinding(class_id=sample[1].id, openclaw_agent_id="other-agent", agent_name="其他助手", workspace_path="/tmp/other"))
    db.commit()

    @contextmanager
    def reader():
        yield db

    monkeypatch.setattr(openclaw_provisioning, "reader_session", reader)
    asyncio.run(openclaw_provisioning.sync_class_agent_thinking_defaults())
    assert [row["thinkingDefault"] for row in config["agents"]["list"]] == ["low", "off"]


def test_web_chat_uses_class_default_and_explicit_override_without_changing_agent(db, sample, thinking_gateway, monkeypatch):
    binding = ClassAgentBinding(class_id=sample[0].id, openclaw_agent_id="class-agent", agent_name="助手",
                                workspace_path="/tmp/test", thinking_default="low")
    db.add(binding)
    db.commit()
    session_levels = []

    async def connected():
        return {"gateway_live": True, "plugin_ready": True}

    async def patch_session(_key, level):
        session_levels.append(level)

    async def model(*args, **kwargs):
        return {"output_text": "测试回复"}

    monkeypatch.setattr(openclaw_bridge, "connection_status", connected)
    monkeypatch.setattr(openclaw_bridge, "_request_responses", model)
    monkeypatch.setattr(openclaw_bridge, "record_openclaw_usage", lambda *args, **kwargs: None)
    monkeypatch.setattr(openclaw_provisioning, "set_web_session_thinking", patch_session)
    for requested in (None, "off"):
        result = asyncio.run(openclaw_bridge.chat_with_class_agent(
            db=db, agent_id="class-agent", class_id=sample[0].id, conversation_id="conversation", message_id="message",
            sender_id="teacher", requested_by="teacher", text="测试", attachments=[], thinking_level=requested,
        ))
        assert result["reply"] == "测试回复"
    assert session_levels == ["low", "off"]
    assert binding.thinking_default == "low"
    assert all(method == "agents.list" for method, _ in thinking_gateway[1])


def test_model_settings_save_default_thinking_and_image_choice_together(client, sample, class_thinking_runtime):
    binding, config, calls = class_thinking_runtime
    path = f"/api/v1/classes/{sample[0].id}/agent-chat/models"
    response = client.patch(path, json={"thinking_default": "low", "image_model": "provider/model"})
    assert response.status_code == 200, response.text
    assert binding.thinking_default == "low" and binding.image_model == "provider/model"
    assert config["agents"]["list"][0]["thinkingDefault"] == "low"
    assert config["agents"]["list"][0]["model"] == "provider/model"
    assert config["agents"]["list"][1] == {"id": "other-agent", "thinkingDefault": "high"}
    assert sum(method == "config.patch" for method, _ in calls) == 1
    current = client.get(path).json()["data"]
    assert current["configured"]["thinking_default"] == "low"


def test_model_settings_reject_stale_thinking_without_saving_other_fields(client, sample, class_thinking_runtime):
    binding, _, calls = class_thinking_runtime
    response = client.patch(f"/api/v1/classes/{sample[0].id}/agent-chat/models",
                            json={"thinking_default": "high", "image_model": "provider/model"})
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "CHAT_THINKING_UNSUPPORTED"
    assert binding.thinking_default is None and binding.image_model is None
    assert not any(method == "config.patch" for method, _ in calls)


def test_model_settings_require_new_model_capabilities_before_explicit_thinking_choice(client, sample, class_thinking_runtime):
    binding, config, calls = class_thinking_runtime
    path = f"/api/v1/classes/{sample[0].id}/agent-chat/models"
    blocked = client.patch(path, json={"main_model": "provider/new", "thinking_default": "low"})
    assert blocked.status_code == 422
    assert blocked.json()["error"]["code"] == "THINKING_MODEL_CHANGED"
    assert binding.main_model is None and binding.thinking_default is None
    assert not any(method == "config.patch" for method, _ in calls)
    saved = client.patch(path, json={"main_model": "provider/new", "thinking_default": None})
    assert saved.status_code == 200, saved.text
    assert config["agents"]["list"][0]["model"] == "provider/new"
    assert binding.main_model == "provider/new" and binding.thinking_default is None
