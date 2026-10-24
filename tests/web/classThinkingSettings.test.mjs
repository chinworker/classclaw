import assert from "node:assert/strict";
import { afterEach, test } from "node:test";
import { installDom, response, tick } from "./dom.mjs";
import { classThinkingSettings } from "../../web/js/classThinkingSettings.js";

let panel;
afterEach(() => panel?.dispose());
const button = () => panel.el.querySelectorAll("button").find((node) => node.textContent === "保存默认思考强度");
const profile = { levels: [{ id: "off", label: "关闭" }, { id: "low", label: "开启" }],
  class_default_level: null, system_default_level: "off", default_level: "off" };

test("class default offers only Gateway choices and submits the level id with its binary label", async () => {
  installDom(); const calls = [];
  globalThis.fetch = async (path, options = {}) => { calls.push({ path, options }); return response(options.method === "PATCH" ? {} : profile); };
  panel = classThinkingSettings("class-a"); document.body.append(panel.el); await panel.load();
  const select = panel.el.querySelector("select");
  assert.deepEqual(select.querySelectorAll("option").map((node) => node.value), ["", "off", "low"]);
  assert.equal(select.querySelectorAll("option")[2].textContent, "开启");
  select.value = "low"; select.dispatchEvent(new Event("change")); button().click(); await tick();
  assert.equal(calls.at(-1).path, "/api/v1/classes/class-a/agent-chat/thinking");
  assert.deepEqual(JSON.parse(calls.at(-1).options.body), { thinking_default: "low" });
  assert.equal(panel.isDirty(), false);
  select.value = ""; select.dispatchEvent(new Event("change")); button().click(); await tick();
  assert.deepEqual(JSON.parse(calls.at(-1).options.body), { thinking_default: null });
});

test("stale defaults are visible and a rejected save retains the selection", async () => {
  installDom();
  globalThis.fetch = async (_path, options) => options.method === "PATCH"
    ? new Response(JSON.stringify({ success: false, error: { code: "CHAT_THINKING_UNSUPPORTED", message: "模型已变更，请重新选择" } }), { status: 422 })
    : response({ ...profile, class_default_level: "high", default_adjusted: true });
  panel = classThinkingSettings("class-a"); document.body.append(panel.el); await panel.load();
  assert.ok(panel.el.textContent.includes("不受当前模型支持"));
  const select = panel.el.querySelector("select");
  select.value = "low"; select.dispatchEvent(new Event("change")); button().click(); await tick();
  assert.equal(select.value, "low"); assert.equal(panel.isDirty(), true);
  assert.ok(panel.el.textContent.includes("模型已变更"));
  assert.equal(button().disabled, false);
});

test("leaving during capability loading aborts the view request and ignores late results", async () => {
  installDom(); let finish, signal;
  globalThis.fetch = (_path, options) => { signal = options.signal; return new Promise((resolve) => { finish = resolve; }); };
  panel = classThinkingSettings("class-a"); document.body.append(panel.el);
  const pending = panel.load(); panel.dispose();
  assert.equal(signal.aborted, true);
  finish(response(profile)); await pending;
  assert.equal(panel.el.querySelector("select"), null);
});

test("class settings exposes the default thinking panel independently of basic class fields", async () => {
  installDom();
  const page = await import("../../web/js/pages/classSettings.js");
  const { state, saveSession } = await import("../../web/js/state.js");
  state.classId = "class-a"; saveSession("test-token");
  const calls = [];
  globalThis.fetch = async (path) => { calls.push(path); return response(path.endsWith("/thinking") ? profile : { id: "class-a", name: "测试班", grade: "高一", status: "active" }); };
  const mount = document.createElement("main"); document.body.append(mount);
  try {
    await page.render(mount, {}, {}); await tick();
    assert.ok(mount.querySelector(".class-thinking-select"));
    assert.ok(mount.textContent.includes("保存班级资料"));
    assert.ok(calls.includes("/api/v1/classes/class-a/agent-chat/thinking"));
  } finally { page.dispose(); }
});
