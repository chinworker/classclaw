import test, { beforeEach, afterEach } from "node:test";
import assert from "node:assert/strict";
import { installDom, tick, response } from "./dom.mjs";

installDom();
const page = await import("../../web/js/pages/agent.js");
const { state, saveSession, clearSession } = await import("../../web/js/state.js");
const { agentChatStore } = await import("../../web/js/agentChatStore.js");
let calls;
let thinkingProfile;

beforeEach(() => {
  page.dispose();
  agentChatStore.clear();
  document.body.replaceChildren();
  state.user = { id: "teacher-a", role: "head_teacher" };
  state.classId = "class-a";
  saveSession("test-token-a");
  calls = [];
  thinkingProfile = { model: "provider/model-a", levels: [{ id: "off", label: "关闭" }, { id: "high", label: "高" }],
    default_level: "off", default_adjusted: false };
  globalThis.fetch = async (path, options = {}) => {
    if (path.includes("/agent-chat/messages")) return new Promise((resolve, reject) => {
      calls.push({ path, options, resolve: (reply) => resolve(response({ reply })), reject });
      options.signal.addEventListener("abort", () => reject(new DOMException("cancelled", "AbortError")));
    });
    if (path.includes("/openclaw/status")) return response({ ready: true, gateway_live: true, plugin_ready: true });
    if (path.endsWith("/agent-chat/thinking")) return response(thinkingProfile);
    if (path.endsWith("/agent-binding")) return response({ agent_name: "测试 Agent", openclaw_agent_id: "agent-a", status: "linked", channel_account_id: "test-channel" });
    if (path.endsWith("/cancel")) return response({ cancelled: true });
    throw new Error(`Unexpected request: ${path}`);
  };
});

afterEach(async () => { page.dispose(); agentChatStore.clear(); await tick(); });

async function mountPage() {
  const mount = document.createElement("main");
  document.body.append(mount);
  await page.render(mount, {}, { refreshOpenclawDot() {} });
  return mount;
}

async function send(mount, text) {
  const input = mount.querySelector("textarea");
  input.value = text;
  input.dispatchEvent(new Event("input"));
  mount.querySelector(".agent-chat-send").click();
  await tick();
}

function newChat(mount) { mount.querySelectorAll("button").find((button) => button.textContent === "新对话").click(); }
function choose(mount, title) {
  mount.querySelectorAll(".agent-conversation-item").find((button) => button.querySelector(".agent-conversation-title").textContent === title).click();
}

test("thinking choices and binary labels match the model reported by OpenClaw", async () => {
  thinkingProfile = { model: "kimi/kimi-for-coding", levels: [{ id: "off", label: "关闭" }, { id: "low", label: "开启" }], default_level: "off" };
  const mount = await mountPage();
  const selector = mount.querySelector(".agent-thinking-select");
  assert.deepEqual(selector.querySelectorAll("option").map((option) => [option.value, option.textContent]),
    [["", "跟随默认（关闭）"], ["off", "关闭"], ["low", "开启"]]);
  selector.value = "low";
  selector.dispatchEvent(new Event("change"));
  await send(mount, "开启思考");
  assert.equal(calls[0].options.body.get("thinking_level"), "low");
  calls[0].resolve("完成");
  await tick();
});

test("returning after a model change clears only unsupported conversation choices", async () => {
  let mount = await mountPage();
  const scope = agentChatStore.getScope("teacher-a", "class-a");
  const first = agentChatStore.selectedConversation(scope);
  agentChatStore.setThinkingLevel(scope, first, "high");
  const second = agentChatStore.createConversation(scope);
  agentChatStore.setThinkingLevel(scope, second, "off");
  page.dispose();
  mount.remove();
  thinkingProfile = { model: "other/binary", levels: [{ id: "off", label: "关闭" }, { id: "low", label: "开启" }], default_level: "off" };
  mount = await mountPage();
  assert.equal(first.thinkingLevel, null);
  assert.equal(second.thinkingLevel, "off");
  agentChatStore.selectConversation(scope, first.id);
  assert.equal(mount.querySelector(".agent-thinking-select").value, "");
  assert.match(mount.querySelector(".agent-thinking-note").textContent, /已恢复为跟随默认/);
});

