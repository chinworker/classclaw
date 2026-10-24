import test from "node:test";
import assert from "node:assert/strict";
import { createChatStore } from "../../web/js/agentChatStore.js";

const listing = { items: [{ id: "old-a", title: "原来的对话", created_at: "2026-10-24T02:00:00Z" }], has_more: false };
const messages = { messages: [{ id: "message-a", role: "user", text: "原来的问题" },
  { id: "message-b", role: "assistant", text: "原来的回答", thinking: { text: "not-a-visible-answer" } }], has_more: false };

test("a fresh login recovers the same conversations and only visible message fields", async () => {
  const request = async (path) => path.includes("/messages?") ? messages : listing;
  let store = createChatStore({ request });
  let scope = store.getScope("teacher-a", "class-a");
  await store.loadHistory(scope);
  let conversation = store.selectedConversation(scope);
  assert.equal(conversation.id, "old-a");
  await store.loadMessages(scope, conversation);
  assert.deepEqual(conversation.messages.map((item) => item.text), ["原来的问题", "原来的回答"]);
  assert.equal(conversation.messages[1].thinking, undefined);
  store.clear();
  store = createChatStore({ request }); scope = store.getScope("teacher-a", "class-a");
  await store.loadHistory(scope); conversation = store.selectedConversation(scope); await store.loadMessages(scope, conversation);
  assert.equal(conversation.id, "old-a"); assert.equal(conversation.messages[1].text, "原来的回答");
});

test("history loading cannot discard a draft or select over a newly created conversation", async () => {
  let finish;
  const store = createChatStore({ request: () => new Promise((resolve) => { finish = resolve; }) });
  const scope = store.getScope("teacher-a", "class-a");
  const initial = store.selectedConversation(scope);
  const pending = store.loadHistory(scope);
  initial.draft = "正在输入的草稿";
  const fresh = store.createConversation(scope);
  finish(listing); await pending;
  assert.equal(scope.selectedId, fresh.id); assert.equal(initial.draft, "正在输入的草稿");
  assert.equal(scope.conversations.length, 3);
});

test("old-account history responses are discarded after logout", async () => {
  let finish;
  const store = createChatStore({ request: () => new Promise((resolve) => { finish = resolve; }) });
  const oldScope = store.getScope("teacher-a", "class-a");
  const pending = store.loadHistory(oldScope);
  store.clear();
  const newScope = store.getScope("teacher-b", "class-b");
  finish(listing); await pending;
  assert.equal(oldScope.conversations.length, 0);
  assert.equal(newScope.conversations.length, 1); assert.equal(newScope.conversations[0].messages.length, 0);
});

test("deletion is persisted before the local row is removed and late lists cannot restore it", async () => {
  let resolveList;
  const store = createChatStore({ request: (path, options = {}) => {
    if (options.method === "DELETE") return Promise.resolve({ removed: true });
    return new Promise((resolve) => { resolveList = resolve; });
  } });
  const scope = store.getScope("teacher-a", "class-a");
  const conversation = store.selectedConversation(scope);
  const pending = store.loadHistory(scope);
  await store.removeConversation(scope, conversation);
  resolveList({ items: [{ ...listing.items[0], id: conversation.id }], has_more: false }); await pending;
  assert.ok(!scope.conversations.some((item) => item.id === conversation.id));
});

test("failed history loads remain retryable and pagination prepends older messages without duplicates", async () => {
  let fail = true;
  const store = createChatStore({ request: async (path) => {
    if (!path.includes("/messages?")) {
      if (fail) throw new Error("历史暂不可用");
      return listing;
    }
    if (path.includes("offset=0")) return { ...messages, has_more: true };
    return { messages: [{ id: "earliest", role: "user", text: "更早的问题" }, messages.messages[0]], has_more: false };
  } });
  const scope = store.getScope("teacher-a", "class-a");
  await store.loadHistory(scope); assert.equal(scope.historyError.message, "历史暂不可用");
  fail = false; await store.loadHistory(scope, { force: true });
  const conversation = store.selectedConversation(scope);
  await store.loadMessages(scope, conversation); await store.loadMessages(scope, conversation, { more: true });
  assert.deepEqual(conversation.messages.map((item) => item.text), ["更早的问题", "原来的问题", "原来的回答"]);
  assert.equal(conversation.historyHasMore, false);
});
