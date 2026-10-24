from __future__ import annotations

import json
import uuid

import pytest
from sqlalchemy import func, select

from app.config import settings
from app.core.errors import AppError
from app.models.entities import AgentChatHiddenConversation, User
from app.services import agent_chat_history
from tests.helpers import make_binding, teacher_for_class

CONVERSATION = "11111111-1111-4111-8111-111111111111"
OTHER_CONVERSATION = "22222222-2222-4222-8222-222222222222"
SESSION = "33333333-3333-4333-8333-333333333333"


@pytest.fixture
def history(client, db, sample, tmp_path):
    cls = sample[0]
    headers = teacher_for_class(client, cls, db, "history-teacher")
    owner = db.scalar(select(User).where(User.username == "history-teacher"))
    make_binding(db, cls.id, tmp_path / "workspace")
    directory = settings.openclaw_state_dir / "agents/class-agent/sessions"
    directory.mkdir(parents=True)
    key = f"agent:class-agent:openresponses-user:classclaw-web-chat:{cls.id}:{owner.id}:{CONVERSATION}"
    session = directory / f"{SESSION}.jsonl"
    index = {key: {"sessionId": SESSION, "sessionFile": str(session), "updatedAt": 1792807800000}}
    records = [
        {"type": "message", "timestamp": "2026-10-24T02:00:00Z", "message": {"role": "system", "content": "private-system-prompt"}},
        {"type": "message", "timestamp": "2026-10-24T02:01:00Z", "message": {"role": "user", "content": "下周一的课表"}},
        {"type": "message", "message": {"role": "assistant", "stopReason": "toolUse", "content": [
            {"type": "text", "text": "private-tool-preamble"}, {"type": "toolCall", "arguments": {"token": "private-token"}},
        ]}},
        {"type": "message", "message": {"role": "toolResult", "content": [{"type": "text", "text": "private-tool-result"}]}},
        {"type": "message", "timestamp": "2026-10-24T02:02:00Z", "message": {"role": "assistant", "stopReason": "stop", "content": [
            {"type": "thinking", "thinking": "private-reasoning"}, {"type": "text", "text": "周一第一节语文"},
        ]}},
    ]

    def write():
        (directory / "sessions.json").write_text(json.dumps(index), encoding="utf-8")
        session.write_text("\n".join(json.dumps(row, ensure_ascii=False) for row in records) + "\n", encoding="utf-8")

    write()
    return {"headers": headers, "owner": owner, "index": index, "records": records, "directory": directory,
            "session": session, "write": write, "base": f"/api/v1/classes/{cls.id}/agent-chat/conversations"}


def test_history_recovers_existing_gateway_conversations_without_exposing_internal_content(client, history):
    listing = client.get(history["base"], headers=history["headers"])
    assert listing.status_code == 200, listing.text
    assert listing.headers["cache-control"] == "no-store"
    assert listing.json()["data"]["items"][0]["id"] == CONVERSATION
    assert listing.json()["data"]["items"][0]["title"] == "下周一的课表"
    messages = client.get(f"{history['base']}/{CONVERSATION}/messages", headers=history["headers"])
    assert messages.status_code == 200, messages.text
    assert messages.headers["cache-control"] == "no-store"
    assert [(item["role"], item["text"]) for item in messages.json()["data"]["messages"]] == [
        ("user", "下周一的课表"), ("assistant", "周一第一节语文"),
    ]
    for private in ("private-", "sessionFile", "classclaw-web-chat", SESSION):
        assert private not in listing.text + messages.text


def test_history_is_scoped_to_logged_in_owner_and_class(client, db, sample, history):
    key = next(iter(history["index"]))
    foreign_key = key.replace(history["owner"].id, str(uuid.uuid4())).replace(CONVERSATION, OTHER_CONVERSATION)
    history["index"][foreign_key] = next(iter(history["index"].values()))
    history["write"]()
    response = client.get(history["base"], headers=history["headers"])
    assert [row["id"] for row in response.json()["data"]["items"]] == [CONVERSATION]
    assert client.get(f"{history['base']}/{OTHER_CONVERSATION}/messages", headers=history["headers"]).status_code == 404
    assert client.get(history["base"], headers={"Authorization": ""}).status_code == 401
    assert client.get(history["base"]).status_code == 403  # Shared service credentials are not a personal history login.
    foreign = teacher_for_class(client, sample[1], db, "other-history-teacher")
    assert client.get(history["base"], headers=foreign).status_code == 403
    assert client.delete(f"{history['base']}/{CONVERSATION}", headers=foreign).status_code == 403
    new_owner = teacher_for_class(client, sample[0], db, "new-history-owner")
    assert client.get(history["base"], headers=new_owner).json()["data"]["items"] == []
    assert client.get(f"{history['base']}/{CONVERSATION}/messages", headers=new_owner).status_code == 404