test("sending refreshes stale model choices and preserves the draft for review", async () => {
  const mount = await mountPage();
  const selector = mount.querySelector(".agent-thinking-select");
  selector.value = "high";
  selector.dispatchEvent(new Event("change"));
  thinkingProfile = { model: "other/plain", levels: [{ id: "off", label: "关闭" }], default_level: "off" };
  await send(mount, "模型已切换");
  assert.equal(calls.length, 0);
  assert.equal(mount.querySelector("textarea").value, "模型已切换");
  assert.deepEqual(selector.querySelectorAll("option").map((option) => option.value), ["", "off"]);
  assert.equal(selector.value, "");
  await send(mount, "模型已切换");
  assert.equal(calls[0].options.body.has("thinking_level"), false);
  calls[0].resolve("完成");
  await tick();
});

test("model-specific defaults are displayed and explicit choices remain available", async () => {
  thinkingProfile = { model: "other/adaptive", levels: [{ id: "adaptive", label: "自适应" }, { id: "max", label: "最高" }],
    configured_default_level: "off", default_level: "adaptive", default_adjusted: true };
  const mount = await mountPage();
  const selector = mount.querySelector(".agent-thinking-select");
  assert.equal(selector.querySelector("option").textContent, "跟随默认（自适应）");
  assert.match(mount.querySelector(".agent-thinking-note").textContent, /默认采用自适应/);
  assert.deepEqual(selector.querySelectorAll("option").map((option) => option.value), ["", "adaptive", "max"]);
});

test("unknown capabilities disable choices and a failed preflight preserves the draft", async () => {
  const fallback = globalThis.fetch;
  globalThis.fetch = async (path, options) => path.endsWith("/agent-chat/thinking")
    ? response(null, 503) : fallback(path, options);
  const mount = await mountPage();
  const selector = mount.querySelector(".agent-thinking-select");
  assert.equal(selector.disabled, true);
  assert.equal(selector.querySelectorAll("option").length, 1);
  await send(mount, "请保留草稿");
  assert.equal(calls.length, 0);
  assert.equal(mount.querySelector("textarea").value, "请保留草稿");
  assert.equal(Boolean(mount.querySelector(".agent-chat-send").disabled), false);
});

test("focus refreshes capabilities without altering a running request", async () => {
  const mount = await mountPage();
  const selector = mount.querySelector(".agent-thinking-select");
  selector.value = "high";
  selector.dispatchEvent(new Event("change"));
  await send(mount, "已发送的请求");
  thinkingProfile = { model: "other/plain", levels: [{ id: "off", label: "关闭" }], default_level: "off" };
  window.dispatchEvent(new Event("focus"));
  await tick();
  assert.equal(calls[0].options.body.get("thinking_level"), "high");
  assert.equal(calls[0].options.signal.aborted, false);
  assert.equal(selector.disabled, true);
  calls[0].resolve("完成");
  await tick();
  assert.equal(selector.value, "");
  assert.equal(selector.disabled, false);
  assert.deepEqual(selector.querySelectorAll("option").map((option) => option.value), ["", "off"]);
});

