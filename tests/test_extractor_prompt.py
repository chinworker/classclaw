from __future__ import annotations

import asyncio
from dataclasses import replace

from app.config import settings
from app.services import openclaw_bridge as bridge


def test_extractor_enabled_by_default():
    assert settings.openclaw_extractor_enabled is True
    assert settings.openclaw_extractor_agent_id == "classclaw-extractor"


def test_full_prompt_used_when_extractor_switch_off(monkeypatch):
    monkeypatch.setattr(bridge, "settings", replace(settings, openclaw_extractor_enabled=False))
    prompt = asyncio.run(bridge._build_interaction_prompt("wechat", "张三迟到", {"classes": []}))
    assert "安全与质量要求" in prompt
    assert "张三迟到" in prompt
    assert "student.create" in prompt


def test_slim_prompt_used_when_extractor_ready(monkeypatch):
    monkeypatch.setattr(bridge, "settings", replace(settings, openclaw_extractor_agent_id="classclaw-extractor"))

    async def ready() -> bool:
        return True

    monkeypatch.setattr(bridge, "ensure_extractor_agent", ready)
    prompt = asyncio.run(bridge._build_interaction_prompt("wechat", "张三迟到", {"classes": []}))
    assert "安全与质量要求" not in prompt
    assert "张三迟到" in prompt
    assert "ClassClaw 输入清洗规范" in prompt


def test_responses_json_targets_extractor_when_ready(monkeypatch):
    captured: dict = {}

    async def fake_post(url, **kwargs):
        captured["url"] = url
        captured["json"] = kwargs.get("json") or (kwargs[1] if len(kwargs) > 1 else None)

        class Response:
            status_code = 200
            headers = {"content-type": "application/json"}

            def json(self):
                return {"output_text": '{"status":"no_action","operations":[]}'}

        return Response()

    async def connected(force: bool = False):
        return {"gateway_live": True, "plugin_ready": True}

    monkeypatch.setattr(bridge, "connection_status", connected)
    monkeypatch.setattr(bridge, "settings", replace(settings, openclaw_extractor_agent_id="classclaw-extractor"))

    async def ready() -> bool:
        return True

    monkeypatch.setattr(bridge, "ensure_extractor_agent", ready)

    class FakeClient:
        async def post(self, url, **kwargs):
            return await fake_post(url, **kwargs)

    monkeypatch.setattr(bridge, "get_http_client", lambda: FakeClient())
    result = asyncio.run(bridge._responses_json("测试", user="test"))
    assert result["status"] == "no_action"
    body = captured["json"]
    assert body["model"] == "openclaw/classclaw-extractor"


def test_extractor_agents_md_contains_full_rules():
    md = bridge._extractor_agents_md()
    assert "安全与质量要求" in md
    assert "student.create" in md
    assert "operations" in md
