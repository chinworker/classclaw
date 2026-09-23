import test, { beforeEach, afterEach } from "node:test";
import assert from "node:assert/strict";
import { installDom, tick, response } from "./dom.mjs";

installDom();
const page = await import("../../web/js/pages/account.js");
const agentPage = await import("../../web/js/pages/agent.js");
const { state, saveSession } = await import("../../web/js/state.js");
const { agentChatStore } = await import("../../web/js/agentChatStore.js");
const { appConfig } = await import("../../web/js/config.js");
let calls;
let binding;
let modelSettings;
const qr = "data:image/png;base64,cXI=";

beforeEach((t) => {
  t.mock.timers.enable({ apis: ["setTimeout"] });
  page.dispose(); agentPage.dispose(); agentChatStore.clear();
  document.body.replaceChildren();
  state.user = { id: "teacher-a", username: "teacher", role: "head_teacher" };
  state.classId = "class-a";
  saveSession("test-token-a");
  appConfig.features.wechat_binding = true;
  calls = [];
  binding = { agent_name: "测试 Agent", openclaw_agent_id: "agent-a", status: "awaiting_qr", channel_account_id: "pending-alias" };
  modelSettings = { agent_name: "测试 Agent", configured: {}, effective: {}, models: [], image_models: [], speech_models: [] };
  globalThis.fetch = async (path, options = {}) => {
    calls.push({ path, options });
    if (path.endsWith("/agent-binding")) return response(binding);
    if (path.endsWith("/start")) return response({ connected: false, qr_data_url: qr });
    if (path.endsWith("/agent-chat/models")) return response(modelSettings);
    if (path.endsWith("/agent-chat/thinking")) return response({ levels: [{ id: "off", label: "关闭" }], default_level: "off" });
    if (path.endsWith("/cancel")) return response({ cancelled: true });
    throw new Error(`Unexpected request: ${path}`);
  };
});
afterEach(async () => { page.dispose(); agentPage.dispose(); agentChatStore.clear(); await tick(); });
async function mountPage() {
  const mount = document.createElement("main");
  document.body.append(mount);
  await page.render(mount);
  return mount;
}
function button(mount, text) { return mount.querySelectorAll("button").find((node) => node.textContent === text); }

test("teacher with a pending account alias can see and generate QR in account settings", async () => {
  const mount = await mountPage();
  assert.ok(mount.textContent.includes("微信尚未绑定完成"));
  assert.ok(mount.textContent.includes("ClassClaw Channels"));
  const webChannel = mount.querySelector(".classclaw-web-channel");
  assert.ok(webChannel.textContent.includes("默认启用"));
  assert.equal(webChannel.querySelector("a").getAttribute("href"), "#/agent");
  assert.ok(mount.querySelector(".account-channels").querySelector(".qr-panel"));
  assert.equal(mount.querySelector(".account-agent-settings").querySelector(".qr-panel"), null);
  assert.ok(mount.querySelector("form"));
  assert.ok(button(mount, "模型设置"));
  assert.equal(calls.some(({ path }) => path.endsWith("/start")), false);
  button(mount, "绑定微信 / 重新生成二维码").click(); await tick();
  assert.equal(mount.querySelector("img").src, qr);
  assert.equal(mount.querySelector("img").classList.contains("hidden"), false);
  assert.equal(JSON.parse(calls.at(-1).options.body).force, true);
  assert.ok(calls.every(({ path }) => path.includes("/classes/class-a/")));
});

test("linked teachers retain an explicit rebind action without automatically starting login", async () => {
  binding.status = "linked";
  const mount = await mountPage();
  assert.ok(mount.textContent.includes("微信已绑定"));
  assert.ok(button(mount, "绑定微信 / 重新生成二维码"));
  assert.equal(calls.length, 1);
});

test("admin and teacher without a class never request class binding APIs", async () => {
  state.user.role = "admin";
  const admin = await mountPage();
  assert.equal(admin.querySelector(".account-agent-settings"), null);
  assert.ok(admin.querySelector("form"));
  page.dispose(); admin.remove();
  state.user.role = "head_teacher";
  state.classId = null;
  const teacher = await mountPage();
  assert.ok(teacher.textContent.includes("尚未创建班级"));
  assert.equal(calls.length, 0);
});