test("a late capabilities response cannot replace another class's choices", async () => {
  const fallback = globalThis.fetch;
  let release;
  globalThis.fetch = async (path, options) => path.includes("/classes/class-a/agent-chat/thinking")
    ? new Promise((resolve) => { release = resolve; }) : fallback(path, options);
  const oldMount = document.createElement("main");
  document.body.append(oldMount);
  const oldRender = page.render(oldMount, {}, { refreshOpenclawDot() {} });
  await tick();
  page.dispose();
  oldMount.remove();
  state.classId = "class-b";
  thinkingProfile = { model: "class-b/plain", levels: [{ id: "off", label: "关闭" }], default_level: "off" };
  const current = await mountPage();
  release(response({ model: "class-a/high", levels: [{ id: "high", label: "高" }], default_level: "high" }));
  await oldRender;
  assert.deepEqual(current.querySelector(".agent-thinking-select").querySelectorAll("option").map((option) => option.value), ["", "off"]);
  assert.equal(agentChatStore.getScope("teacher-a", "class-a").thinkingOptions, null);
  assert.equal(agentChatStore.getScope("teacher-a", "class-b").thinkingOptions.model, "class-b/plain");
});

test("the thinking selector is scoped, survives navigation, and is disabled while replying", async () => {
  let mount = await mountPage();
  let selector = mount.querySelector(".agent-thinking-select");
  selector.value = "high";
  selector.dispatchEvent(new Event("change"));
  await send(mount, "高强度会话");
  assert.equal(calls[0].options.body.get("thinking_level"), "high");
  assert.equal(selector.disabled, true);
  newChat(mount);
  assert.equal(mount.querySelector(".agent-thinking-select").value, "");
  choose(mount, "高强度会话");
  assert.equal(mount.querySelector(".agent-thinking-select").value, "high");
  calls[0].resolve("已完成");
  await tick();
  page.dispose();
  mount.remove();
  mount = await mountPage();
  selector = mount.querySelector(".agent-thinking-select");
  assert.equal(selector.value, "high");
  assert.equal(selector.disabled, false);
  selector.value = "";
  selector.dispatchEvent(new Event("change"));
  await send(mount, "恢复默认");
  assert.equal(calls[1].options.body.has("thinking_level"), false);
  calls[1].resolve("已完成");
  await tick();
});

test("streaming updates only the current answer node and continues off-page", async () => {
  const fallback = globalThis.fetch;
  let writer;
  globalThis.fetch = async (path, options) => {
    if (!path.includes("/agent-chat/messages")) return fallback(path, options);
    return new Response(new ReadableStream({ start(controller) { writer = controller; } }), { headers: { "Content-Type": "text/event-stream" } });
  };
  const push = (event, data) => writer.enqueue(new TextEncoder().encode(`event: ${event}\ndata: ${JSON.stringify({ success: true, data })}\n\n`));
  let mount = await mountPage();
  await send(mount, "流式对话");
  await tick();
  push("delta", { text: "第一段" });
  await tick();
  const userNode = mount.querySelector(".from-user");
  push("delta", { text: "第二段" });
  await tick();
  assert.equal(mount.querySelector(".from-user"), userNode);
  assert.ok(mount.querySelector(".agent-chat-messages").textContent.includes("第一段第二段"));
  newChat(mount);
  push("delta", { text: "第三段" });
  await tick();
  assert.equal(mount.querySelector(".agent-chat-messages").textContent.includes("第三段"), false);
  page.dispose();
  mount.remove();
  push("done", { reply: "第一段第二段第三段，最终完成" });
  await tick();
  mount = await mountPage();
  choose(mount, "流式对话");
  assert.ok(mount.querySelector(".agent-chat-messages").textContent.includes("最终完成"));
  assert.equal(mount.querySelectorAll(".agent-chat-message").length, 2);
});

