import test, { beforeEach, afterEach } from "node:test";
import assert from "node:assert/strict";
import { installDom, tick, response } from "./dom.mjs";

installDom();
const page = await import("../../web/js/pages/agent.js");
const { state, saveSession, clearSession } = await import("../../web/js/state.js");
const { agentChatStore } = await import("../../web/js/agentChatStore.js");
let calls;

beforeEach(() => {
  page.dispose();
  agentChatStore.clear();
  document.body.replaceChildren();
  state.user = { id: "teacher-a", role: "head_teacher" };
  state.classId = "class-a";
  saveSession("test-token-a");
  calls = [];
  globalThis.fetch = async (path, options = {}) => {
    if (path.includes("/agent-chat/messages")) return new Promise((resolve, reject) => {
      calls.push({ path, options, resolve: (reply) => resolve(response({ reply })), reject });
      options.signal.addEventListener("abort", () => reject(new DOMException("cancelled", "AbortError")));
    });
    if (path.includes("/openclaw/status")) return response({ ready: true, gateway_live: true, plugin_ready: true });
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

function send(mount, text) {
  const input = mount.querySelector("textarea");
  input.value = text;
  input.dispatchEvent(new Event("input"));
  mount.querySelector(".agent-chat-send").click();
}

function newChat(mount) { mount.querySelectorAll("button").find((button) => button.textContent === "新对话").click(); }
function choose(mount, title) {
  mount.querySelectorAll(".agent-conversation-item").find((button) => button.querySelector(".agent-conversation-title").textContent === title).click();
}

test("the thinking selector is scoped, survives navigation, and is disabled while replying", async () => {
  let mount = await mountPage();
  let selector = mount.querySelector(".agent-thinking-select");
  selector.value = "high";
  selector.dispatchEvent(new Event("change"));
  send(mount, "高强度会话");
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
  send(mount, "恢复默认");
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
  send(mount, "流式对话");
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
  send(mount, "第一条对话");
  assert.ok(mount.querySelector(".agent-chat-thinking"));
  newChat(mount);
  assert.equal(mount.querySelectorAll(".agent-conversation-item").length, 2);
  assert.equal(calls[0].options.signal.aborted, false);
  assert.equal(mount.querySelector(".agent-chat-messages").textContent.includes("第一条对话"), false);
  send(mount, "第二条对话");
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
  send(mount, "离开页面后继续");
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
  send(returned, "继续这条对话");
  assert.equal(calls[1].options.body.get("conversation_id"), conversationId);
  calls[1].resolve("好的");
  await tick();
});

test("returning to a running conversation restores the busy state and Stop only stops it", async () => {
  const mount = await mountPage();
  send(mount, "正在运行");
  page.dispose();
  mount.remove();
  const returned = await mountPage();
  assert.equal(returned.querySelector("textarea").disabled, true);
  assert.equal(returned.querySelector(".agent-chat-stop").classList.contains("hidden"), false);
  newChat(returned);
  send(returned, "另一条仍然运行");
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
  send(mount, "已有对话");
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
  send(mount, "处理附件");
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
  send(current, "当前视图仍可更新");
  calls[0].resolve("更新正常");
  await tick();
  assert.ok(current.querySelector(".agent-chat-messages").textContent.includes("更新正常"));
});

test("leaving during QR setup does not schedule another poll or rerender a detached view", async () => {
  const originalFetch = fetch;
  let finishQr;
  globalThis.fetch = (path, options) => {
    if (path.endsWith("/agent-binding")) return Promise.resolve(response({ agent_name: "测试 Agent", openclaw_agent_id: "agent-a" }));
    if (path.endsWith("/agent-binding/start")) return new Promise((resolve) => { finishQr = resolve; });
    return originalFetch(path, options);
  };
  const mount = await mountPage();
  mount.querySelectorAll("button").find((button) => button.textContent === "绑定微信 / 重新生成二维码").click();
  page.dispose();
  mount.remove();
  const qrText = mount.querySelector(".qr-panel").textContent;
  finishQr(response({ connected: false, message: "等待扫码" }));
  await tick();
  assert.equal(mount.querySelector(".qr-panel").textContent, qrText);
});
