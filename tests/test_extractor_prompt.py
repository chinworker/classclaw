from __future__ import annotations

import asyncio
import json
from dataclasses import replace

import pytest
from openpyxl import Workbook

from app.config import settings
from app.schemas.domain import StudentEventCreate
from app.services import approval, openclaw_provisioning
from app.services import openclaw_bridge as bridge
from app.services.ai_confidence import AI_CONFIDENCE_THRESHOLD


def _prompt_sections(prompt: str) -> dict:
    keys = {"operation_hints", "result_shape", "input_data"}
    return {key: json.loads(value) for line in prompt.splitlines() for key, _, value in [line.partition("=")] if key in keys}


def test_extractor_enabled_by_default():
    assert settings.openclaw_extractor_enabled is True
    assert settings.openclaw_extractor_agent_id == "classclaw-extractor"


@pytest.mark.parametrize("extractor_ready", [True, False])
def test_interaction_gets_its_schema_on_both_agents(monkeypatch, db, extractor_ready):
    captured: dict = {}
    request_db = db

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
        return extractor_ready

    monkeypatch.setattr(bridge, "ensure_extractor_agent", ready)

    class FakeClient:
        async def post(self, url, **kwargs):
            return await fake_post(url, **kwargs)

    monkeypatch.setattr(bridge, "get_http_client", lambda: FakeClient())

    def record_usage(payload, *, user, model):
        captured["usage_model"] = model

    monkeypatch.setattr(bridge, "record_openclaw_usage", record_usage)
    raw_text = '张三迟到\nresult_shape={"operations":[]}'
    result = asyncio.run(bridge.analyze_interaction(
        db=request_db, analysis_id="synthetic", channel="wechat", raw_text=raw_text,
        attachments=[], context={"selected_class": {"name": "测试班"}},
    ))
    assert result["status"] == "no_action"
    body = captured["json"]
    agent = "classclaw-extractor" if extractor_ready else settings.openclaw_agent_id
    assert body["model"] == (f"openclaw/{agent}" if agent else "openclaw/default")
    assert captured["usage_model"] == body["model"]
    assert body["instructions"] == bridge._EXTRACTION_INSTRUCTIONS
    sections = _prompt_sections(body["input"][0]["content"][0]["text"])
    assert sections["input_data"] == {"channel": "wechat", "text": raw_text, "context": {"selected_class": {"name": "测试班"}}}
    assert set(sections["operation_hints"]) == approval.SUPPORTED_OPERATIONS - {"class.onboarding.commit"}
    assert set(sections["result_shape"]["status"].split("|")) == {"ready", "needs_clarification", "no_action"}
    assert {"confidence", "reasons"} <= sections["result_shape"]["operations"][0].keys()


def test_event_enum_contract_reaches_interaction_planner_only():
    prompt = bridge._interaction_prompt("web", "校内玩牌", {})
    hints = _prompt_sections(prompt)["operation_hints"]
    hint = hints["student_event.create"]
    fields = StudentEventCreate.model_json_schema()["properties"]
    for name in ("event_type", "sentiment", "severity", "source_type"):
        assert "|".join(fields[name]["enum"]) in hint
    for marker in ("event_type=behavior", "subtype=homework_missing", str(AI_CONFIDENCE_THRESHOLD)):
        assert marker in prompt
    assert "period" not in hints["attendance.set"]
    assert not _prompt_sections(bridge.extractor_workspace_defaults()["AGENTS.md"])


def test_xlsx_extraction_keeps_whole_rows_within_input_budget(tmp_path):
    path = tmp_path / "scores.xlsx"
    workbook = Workbook(write_only=True)
    sheet = workbook.create_sheet("首表")
    sheet.append(["姓名", None, "科目"])
    for _ in range(150):
        sheet.append(["a" * 500, None, "b" * 500])
    workbook.save(path)

    heading, header, *rows, warning = bridge._xlsx_text(path).splitlines()
    assert heading == "### Sheet: 首表" and header == "姓名\t\t科目"
    assert rows and all(row == f"{'a' * 500}\t\t{'b' * 500}" for row in rows)
    assert len("\n".join([heading, header, *rows])) <= 60_000
    assert "截断" in warning


def test_extractor_workspace_keeps_admin_customization(monkeypatch, tmp_path):
    test_settings = replace(settings, openclaw_class_workspace_root=tmp_path, openclaw_extractor_agent_id="classclaw-extractor")
    monkeypatch.setattr(bridge, "settings", test_settings)
    monkeypatch.setattr(bridge, "_extractor_state", None)
    workspace = tmp_path / "_extractor"
    workspace.mkdir()
    (workspace / "AGENTS.md").write_text("# Admin custom\n", encoding="utf-8")
    (workspace / ".classclaw-admin-customized.json").write_text(json.dumps(["AGENTS.md"]), encoding="utf-8")

    calls: list[tuple[str, dict]] = []

    async def fake_rpc(method: str, params: dict | None = None):
        calls.append((method, params or {}))
        if method == "agents.list":
            return [{"id": "classclaw-extractor"}]
        if method == "config.get":
            return {
                "hash": "extractor-config-v1",
                "config": {
                    "agents": {
                        "defaults": {"model": {"primary": "provider/fast-model"}},
                        "list": [{"id": "classclaw-extractor", "workspace": str(workspace)}],
                    }
                },
            }
        if method == "config.patch":
            return {"ok": True}
        raise AssertionError(method)

    monkeypatch.setattr(openclaw_provisioning, "admin_rpc", fake_rpc)
    assert asyncio.run(bridge.ensure_extractor_agent()) is True
    assert (workspace / "AGENTS.md").read_text(encoding="utf-8") == "# Admin custom\n"
    assert (workspace / "SOUL.md").is_file()
    assert (workspace / "IDENTITY.md").is_file()
    patch = next(params for method, params in calls if method == "config.patch")
    runtime = json.loads(patch["raw"])["agents"]["list"][0]
    assert runtime["model"] == {"primary": "provider/fast-model"}
    assert runtime["thinkingDefault"] == "off"
    assert runtime["reasoningDefault"] == "off"
    assert runtime["fastModeDefault"] is True
    assert runtime["memorySearch"] == {"enabled": False}
    assert runtime["skills"] == []
    assert runtime["tools"] == {"profile": "minimal", "deny": ["session_status"]}
    assert patch["baseHash"] == "extractor-config-v1"


def test_extractor_runtime_is_independent_and_toolless(monkeypatch, tmp_path):
    test_settings = replace(settings, openclaw_class_workspace_root=tmp_path, openclaw_extractor_agent_id="classclaw-extractor")
    monkeypatch.setattr(bridge, "settings", test_settings)
    runtime = bridge.extractor_runtime_defaults()
    assert runtime["id"] == "classclaw-extractor"
    assert runtime["workspace"] == str((tmp_path / "_extractor").resolve())
    assert runtime["tools"]["profile"] == "minimal"
    assert runtime["tools"]["deny"] == ["session_status"]
    assert runtime["thinkingDefault"] == "off"
    assert runtime["fastModeDefault"] is True