test("new conversation appears immediately; replies stay in their originating conversation", async () => {
  const mount = await mountPage();
  await send(mount, "第一条对话");
  assert.ok(mount.querySelector(".agent-chat-thinking"));
  newChat(mount);
  assert.equal(mount.querySelectorAll(".agent-conversation-item").length, 2);
  assert.equal(calls[0].options.signal.aborted, false);
  assert.equal(mount.querySelector(".agent-chat-messages").textContent.includes("第一条对话"), false);
  await send(mount, "第二条对话");
  calls[0].resolve("旧对话后台完成");
  await tick();
  assert.ok(mount.querySelector(".agent-conversation-list").textContent.includes("新回复"));
  assert.equal(mount.querySelector(".agent-chat-messages").textContent.includes("旧对话后台完成"), false);
  choose(mount, "第一条对话");
  assert.ok(mount.querySelector(".agent-chat-messages").textContent.includes("旧对话后台完成"));
  assert.equal(calls[1].options.signal.aborted, false);
  choose(mount, "第二条对话");
  assert.ok(mount.querySelector(".agent-chat-thinking"));
  calls[1].resolve("第二条完成");
  await tick();
  assert.ok(mount.querySelector(".agent-chat-messages").textContent.includes("第二条完成"));
});

test("leaving the page does not stop a request or steal focus, returning recovers the result", async () => {
  const mount = await mountPage();
  await send(mount, "离开页面后继续");
  const conversationId = calls[0].options.body.get("conversation_id");
  page.dispose();
  mount.remove();
  const otherInput = document.createElement("textarea");
  document.body.append(otherInput);
  otherInput.focus();
  assert.equal(calls[0].options.signal.aborted, false);
  calls[0].resolve("已在后台完成");
  await tick();
  assert.equal(document.activeElement, otherInput);
  const returned = await mountPage();
  assert.equal(returned.querySelectorAll(".agent-conversation-item").length, 1);
  assert.ok(returned.querySelector(".agent-chat-messages").textContent.includes("已在后台完成"));
  await send(returned, "继续这条对话");
  assert.equal(calls[1].options.body.get("conversation_id"), conversationId);
  calls[1].resolve("好的");
  await tick();
});

test("returning to a running conversation restores the busy state and Stop only stops it", async () => {
  const mount = await mountPage();
  await send(mount, "正在运行");
  page.dispose();
  mount.remove();
  const returned = await mountPage();
  assert.equal(returned.querySelector("textarea").disabled, true);
  assert.equal(returned.querySelector(".agent-chat-stop").classList.contains("hidden"), false);
  newChat(returned);
  await send(returned, "另一条仍然运行");
  choose(returned, "正在运行");
  returned.querySelector(".agent-chat-stop").click();
  await tick();
  assert.equal(calls[0].options.signal.aborted, true);
  assert.equal(calls[1].options.signal.aborted, false);
  assert.equal(returned.querySelector("textarea").disabled, false);
  assert.ok(returned.querySelector(".agent-chat-messages").textContent.includes("已停止本次回答"));
  calls[1].resolve("继续完成");
  await tick();
});

test("drafts survive switching and logout removes all previous conversation data", async () => {
  const mount = await mountPage();
  await send(mount, "已有对话");
  calls[0].resolve("收到");
  await tick();
  const input = mount.querySelector("textarea");
  input.value = "未发送草稿";
  input.dispatchEvent(new Event("input"));
  newChat(mount);
  choose(mount, "已有对话");
  assert.equal(mount.querySelector("textarea").value, "未发送草稿");
  clearSession();
  assert.equal(mount.textContent, "");
  state.user = { id: "teacher-b", role: "head_teacher" };
  state.classId = "class-b";
  saveSession("test-token-b");
  mount.remove();
  const returned = await mountPage();
  assert.equal(returned.querySelectorAll(".agent-conversation-item").length, 1);
  assert.equal(returned.textContent.includes("已有对话"), false);
});

test("a microphone permission response after leaving closes the stream without recording", async () => {
  let grant;
  let stopped = false;
  globalThis.MediaRecorder = class { constructor() { throw new Error("Must not start a recorder after leaving"); } };
  navigator.mediaDevices = { getUserMedia: () => new Promise((resolve) => { grant = resolve; }) };
  const originalFetch = fetch;
  globalThis.fetch = (path, options) => path.endsWith("/agent-binding")
    ? Promise.resolve(response({ agent_name: "测试 Agent", openclaw_agent_id: "agent-a", channel_account_id: "test-channel", speech_model: "test/stt" }))
    : originalFetch(path, options);
  try {
    const mount = await mountPage();
    mount.querySelectorAll("button").find((button) => button.textContent === "语音").click();
    page.dispose();
    mount.remove();
    grant({ getTracks: () => [{ stop() { stopped = true; } }] });
    await tick();
    assert.equal(stopped, true);
  } finally {
    delete globalThis.MediaRecorder;
    delete navigator.mediaDevices;
  }
});

