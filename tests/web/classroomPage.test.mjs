import test, { beforeEach, afterEach } from "node:test";
import assert from "node:assert/strict";
import { installDom, tick, response } from "./dom.mjs";

installDom();
const page = await import("../../web/js/pages/classroom.js");
const { state, saveSession } = await import("../../web/js/state.js");

let calls;
let statusPayload;
let leasePayload;
let advanceTimers;

// 页面轮询用 window.setTimeout；installDom 捕获的是真实实现，这里换成可推进的假时钟。
function installWindowClock() {
  let clock = 0;
  let timers = [];
  let nextId = 1;
  window.setTimeout = (fn, ms = 0) => {
    const id = nextId;
    nextId += 1;
    timers.push({ id, fn, at: clock + ms });
    return id;
  };
  window.clearTimeout = (id) => { timers = timers.filter((row) => row.id !== id); };
  advanceTimers = (ms) => {
    clock += ms;
    const due = timers.filter((row) => row.at <= clock).sort((a, b) => a.at - b.at);
    timers = timers.filter((row) => row.at > clock);
    for (const row of due) row.fn();
  };
}

function onlineDevice(overrides = {}) {
  return {
    device_id: "dev-1", class_id: "class-a", name: "教室终端", pairing_status: "paired", online: true,
    pairing_code: null, pairing_expires_at: null, app_version: "1.0.0", os_version: "Windows 10 教育版",
    protocol_version: 1, config_revision: 3,
    capabilities: { display: true, speak: true, volume_control: true, chinese_tts: true, capture: true, displays: ["教室一体机", "外接投影"] },
    inventory: {
      cameras: [{ name: "USB 摄像头", identifier: "usb-cam-1" }],
      microphones: [{ name: "内置麦克风", identifier: "mic-1" }],
      speakers: [{ name: "教室音箱", identifier: "spk-1" }],
    },
    display_name: "教室一体机", audio_output_name: "教室音箱", volume_level: 40, muted: false,
    last_seen_at: "2026-09-23T09:59:50+08:00", last_error: null, ...overrides,
  };
}

function status({ device = onlineDevice(), camera = null, viewers = 0 } = {}) {
  return {
    class_id: "class-a", server_time: "2026-09-23T10:00:00+08:00", device,
    device_warnings: device && !device.capabilities?.chinese_tts ? ["终端未确认可用中文语音"] : [],
    camera: camera
      ? { registered: true, connected: camera.status === "connected", camera, device_online: true, note: "摄像头画面可用" }
      : { registered: false, connected: false, camera: null, note: "本班尚未连接摄像头；连接只能从网页发起，每班最多一路" },
    streaming: camera ? { viewers, keep_stream_up: viewers > 0, grace_remaining_seconds: null, provider: "none" } : null,
    media_provider: "none", recent_broadcasts: [],
    note: device ? (device.online ? "教室终端在线" : "教室终端离线") : "本班尚未配对教室终端",
  };
}

const CAMERA = {
  camera_id: "cam-1", class_id: "class-a", name: "教室摄像头", access_path: "windows_capture",
  source_kind: "windows_device", device_id: "dev-1", device_identifier: "usb-cam-1", device_label: "USB 摄像头",
  protocol: null, location: null, credential_configured: false, video: {}, audio_capable: true,
  status: "connected", config_revision: 2, connected_at: "2026-09-23T09:00:00+08:00",
  disconnected_at: null, last_error: null, updated_at: "2026-09-23T09:00:00+08:00",
};

async function mountPage() {
  const mount = document.createElement("main");
  document.body.append(mount);
  await page.render(mount);
  await tick();
  return mount;
}

function button(mount, text) {
  return mount.querySelectorAll("button").find((node) => node.textContent === text);
}

function body(path, method = "POST") {
  const call = calls.find((row) => row.path === path && (row.options.method || "GET") === method);
  return call ? JSON.parse(call.options.body || "{}") : null;
}