test("provisioning occurs in account settings and only reloads the Agent section", async () => {
  binding.openclaw_agent_id = null;
  const fallback = fetch;
  globalThis.fetch = async (path, options) => {
    if (path.endsWith("/provision")) { binding.openclaw_agent_id = "agent-a"; return response(binding); }
    return fallback(path, options);
  };
  const mount = await mountPage();
  const password = mount.querySelector("input");
  password.value = "unsaved-password-input";
  button(mount, "创建班级 Agent").click(); await tick();
  assert.ok(mount.querySelector(".qr-panel"));
  assert.equal(mount.querySelector("input"), password);
  assert.equal(password.value, "unsaved-password-input");
});

test("Gateway errors leave account info and password form usable", async () => {
  globalThis.fetch = async () => new Response(JSON.stringify({ success: false, error: { code: "OPENCLAW_ADMIN_UNAVAILABLE", message: "智能服务暂时不可用" } }), { status: 503 });
  const mount = await mountPage();
  assert.ok(mount.querySelector("form"));
  assert.ok(mount.textContent.includes("teacher"));
  assert.ok(mount.querySelector(".account-agent-settings").textContent.includes("智能服务暂时不可用"));
});

test("leaving during QR setup aborts only the binding request and ignores its late response", async (t) => {
  const fallback = fetch;
  let finish;
  let signal;
  globalThis.fetch = (path, options) => {
    if (path.endsWith("/start")) {
      signal = options.signal;
      return new Promise((resolve) => { finish = resolve; });
    }
    return fallback(path, options);
  };
  const mount = await mountPage();
  button(mount, "绑定微信 / 重新生成二维码").click();
  page.dispose(); mount.remove();
  assert.equal(signal.aborted, true);
  const text = mount.textContent;
  finish(response({ connected: true, route_ready: true })); await tick();
  t.mock.timers.tick(10000); await tick();
  assert.equal(mount.textContent, text);
  assert.equal(calls.length, 1);
});

test("model settings are scoped to the class and the dialog closes on page disposal", async () => {
  const mount = await mountPage();
  button(mount, "模型设置").click(); await tick();
  assert.ok(document.body.querySelector(".modal-overlay"));
  assert.ok(calls.some(({ path }) => path === "/api/v1/classes/class-a/agent-chat/models"));
  page.dispose();
  assert.equal(document.body.querySelector(".modal-overlay"), null);
});

test("late model settings cannot open over another account or page", async () => {
  const fallback = fetch;
  let finish;
  globalThis.fetch = (path, options) => path.endsWith("/agent-chat/models")
    ? new Promise((resolve) => { finish = resolve; }) : fallback(path, options);
  const mount = await mountPage();
  button(mount, "模型设置").click();
  page.dispose(); mount.remove();
  state.user = { id: "teacher-b", username: "another", role: "head_teacher" };
  state.classId = "class-b";
  finish(response(modelSettings)); await tick();
  assert.equal(document.body.querySelector(".modal-overlay"), null);
});

test("opening and leaving account settings never interrupts a sent Agent conversation", async () => {
  const fallback = fetch;
  let finish;
  let signal;
  globalThis.fetch = (path, options) => {
    if (path.includes("/openclaw/status")) return Promise.resolve(response({ ready: true, gateway_live: true, plugin_ready: true }));
    if (path.endsWith("/agent-chat/messages")) {
      signal = options.signal;
      return new Promise((resolve) => { finish = resolve; });
    }
    return fallback(path, options);
  };
  const chatMount = document.createElement("main");
  document.body.append(chatMount);
  await agentPage.render(chatMount, {}, { refreshOpenclawDot() {} });
  chatMount.querySelector("textarea").value = "切换到账户设置";
  chatMount.querySelector(".agent-chat-send").click();
  agentPage.dispose(); chatMount.remove();
  const accountMount = await mountPage();
  assert.equal(signal.aborted, false);
  page.dispose(); accountMount.remove();
  finish(response({ reply: "后台完成" })); await tick();
  assert.equal(signal.aborted, false);
  const scope = agentChatStore.getScope("teacher-a", "class-a");
  assert.equal(scope.conversations[0].messages.at(-1).text, "后台完成");
});
