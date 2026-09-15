import test, { beforeEach, afterEach } from "node:test";
import assert from "node:assert/strict";
import { installDom, tick, response } from "./dom.mjs";

installDom();
const { qrBindingPanel } = await import("../../web/js/components.js");
const { appConfig } = await import("../../web/js/config.js");
const { saveSession } = await import("../../web/js/state.js");
let panels;
const qrA = "data:image/png;base64,cXI=";
const qrB = "data:image/png;base64,bmV3";
const loginId = "22222222-2222-2222-2222-222222222222";

beforeEach((t) => {
  t.mock.timers.enable({ apis: ["setTimeout", "Date"] });
  document.body.replaceChildren();
  saveSession("test-qr-token");
  appConfig.features.wechat_binding = true;
  panels = [];
});
afterEach(() => { panels.forEach((panel) => panel.dispose()); });
function mountPanel(options = {}) {
  const panel = qrBindingPanel("class-a", options);
  panels.push(panel);
  document.body.append(panel.el);
  return panel;
}

test("waiting without a replacement QR preserves the image, then refreshes and finishes", async (t) => {
  const replies = [
    { connected: false, message: "等待扫码" },
    { connected: false, qr_data_url: qrB },
    { connected: true, route_ready: true, binding: { agent_name: "测试 Agent" } },
  ];
  let waits = 0;
  let done = 0;
  globalThis.fetch = async (path, options) => {
    if (path.endsWith("/start")) return response({ connected: false, qr_data_url: qrA, login_id: loginId });
    assert.deepEqual(JSON.parse(options.body), { login_id: loginId });
    return response(replies[waits++]);
  };
  const panel = mountPanel({ onDone: () => { done += 1; } });
  await panel.start();
  const img = panel.el.querySelector("img");
  assert.equal(img.src, qrA);
  t.mock.timers.tick(appConfig.wechat.qr_initial_poll_ms); await tick();
  assert.equal(img.src, qrA);
  assert.equal(img.classList.contains("hidden"), false);
  assert.equal(panel.el.querySelector(".field-error").classList.contains("hidden"), true);
  t.mock.timers.tick(appConfig.wechat.qr_poll_ms); await tick();
  assert.equal(img.src, qrB);
  t.mock.timers.tick(appConfig.wechat.qr_poll_ms); await tick();
  assert.equal(done, 1);
  assert.equal(img.classList.contains("hidden"), true);
  t.mock.timers.tick(10000); await tick();
  assert.equal(waits, 3);
});

test("regenerating ignores an older in-flight wait response", async (t) => {
  let oldWait;
  let oldSignal;
  let starts = 0;
  globalThis.fetch = async (path, options) => {
    if (path.endsWith("/start")) return response({ connected: false, qr_data_url: starts++ ? qrB : qrA });
    oldSignal = options.signal;
    return new Promise((resolve) => { oldWait = resolve; });
  };
  let done = 0;
  const panel = mountPanel({ onDone: () => { done += 1; } });
  await panel.start();
  t.mock.timers.tick(appConfig.wechat.qr_initial_poll_ms); await tick();
  await panel.start();
  assert.equal(oldSignal.aborted, true);
  oldWait(response({ connected: true, route_ready: true })); await tick();
  assert.equal(done, 0);
  assert.equal(panel.el.querySelector("img").src, qrB);
  assert.equal(panel.el.querySelector("img").classList.contains("hidden"), false);
});

test("expired provider session stops polling and offers regeneration", async (t) => {
  let waits = 0;
  globalThis.fetch = async (path) => {
    if (path.endsWith("/start")) return response({ connected: false, qr_data_url: qrA });
    waits += 1;
    return response({ connected: false, restart_required: true, message: "二维码已过期，请重新生成。" });
  };
  const panel = mountPanel();
  await panel.start();
  t.mock.timers.tick(appConfig.wechat.qr_initial_poll_ms); await tick();
  assert.equal(panel.el.querySelector("img").classList.contains("hidden"), true);
  assert.equal(panel.el.querySelector("button").disabled, false);
  assert.ok(panel.el.textContent.includes("请点击重新生成二维码"));
  t.mock.timers.tick(10000); await tick();
  assert.equal(waits, 1);
});