beforeEach((t) => {
  t.mock.timers.enable({ apis: ["setTimeout"] });
  installWindowClock();
  page.dispose();
  document.body.replaceChildren();
  state.user = { id: "teacher-a", username: "teacher", role: "head_teacher" };
  state.classId = "class-a";
  saveSession("test-token");
  calls = [];
  statusPayload = status();
  leasePayload = {
    session_id: "sess-12345678", camera_id: "cam-1", class_id: "class-a", token: "once-only-token",
    video_allowed: true, audio_allowed: false, audio_unavailable_reason: "该摄像头没有可用音轨",
    expires_at: "2026-09-23T10:05:00+08:00", lease_seconds: 300, viewers: 1,
    available: false, provider: "none", reason: "MEDIA_NOT_CONFIGURED",
    message: "尚未接入媒体转发组件，当前只能查看终端与摄像头状态，不能显示画面",
  };

  globalThis.fetch = async (path, options = {}) => {
    calls.push({ path, options });
    if (path.endsWith("/classroom/status")) return response(statusPayload);
    if (path.endsWith("/classroom/pairing")) {
      return options.method === "DELETE"
        ? response(status().device)
        : response({ ...onlineDevice(), pairing_status: "unpaired", pairing_code: "K7M2-P9QX",
            pairing_expires_at: "2026-09-23T10:10:00+08:00", pairing_ttl_seconds: 600 }, 201);
    }
    if (path.endsWith("/classroom/device/output")) return response({ note: "已下发输出设备设置", command: {} });
    if (path.endsWith("/classroom/volume")) return response({ note: "已登记音量指令", command_id: "c-1", requested_volume: 55 });
    if (path.endsWith("/classroom/device/test")) return response({ text: "ClassClaw 教室终端显示测试", warnings: [], command: {} });
    if (path.endsWith("/classroom/camera")) return response({ ...CAMERA, replaced: false, old_connection_stopped: true, capture_started: true, revoked_media_sessions: 0 }, 201);
    if (path.endsWith("/classroom/media-sessions")) return response(leasePayload, 201);
    if (path.includes("/media-sessions/")) return response({ ...leasePayload, viewers: 0 });
    throw new Error(`Unexpected request: ${path}`);
  };
});

afterEach(async () => {
  page.dispose();
  await tick();
});

test("离页期间创建成功的观看租约立即释放", async () => {
  statusPayload = status({ camera: CAMERA });
  const fetch = globalThis.fetch;
  let finish;
  globalThis.fetch = (path, options) => path.endsWith("/classroom/media-sessions")
    ? new Promise((resolve) => { finish = resolve; }) : fetch(path, options);
  const mount = await mountPage();
  button(mount, "开始观看").click();
  await tick();
  page.dispose();
  finish(response(leasePayload, 201));
  await tick();
  await tick();
  assert.equal(calls.filter((row) => row.path.endsWith("/media-sessions/sess-12345678") && row.options.method === "DELETE").length, 1);
});

test("已有观看租约时不能重复申请并遗留旧租约", async () => {
  statusPayload = status({ camera: CAMERA });
  const mount = await mountPage();
  button(mount, "开始观看").click();
  await tick();
  await tick();
  assert.equal(button(mount, "开始观看").disabled, true);
  button(mount, "开始观看").click();
  await tick();
  assert.equal(calls.filter((row) => row.path.endsWith("/classroom/media-sessions") && row.options.method === "POST").length, 1);
});

test("未配对时只能生成一次性配对码", async () => {
  statusPayload = status({ device: null });
  const mount = await mountPage();
  assert.match(mount.textContent, /本班还没有教室终端/);
  assert.equal(button(mount, "生成配对码") !== undefined, true);

  button(mount, "生成配对码").click();
  await tick();
  await tick();
  assert.equal(calls.filter((row) => row.path.endsWith("/classroom/pairing")).length, 1);
  assert.match(mount.textContent, /K7M2-P9QX/);
  assert.match(mount.textContent, /只显示一次/);
});

test("终端离线时音量与测试按钮禁用", async () => {
  statusPayload = status({ device: onlineDevice({ online: false, last_seen_at: null }) });
  const mount = await mountPage();
  assert.match(mount.textContent, /教室终端离线/);
  assert.equal(button(mount, "下发音量").disabled, true);
  assert.equal(button(mount, "显示测试").disabled, true);
});

test("展示终端上报的设备清单与能力警告", async () => {
  statusPayload = status({ device: onlineDevice({ capabilities: { display: true, speak: true, volume_control: true, chinese_tts: false, capture: true, displays: ["教室一体机"] } }) });
  const mount = await mountPage();
  assert.match(mount.textContent, /USB 摄像头/);
  assert.match(mount.textContent, /内置麦克风/);
  assert.match(mount.textContent, /教室音箱/);
  assert.match(mount.textContent, /终端未确认可用中文语音/);
});

test("下发输出设备设置只提交已选择的项", async () => {
  const mount = await mountPage();
  const display = mount.querySelectorAll("select").find((node) => node.getAttribute("aria-label") === "目标显示屏");
  display.value = "外接投影";
  button(mount, "下发输出设备设置").click();
  await tick();
  await tick();
  assert.deepEqual(body("/api/v1/classes/class-a/classroom/device/output"), { display_name: "外接投影" });
});

test("音量与测试指令走同一批接口并提示以回执为准", async () => {
  const mount = await mountPage();
  const slider = mount.querySelectorAll("input").find((node) => node.getAttribute("aria-label") === "教室系统音量");
  slider.value = "55";
  slider.dispatchEvent(new Event("input"));
  button(mount, "下发音量").click();
  await tick();
  await tick();
  assert.deepEqual(body("/api/v1/classes/class-a/classroom/volume"), { volume: 55, mute: false });

  button(mount, "显示测试").click();
  await tick();
  await tick();
  assert.deepEqual(body("/api/v1/classes/class-a/classroom/device/test"), { kind: "display" });
  // 页面常驻说明：下发成功不等于已生效。
  assert.match(mount.textContent, /设备实际值以终端回执为准，不将下发成功当作已生效/);
});