def test_history_removal_survives_relogin_is_idempotent_and_blocks_reuse(client, db, sample, history):
    path = f"{history['base']}/{CONVERSATION}"
    assert client.delete(path, headers=history["headers"]).status_code == 200
    assert client.delete(path, headers=history["headers"]).status_code == 200
    assert db.scalar(select(func.count()).select_from(AgentChatHiddenConversation)) == 1
    new_token = client.post("/api/v1/auth/login", json={"username": "history-teacher", "password": "32767"}).json()["data"]["access_token"]
    headers = {"Authorization": f"Bearer {new_token}"}
    assert client.get(history["base"], headers=headers).json()["data"]["items"] == []
    assert client.get(path + "/messages", headers=headers).status_code == 410
    blocked = client.post(f"/api/v1/classes/{sample[0].id}/agent-chat/messages", headers=headers,
                          data={"conversation_id": CONVERSATION, "text": "不能复用已删除对话"})
    assert blocked.status_code == 410
    with pytest.raises(AppError) as caught:
        agent_chat_history.require_visible(db, sample[0].id, history["owner"].id, CONVERSATION)
    assert caught.value.code == "CHAT_REMOVED"
    assert history["session"].exists()  # Removal does not rewrite the Gateway's runtime ledger.


@pytest.mark.parametrize("unsafe", ["outside", "symlink", "wrong_name"])
def test_history_rejects_untrusted_paths_without_disclosing_them(client, history, tmp_path, unsafe):
    row = next(iter(history["index"].values()))
    outside = tmp_path / "private.jsonl"
    outside.write_text('secret-data', encoding="utf-8")
    if unsafe == "outside":
        row["sessionFile"] = str(outside)
    elif unsafe == "wrong_name":
        row["sessionFile"] = str(history["directory"] / "another.jsonl")
    else:
        history["session"].unlink()
        history["session"].symlink_to(outside)
    # Do not rewrite the symlink as part of fixture setup.
    (history["directory"] / "sessions.json").write_text(json.dumps(history["index"]), encoding="utf-8")
    response = client.get(f"{history['base']}/{CONVERSATION}/messages", headers=history["headers"])
    assert response.status_code == 503
    assert "secret-data" not in response.text and str(tmp_path) not in response.text


def test_history_paginates_visible_messages_and_marks_incomplete_answers(client, history):
    for number in range(5):
        history["records"].append({"type": "message", "message": {"role": "user", "content": f"消息{number}"}})
    history["records"].append({"type": "message", "message": {"role": "assistant", "content": [{"type": "text", "text": "部分正文"}],
                                                                  "stopReason": "aborted", "errorMessage": "private-error"}})
    history["write"]()
    path = f"{history['base']}/{CONVERSATION}/messages"
    latest = client.get(path + "?limit=2", headers=history["headers"]).json()["data"]
    assert latest["has_more"] is True
    assert [row["text"] for row in latest["messages"]] == ["消息4", "部分正文"]
    assert latest["messages"][-1]["incomplete"] is True
    older = client.get(path + "?limit=2&offset=2", headers=history["headers"]).json()["data"]
    assert [row["text"] for row in older["messages"]] == ["消息2", "消息3"]
    assert "private-error" not in str(latest)


def test_history_limits_corrupt_or_oversized_files(client, history, monkeypatch):
    monkeypatch.setattr(agent_chat_history, "_MAX_HISTORY_BYTES", 10)
    response = client.get(f"{history['base']}/{CONVERSATION}/messages", headers=history["headers"])
    assert response.status_code == 413
    (history["directory"] / "sessions.json").write_text("not-json", encoding="utf-8")
    response = client.get(history["base"], headers=history["headers"])
    assert response.status_code == 503