test("missing initial QR, image errors and disabled feature are visible and retryable", async () => {
  let calls = 0;
  globalThis.fetch = async () => { calls += 1; return response({ connected: false, qr_data_url: calls > 1 ? qrA : null }); };
  const panel = mountPanel();
  await panel.start();
  assert.ok(panel.el.textContent.includes("暂时没有拿到二维码"));
  assert.equal(panel.el.querySelector(".field-error").classList.contains("hidden"), false);
  await panel.start();
  panel.el.querySelector("img").onerror();
  assert.ok(panel.el.textContent.includes("二维码图片加载失败"));
  appConfig.features.wechat_binding = false;
  const disabled = mountPanel();
  await disabled.start();
  assert.equal(disabled.el.querySelector("button").disabled, true);
  assert.equal(calls, 2);
});

test("disposing during start ignores the late response and never starts polling", async (t) => {
  let finish;
  let signal;
  let calls = 0;
  globalThis.fetch = (path, options) => {
    calls += 1; signal = options.signal;
    return new Promise((resolve) => { finish = resolve; });
  };
  const panel = mountPanel();
  const pending = panel.start();
  panel.dispose();
  const text = panel.el.textContent;
  assert.equal(signal.aborted, true);
  finish(response({ connected: false, qr_data_url: qrA }));
  await pending;
  t.mock.timers.tick(10000); await tick();
  assert.equal(panel.el.textContent, text);
  assert.equal(calls, 1);
});

test("phone verification is entered in the page, scoped to the current challenge, and cleared immediately", async (t) => {
  const challenge = "33333333-3333-3333-3333-333333333333";
  const calls = [];
  globalThis.fetch = async (path, options) => {
    calls.push({ path, body: JSON.parse(options.body) });
    if (path.endsWith("/start")) return response({ login_id: loginId, connected: false, qr_data_url: qrA });
    if (path.endsWith("/verify")) return response({ login_id: loginId, connected: false, verification_required: false, message: "正在确认" });
    return response({ login_id: loginId, connected: false, verification_required: true, challenge_id: challenge, qr_data_url: qrA });
  };
  const panel = mountPanel();
  await panel.start();
  t.mock.timers.tick(appConfig.wechat.qr_initial_poll_ms); await tick();
  const form = panel.el.querySelector(".qr-verification");
  assert.equal(form.classList.contains("hidden"), false);
  const input = form.querySelector("input");
  input.value = "001234";
  // A status poll must not clear a partially typed code for the same challenge.
  t.mock.timers.tick(appConfig.wechat.qr_poll_ms); await tick();
  assert.equal(input.value, "001234");
  form.dispatchEvent(new Event("submit", { cancelable: true }));
  assert.equal(input.value, "");
  assert.equal(form.querySelector("button").disabled, true);
  await tick();
  assert.deepEqual(calls.at(-1).body, { login_id: loginId, challenge_id: challenge, code: "001234" });
  assert.equal(form.classList.contains("hidden"), true);
  assert.equal(panel.el.textContent.includes("001234"), false);
});

test("regeneration hides verification and ignores a late verification success", async (t) => {
  let finishVerify;
  let starts = 0;
  let done = 0;
  globalThis.fetch = async (path) => {
    if (path.endsWith("/start")) return response({ login_id: loginId, connected: false, qr_data_url: starts++ ? qrB : qrA });
    if (path.endsWith("/verify")) return new Promise((resolve) => { finishVerify = resolve; });
    return response({ login_id: loginId, connected: false, verification_required: true, challenge_id: "33333333-3333-3333-3333-333333333333" });
  };
  const panel = mountPanel({ onDone: () => { done++; } });
  await panel.start();
  t.mock.timers.tick(appConfig.wechat.qr_initial_poll_ms); await tick();
  const form = panel.el.querySelector(".qr-verification");
  form.querySelector("input").value = "0123";
  form.dispatchEvent(new Event("submit", { cancelable: true }));
  await panel.start();
  finishVerify(response({ connected: true, route_ready: true })); await tick();
  assert.equal(done, 0); assert.equal(form.classList.contains("hidden"), true);
  assert.equal(panel.el.querySelector("img").src, qrB);
});

test("invalid verification never submits, and page disposal erases typed digits", async (t) => {
  let verifies = 0;
  globalThis.fetch = async (path) => {
    if (path.endsWith("/verify")) verifies++;
    return response({ login_id: loginId, connected: false, qr_data_url: qrA, verification_required: true,
      challenge_id: "33333333-3333-3333-3333-333333333333" });
  };
  const panel = mountPanel();
  await panel.start();
  const form = panel.el.querySelector(".qr-verification");
  form.querySelector("input").value = "password";
  form.dispatchEvent(new Event("submit", { cancelable: true })); await tick();
  assert.equal(verifies, 0);
  form.querySelector("input").value = "1234";
  panel.dispose();
  assert.equal(form.querySelector("input").value, "");
});