test("连接摄像头提交接入路径，更换时带上当前配置版本", async () => {
  const mount = await mountPage();
  assert.match(mount.textContent, /本班尚未连接摄像头/);
  const pathSelect = mount.querySelectorAll("select").find((node) => node.getAttribute("aria-label") === "接入路径");
  const kindSelect = mount.querySelectorAll("select").find((node) => node.getAttribute("aria-label") === "来源类型");
  // 测试替身不会自动选中第一个 option，真实浏览器会。
  pathSelect.value = "windows_capture";
  kindSelect.value = "windows_device";
  const identifier = mount.querySelectorAll("input").find((node) => node.getAttribute("aria-label") === "设备标识");
  identifier.value = "usb-cam-1";
  button(mount, "连接摄像头").click();
  await tick();
  await tick();
  const sent = body("/api/v1/classes/class-a/classroom/camera");
  assert.equal(sent.access_path, "windows_capture");
  assert.equal(sent.source_kind, "windows_device");
  assert.equal(sent.device_identifier, "usb-cam-1");
  assert.equal(sent.expected_revision, undefined);

  // 已登记后按钮变成更换，并带上服务端返回的配置版本。
  statusPayload = status({ camera: CAMERA });
  const remount = await mountPage();
  assert.match(remount.textContent, /配置版本 2/);
  assert.equal(button(remount, "更换摄像头") !== undefined, true);
  const remountPath = remount.querySelectorAll("select").find((node) => node.getAttribute("aria-label") === "接入路径");
  const remountKind = remount.querySelectorAll("select").find((node) => node.getAttribute("aria-label") === "来源类型");
  remountPath.value = "windows_capture";
  remountKind.value = "windows_device";
  const remountIdentifier = remount.querySelectorAll("input").find((node) => node.getAttribute("aria-label") === "设备标识");
  remountIdentifier.value = "usb-cam-2";
  button(remount, "更换摄像头").click();
  await tick();
  await tick();
  const replaced = calls.filter((row) => row.path.endsWith("/classroom/camera") && row.options.method === "POST").pop();
  assert.equal(JSON.parse(replaced.options.body).expected_revision, 2);
});

test("没有摄像头时不提供观看入口", async () => {
  const mount = await mountPage();
  assert.match(mount.textContent, /尚未连接摄像头/);
  assert.equal(button(mount, "开始观看"), undefined);
});

test("观看会话明确说明未接入媒体转发，并不伪造画面", async () => {
  statusPayload = status({ camera: CAMERA, viewers: 1 });
  const mount = await mountPage();
  button(mount, "开始观看").click();
  await tick();
  await tick();
  const sent = body("/api/v1/classes/class-a/classroom/media-sessions");
  assert.deepEqual(sent, { audio: false });
  assert.match(mount.textContent, /尚未接入媒体转发组件/);
  assert.match(mount.textContent, /MEDIA_NOT_CONFIGURED/);
  assert.match(mount.textContent, /声音 未授权 · 该摄像头没有可用音轨/);
  assert.equal(mount.textContent.includes("once-only-token"), false);
});

test("停止观看与离页都会释放本人会话", async () => {
  statusPayload = status({ camera: CAMERA, viewers: 1 });
  const mount = await mountPage();
  button(mount, "开始观看").click();
  await tick();
  await tick();

  button(mount, "停止观看").click();
  await tick();
  await tick();
  const released = calls.filter((row) => row.path.endsWith("/media-sessions/sess-12345678") && row.options.method === "DELETE");
  assert.equal(released.length, 1);

  button(mount, "开始观看").click();
  await tick();
  await tick();
  page.dispose();
  await tick();
  const afterDispose = calls.filter((row) => row.path.endsWith("/media-sessions/sess-12345678") && row.options.method === "DELETE");
  assert.equal(afterDispose.length, 2);
});

test("轮询只更新概况，不重建正在填写的摄像头表单", async () => {
  const mount = await mountPage();
  const identifier = mount.querySelectorAll("input").find((node) => node.getAttribute("aria-label") === "设备标识");
  identifier.value = "usb-cam-9";
  advanceTimers(5000);
  await tick();
  await tick();
  const stillThere = mount.querySelectorAll("input").find((node) => node.getAttribute("aria-label") === "设备标识");
  assert.equal(stillThere.value, "usb-cam-9");
  assert.ok(calls.filter((row) => row.path.endsWith("/classroom/status")).length >= 2);

  // 离页后不再轮询。
  const before = calls.filter((row) => row.path.endsWith("/classroom/status")).length;
  page.dispose();
  advanceTimers(30000);
  await tick();
  assert.equal(calls.filter((row) => row.path.endsWith("/classroom/status")).length, before);
});
