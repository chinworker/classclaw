import test from "node:test";
import assert from "node:assert/strict";
import { createChatStore } from "../../web/js/agentChatStore.js";

const tick = () => new Promise((resolve) => setImmediate(resolve));

test("capability preflight stays with the sent conversation across navigation", async () => {
  let finishThinking;
  const calls = [];
  const store = createChatStore({ request: (path, options) => path.endsWith("/thinking")
    ? new Promise((resolve) => { finishThinking = resolve; })
    : new Promise((resolve) => { calls.push({ path, options, resolve }); }) });
  const scope = store.getScope("teacher-a", "class-a");
  const first = store.selectedConversation(scope);
  const run = store.sendMessage(scope, first, "发送后立即离开");
  assert.equal(first.status, "running");
  const second = store.createConversation(scope);
  finishThinking({ levels: [{ id: "off", label: "关闭" }], default_level: "off" });
  await tick();
  assert.equal(calls[0].options.body.get("conversation_id"), first.id);
  assert.equal(calls[0].options.signal.aborted, false);
  calls[0].resolve({ reply: "后台完成" });
  await run;
  assert.equal(first.messages.at(-1).text, "后台完成");
  assert.equal(second.messages.length, 0);
});

for (const action of ["stop", "logout"]) test(`${action} during capabilities preflight prevents a later model request`, async () => {
  let finishThinking;
  let calls = 0;
  const store = createChatStore({ request: (path) => {
    assert.ok(path.endsWith("/thinking"));
    calls += 1;
    return new Promise((resolve) => { finishThinking = resolve; });
  } });
  const scope = store.getScope("teacher-a", "class-a");
  const conversation = store.selectedConversation(scope);
  const run = store.sendMessage(scope, conversation, "原草稿");
  if (action === "stop") store.stopMessage(conversation);
  else store.clear();
  finishThinking({ levels: [{ id: "off", label: "关闭" }], default_level: "off" });
  await run;
  assert.equal(calls, 1);
  if (action === "stop") {
    assert.equal(conversation.status, "stopped");
    assert.equal(conversation.draft, "原草稿");
  } else {
    assert.equal(scope.conversations.length, 0);
    assert.equal(scope.thinkingOptions, null);
  }
});

function setup() {
  const calls = [];
  let next = 0;
  const store = createChatStore({
    newId: () => `id-${++next}`,
    request: (path, options) => path.endsWith("/agent-chat/thinking")
      ? Promise.resolve({ levels: [{ id: "off", label: "关闭" }, { id: "high", label: "高" }], default_level: "off" })
      : new Promise((resolve, reject) => {
      calls.push({ path, options, resolve, reject });
      options.signal.addEventListener("abort", () => reject(Object.assign(new Error("cancelled"), { code: "REQUEST_CANCELLED" })));
    }),
  });
  const scope = store.getScope("teacher-a", "class-a");
  return { store, scope, calls, first: store.selectedConversation(scope) };
}

test("thinking overrides belong to individual conversations and are frozen for a running turn", async () => {
  const { store, scope, first, calls } = setup();
  assert.equal(first.thinkingLevel, null);
  assert.equal(store.setThinkingLevel(scope, first, "high"), true);
  const run = store.sendMessage(scope, first, "需要仔细分析");
  await tick();
  assert.equal(calls[0].options.body.get("thinking_level"), "high");
  assert.equal(calls[0].options.body.get("stream"), "true");
  assert.equal(store.setThinkingLevel(scope, first, "off"), false);
  const second = store.createConversation(scope);
  assert.equal(second.thinkingLevel, null);
  const secondRun = store.sendMessage(scope, second, "独立新对话");
  await tick();
  assert.equal(calls[1].options.body.has("thinking_level"), false);
  calls[0].resolve({ reply: "完成" });
  calls[1].resolve({ reply: "完成" });
  await Promise.all([run, secondRun]);
  assert.equal(store.setThinkingLevel(scope, first, null), true);
  assert.equal(store.setThinkingLevel(scope, first, "unknown"), false);
});

test("streamed deltas stay with their source across switches and failures mark partial answers incomplete", async () => {
  const { store, scope, first, calls } = setup();
  const run = store.sendMessage(scope, first, "流式任务");
  await tick();
  calls[0].options.onDelta("第一段");
  const second = store.createConversation(scope);
  calls[0].options.onDelta("第二段");
  assert.equal(first.messages.at(-1).text, "第一段第二段");
  assert.equal(first.messages.at(-1).streaming, true);
  assert.equal(second.messages.length, 0);
  calls[0].reject(Object.assign(new Error("连接断开"), { code: "STREAM_INTERRUPTED" }));
  await run;
  assert.equal(first.status, "failed");
  assert.equal(first.messages.at(-2).incomplete, true);
  assert.equal(first.messages.at(-2).streaming, false);
  assert.equal(first.messages.at(-1).error, true);
});

test("final responses replace partial snapshots; late deltas cannot leak after logout", async () => {
  const { store, scope, first, calls } = setup();
  let run = store.sendMessage(scope, first, "流式任务");
  await tick();
  calls[0].options.onDelta("不完整");
  calls[0].resolve({ reply: "最终完整回复" });
  await run;
  assert.deepEqual(first.messages.map((m) => m.text), ["流式任务", "最终完整回复"]);
  run = store.sendMessage(scope, first, "下一轮");
  await tick();
  store.clear();
  const count = first.messages.length;
  calls[1].options.onDelta("退出后回复");
  await run;
  assert.equal(first.messages.length, count);
});

