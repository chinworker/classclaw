from __future__ import annotations

import asyncio
import json
from dataclasses import replace

from app.config import settings
from app.services import openclaw_bridge as bridge, openclaw_provisioning


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
    request_db = object()

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

    def record_usage(payload, *, user, model, db=None):
        captured["usage_db"] = db

    monkeypatch.setattr(bridge, "record_openclaw_usage", record_usage)
    result = asyncio.run(bridge._responses_json("测试", user="test", db=request_db))
    assert result["status"] == "no_action"
    body = captured["json"]
    assert body["model"] == "openclaw/classclaw-extractor"
    assert captured["usage_db"] is request_db


def test_extractor_agents_md_contains_full_rules():
    md = bridge._extractor_agents_md()
    assert "安全与质量要求" in md
    assert "student.create" in md
    assert "operations" in md


def test_extractor_workspace_keeps_admin_customization(monkeypatch, tmp_path):
    test_settings = replace(settings, openclaw_class_workspace_root=tmp_path, openclaw_extractor_agent_id="classclaw-extractor")
    monkeypatch.setattr(bridge, "settings", test_settings)
    monkeypatch.setattr(bridge, "_extractor_state", None)
    workspace = tmp_path / "_extractor"
    workspace.mkdir()
    (workspace / "AGENTS.md").write_text("# Admin custom\n", encoding="utf-8")
    (workspace / ".classclaw-admin-customized.json").write_text(json.dumps(["AGENTS.md"]), encoding="utf-8")

    async def fake_rpc(method: str, params: dict | None = None):
        assert method == "agents.list"
        return [{"id": "classclaw-extractor"}]

    monkeypatch.setattr(openclaw_provisioning, "admin_rpc", fake_rpc)
    assert asyncio.run(bridge.ensure_extractor_agent()) is True
    assert (workspace / "AGENTS.md").read_text(encoding="utf-8") == "# Admin custom\n"
    assert (workspace / "SOUL.md").is_file()
    assert (workspace / "IDENTITY.md").is_file()