test("attachment drafts remain in the original conversation and upload with its ID", async () => {
  const mount = await mountPage();
  const scope = agentChatStore.getScope("teacher-a", "class-a");
  const originalId = scope.selectedId;
  const file = new File(["test"], "资料.txt", { type: "text/plain" });
  const input = mount.querySelector("input");
  input.files = [file];
  input.dispatchEvent(new Event("change"));
  newChat(mount);
  assert.equal(mount.querySelector(".agent-chat-selected").textContent, "");
  mount.querySelectorAll(".agent-conversation-item")[1].click();
  assert.ok(mount.querySelector(".agent-chat-selected").textContent.includes("资料.txt"));
  await send(mount, "处理附件");
  assert.equal(calls[0].options.body.get("files"), file);
  assert.equal(calls[0].options.body.get("conversation_id"), originalId);
  calls[0].resolve("处理完成");
  await tick();
});

test("late page initialization and stale rerender callbacks cannot replace the active view", async () => {
  const originalFetch = fetch;
  let releaseStatus;
  globalThis.fetch = (path, options) => path.includes("/openclaw/status")
    ? new Promise((resolve) => { releaseStatus = resolve; }) : originalFetch(path, options);
  const oldMount = document.createElement("main");
  document.body.append(oldMount);
  const oldRender = page.render(oldMount, {}, { refreshOpenclawDot() {} });
  newChat(oldMount);
  assert.equal(oldMount.querySelectorAll(".agent-conversation-item").length, 2);
  page.dispose();
  oldMount.remove();
  globalThis.fetch = originalFetch;
  const current = await mountPage();
  releaseStatus(response({ ready: true, gateway_live: true, plugin_ready: true }));
  await oldRender;
  await page.render(oldMount, {}, { refreshOpenclawDot() {} });
  assert.equal(agentChatStore.getScope("teacher-a", "class-a").listeners.size, 1);
  await send(current, "当前视图仍可更新");
  calls[0].resolve("更新正常");
  await tick();
  assert.ok(current.querySelector(".agent-chat-messages").textContent.includes("更新正常"));
});

test("Agent page has the shorter title and links to account settings without binding or model controls", async () => {
  const originalFetch = fetch;
  globalThis.fetch = (path, options) => {
    if (path.endsWith("/agent-binding")) return Promise.resolve(response({
      agent_name: "测试 Agent", openclaw_agent_id: "agent-a", channel_account_id: "pending-alias", status: "awaiting_qr",
    }));
    return originalFetch(path, options);
  };
  const mount = await mountPage();
  assert.equal(mount.querySelector("h2").textContent, "班级 Agent");
  assert.equal(mount.querySelector(".qr-panel"), null);
  assert.equal(mount.querySelector(".agent-model-button"), null);
  assert.ok(mount.querySelectorAll("a").some((link) => link.getAttribute("href") === "#/account"));
  assert.ok(mount.querySelector("textarea"));
});

test("missing Agent directs provisioning to account settings", async () => {
  const originalFetch = fetch;
  globalThis.fetch = (path, options) => path.endsWith("/agent-binding")
    ? Promise.resolve(response({ status: "pending_agent" })) : originalFetch(path, options);
  const mount = await mountPage();
  assert.ok(mount.textContent.includes("请到账户设置中创建"));
  assert.equal(mount.querySelector("textarea"), null);
  assert.equal(mount.querySelectorAll("button").some((button) => button.textContent === "创建班级 Agent"), false);
});