test("new conversations are listed immediately and retain their IDs when selected", () => {
  const { store, scope, first } = setup();
  const second = store.createConversation(scope);
  assert.deepEqual(scope.conversations.map((c) => c.id), [second.id, first.id]);
  assert.equal(second.title, "新对话");
  assert.equal(second.messages.length, 0);
  store.selectConversation(scope, first.id);
  assert.equal(store.selectedConversation(scope), first);
  assert.equal(store.getScope("teacher-a", "class-a"), scope);
});

test("new chat and out-of-order replies never cancel or mix running conversations", async () => {
  const { store, scope, calls, first } = setup();
  const firstRun = store.sendMessage(scope, first, "原对话请求");
  await tick();
  const second = store.createConversation(scope);
  const secondRun = store.sendMessage(scope, second, "新对话请求");
  await tick();
  assert.equal(calls[0].options.signal.aborted, false);
  assert.equal(calls[0].options.body.get("conversation_id"), first.id);
  assert.equal(calls[1].options.body.get("conversation_id"), second.id);
  assert.notEqual(calls[0].options.aiTaskId, calls[1].options.aiTaskId);
  calls[1].resolve({ reply: "新对话先完成" });
  await secondRun;
  assert.equal(first.status, "running");
  calls[0].resolve({ reply: "原对话稍后完成" });
  await firstRun;
  assert.deepEqual(first.messages.map((m) => m.text), ["原对话请求", "原对话稍后完成"]);
  assert.deepEqual(second.messages.map((m) => m.text), ["新对话请求", "新对话先完成"]);
  assert.equal(scope.selectedId, second.id);
});

test("leaving a view drops its subscription, not the request; returning sees the result", async () => {
  const { store, scope, calls, first } = setup();
  let renders = 0;
  const leave = store.subscribe(scope, () => { renders += 1; });
  const running = store.sendMessage(scope, first, "后台继续");
  await tick();
  assert.ok(renders >= 1);
  const rendersWhenLeaving = renders;
  leave();
  assert.equal(calls[0].options.signal.aborted, false);
  calls[0].resolve({ reply: "后台已完成" });
  await running;
  assert.equal(renders, rendersWhenLeaving);
  const returned = store.getScope("teacher-a", "class-a");
  assert.equal(store.selectedConversation(returned).messages.at(-1).text, "后台已完成");
  assert.equal(first.unread, true);
  store.selectConversation(returned, first.id);
  assert.equal(first.unread, false);
});

test("one pending turn per conversation and explicit Stop affects only that conversation", async () => {
  const { store, scope, calls, first } = setup();
  const firstRun = store.sendMessage(scope, first, "任务一");
  await tick();
  assert.equal(await store.sendMessage(scope, first, "重复提交"), false);
  const second = store.createConversation(scope);
  const secondRun = store.sendMessage(scope, second, "任务二");
  await tick();
  store.stopMessage(first);
  await firstRun;
  assert.equal(first.status, "stopped");
  assert.equal(first.messages.at(-1).error, false);
  assert.equal(calls[1].options.signal.aborted, false);
  calls[1].resolve({ reply: "任务二完成" });
  await secondRun;
  const resumed = store.sendMessage(scope, first, "继续提问");
  await tick();
  calls[2].resolve({ reply: "可以继续" });
  await resumed;
  assert.equal(first.status, "completed");
});

test("failures, drafts, files, and titles stay with the correct conversation", async () => {
  const { store, scope, calls, first } = setup();
  first.draft = "未发送草稿";
  const file = new File(["content"], "名单.txt", { type: "text/plain" });
  first.files = [file];
  const second = store.createConversation(scope);
  store.selectConversation(scope, first.id);
  assert.equal(first.draft, "未发送草稿");
  assert.equal(first.files[0], file);
  const running = store.sendMessage(scope, first, "", first.files);
  await tick();
  assert.equal(first.title, "名单.txt");
  assert.equal(first.draft, "");
  assert.equal(first.files.length, 0);
  assert.equal(calls[0].options.body.get("files"), file);
  assert.equal(first.messages[0].files[0] instanceof File, false);
  calls[0].reject(Object.assign(new Error("模型超时"), { code: "REQUEST_TIMEOUT", requestId: "request-one" }));
  await running;
  assert.equal(first.status, "failed");
  assert.equal(first.messages.at(-1).requestId, "request-one");
  assert.equal(second.messages.length, 0);
});

test("identity scopes are isolated and logout clears all data, ignoring late replies", async () => {
  const { store, scope, calls, first } = setup();
  const other = store.getScope("teacher-b", "class-a");
  const otherClass = store.getScope("teacher-a", "class-b");
  assert.notEqual(scope, other);
  assert.notEqual(scope, otherClass);
  const running = store.sendMessage(scope, first, "私密对话");
  await tick();
  store.clear();
  assert.equal(calls[0].options.signal.aborted, true);
  await running;
  calls[0].resolve({ reply: "迟到的回复" });
  assert.equal(scope.conversations.length, 0);
  assert.equal(scope.listeners.size, 0);
  assert.equal(first.messages.length, 1);
  assert.equal(await store.sendMessage(scope, first, "登出后不可发送"), false);
  const again = store.getScope("teacher-a", "class-a");
  assert.notEqual(again, scope);
  assert.equal(store.selectedConversation(again).messages.length, 0);
});
