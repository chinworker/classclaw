import test, { beforeEach, afterEach } from "node:test";
import assert from "node:assert/strict";
import { installDom, tick, response } from "./dom.mjs";

installDom();
const page = await import("../../web/js/pages/broadcast.js");
const { state, saveSession } = await import("../../web/js/state.js");

const STUDENTS = [
  { id: "s1", student_no: "001", name: "张三", class_id: "class-a", status: "active" },
  { id: "s2", student_no: "002", name: "李四", class_id: "class-a", status: "active" },
  { id: "s10", student_no: "010", name: "周十", class_id: "class-a", status: "active" },
];

let calls;
let device;
let previewPayload;
let sentPayload;
let detailPayload;
let warnings;
let advanceTimers;

// 轮询用 window.setTimeout，防抖用全局 setTimeout；installDom 捕获的是真实实现，
// 这里给 window 换上一个可推进的假时钟。
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

function statusPayload() {
  return {
    class_id: "class-a",
    server_time: "2026-09-23T10:00:00+08:00",
    device,
    device_warnings: warnings,
    camera: { registered: false, connected: false, camera: null, note: "本班尚未连接摄像头" },
    streaming: null,
    media_provider: "none",
    recent_broadcasts: [],
    note: device ? (device.online ? "教室终端在线" : "教室终端离线") : "本班尚未配对教室终端",
  };
}

function onlineDevice() {
  return {
    device_id: "dev-1", class_id: "class-a", name: "教室终端", pairing_status: "paired", online: true,
    app_version: "1.0.0", os_version: "Windows 10", protocol_version: 1, config_revision: 1,
    capabilities: { display: true, speak: true, volume_control: true, chinese_tts: true, capture: true, displays: ["教室一体机", "外接投影"] },
    inventory: { cameras: [{ name: "USB 摄像头", identifier: "usb-cam-1" }], microphones: [], speakers: [{ name: "教室音箱" }] },
    display_name: "教室一体机", audio_output_name: "教室音箱", volume_level: 40, muted: false,
    last_seen_at: "2026-09-23T09:59:50+08:00", last_error: null,
  };
}

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

function checkbox(mount, label) {
  return mount.querySelectorAll("input").find((node) => node.getAttribute("aria-label") === label);
}

// 浏览器点选复选框会同时触发 click 与 change；测试替身只派发 click，所以这里补上 change。
function choose(mount, label) {
  const box = checkbox(mount, label);
  box.checked = !box.checked;
  box.dispatchEvent(new Event("change"));
  return box;
}

function postBody(path) {
  const call = calls.find((row) => row.path === path && row.options.method === "POST");
  return call ? JSON.parse(call.options.body) : null;
}

function lastPostBody(path) {
  const matching = calls.filter((row) => row.path === path && row.options.method === "POST");
  return matching.length ? JSON.parse(matching[matching.length - 1].options.body) : null;
}

beforeEach((t) => {
  t.mock.timers.enable({ apis: ["setTimeout"] });
  installWindowClock();
  page.dispose();
  document.body.replaceChildren();
  state.user = { id: "teacher-a", username: "teacher", role: "head_teacher" };
  state.classId = "class-a";
  state.students = [];
  saveSession("test-token");
  calls = [];
  device = onlineDevice();
  warnings = [];
  previewPayload = { class_id: "class-a", mode: "three_part", texts: ["请张三同学现在去扫地。"], text: "请张三同学现在去扫地。",
    segment_count: 1, recipients: [{ student_no: "001", name: "张三" }], merge_mode: "combined",
    display_seconds: 15, repeat_count: 1, gap_seconds: 0, volume: null, target_screen: null, warnings: [], device_online: true };
  sentPayload = { broadcast_id: "b-1", class_id: "class-a", mode: "three_part", merge_mode: "combined",
    texts: ["请张三同学现在去扫地。"], segments: [{ index: 0, text: "请张三同学现在去扫地。", recipients: [{ student_no: "001", name: "张三" }] }],
    display_status: "authorized", speak_status: "authorized", status_note: "已下发教室终端，尚未收到显示与播报回执",
    created_by: "teacher", created_at: "2026-09-23T10:00:01+08:00", finished_at: null, command: { command_id: "c-1", status: "authorized" } };
  detailPayload = { ...sentPayload, display_status: "succeeded", speak_status: "succeeded",
    status_note: "教室屏幕已显示，扬声器已播报" };

  globalThis.fetch = async (path, options = {}) => {
    calls.push({ path, options });
    if (path.endsWith("/classroom/status")) return response(statusPayload());
    if (path.startsWith("/api/v1/students?")) return response({ items: STUDENTS, total: STUDENTS.length, page: 1, page_size: 100 });
    if (path.endsWith("/broadcasts/preview")) return response({ ...previewPayload, warnings, device_online: device?.online ?? false });
    if (path.endsWith("/classroom/broadcasts")) return response(sentPayload, 201);
    if (path.includes("/broadcasts?")) return response({ items: [sentPayload], total: 1, page: 1, page_size: 10 });
    if (path.endsWith("/broadcasts/b-1")) return response(detailPayload);
    throw new Error(`Unexpected request: ${path}`);
  };
});

afterEach(async () => {
  page.dispose();
  await tick();
});

test("输入变化立即阻止发送，迟到的旧预览不能覆盖新预览", async (t) => {
  const fetch = globalThis.fetch;
  const pending = [];
  globalThis.fetch = (path, options) => path.endsWith("/broadcasts/preview")
    ? new Promise((resolve) => pending.push(resolve)) : fetch(path, options);
  const mount = await mountPage();
  choose(mount, "001 张三");
  t.mock.timers.tick(400);
  await tick();
  assert.equal(pending.length, 1);
  button(mount, "来老师办公室").click();
  assert.equal(button(mount, "显示并播报").disabled, true);
  t.mock.timers.tick(400);
  await tick();
  assert.equal(pending.length, 2);
  pending[1](response({ ...previewPayload, texts: ["最新预览：来办公室"] }));
  await tick();
  await tick();
  assert.match(mount.textContent, /最新预览：来办公室/);
  pending[0](response({ ...previewPayload, texts: ["旧预览：去扫地"] }));
  await tick();
  await tick();
  assert.equal(mount.textContent.includes("旧预览：去扫地"), false);
  choose(mount, "001 张三");
  assert.equal(button(mount, "显示并播报").disabled, true);
});

test("离线终端禁止发送并说明原因", async () => {
  device = { ...onlineDevice(), online: false, last_seen_at: null };
  const mount = await mountPage();
  assert.equal(button(mount, "显示并播报").disabled, true);
  assert.match(mount.textContent, /教室终端离线/);
});

test("未配对时引导去教室设备页", async () => {
  device = null;
  const mount = await mountPage();
  assert.match(mount.textContent, /本班尚未配对教室终端/);
  assert.ok(mount.querySelectorAll("a").some((node) => node.getAttribute("href") === "#/classroom"));
});

test("选中学生后按服务端预览渲染冻结句子", async (t) => {
  const mount = await mountPage();
  t.mock.timers.tick(400);
  await tick();
  await tick();
  assert.match(mount.textContent, /请先选择学生/);

  choose(mount, "001 张三");
  await tick();
  t.mock.timers.tick(400);
  await tick();
  await tick();

  const body = postBody("/api/v1/classes/class-a/classroom/broadcasts/preview");
  assert.equal(body.mode, "three_part");
  assert.deepEqual(body.student_ids, ["s1"]);
  assert.equal(body.salutation, "同学");
  assert.equal(body.time_phrase, "现在");
  assert.match(mount.textContent, /请张三同学现在去扫地。/);
  assert.match(mount.textContent, /对象：001 张三/);
  assert.equal(button(mount, "显示并播报").disabled, false);
});

test("名单按学号自然升序，10 号不排在 2 号前面", async () => {
  const mount = await mountPage();
  const labels = mount.querySelectorAll("input")
    .filter((node) => /^\d{3} /.test(node.getAttribute("aria-label") || ""))
    .map((node) => node.getAttribute("aria-label"));
  assert.deepEqual(labels, ["001 张三", "002 李四", "010 周十"]);
});

test("快捷选项写入时间与事项并重新预览", async (t) => {
  const mount = await mountPage();
  choose(mount, "002 李四");
  button(mount, "下课后").click();
  button(mount, "来老师办公室").click();
  await tick();
  t.mock.timers.tick(400);
  await tick();
  await tick();
  const body = postBody("/api/v1/classes/class-a/classroom/broadcasts/preview");
  assert.equal(body.time_phrase, "下课后");
  assert.equal(body.predicate, "来老师办公室");
  assert.deepEqual(body.student_ids, ["s2"]);
});

test("发送使用与预览相同的字段，并轮询真实回执", async (t) => {
  const mount = await mountPage();
  choose(mount, "001 张三");
  await tick();
  t.mock.timers.tick(400);
  await tick();
  await tick();

  button(mount, "显示并播报").click();
  await tick();
  await tick();

  const body = postBody("/api/v1/classes/class-a/classroom/broadcasts");
  assert.deepEqual(body, { ...postBody("/api/v1/classes/class-a/classroom/broadcasts/preview"), expected_texts: previewPayload.texts });
  assert.match(mount.textContent, /已下发教室终端，尚未收到显示与播报回执/);

  // 未终结时继续轮询，拿到回执后停止。
  advanceTimers(2000);
  await tick();
  await tick();
  assert.ok(calls.some((row) => row.path.endsWith("/broadcasts/b-1")));
  assert.match(mount.textContent, /教室屏幕已显示，扬声器已播报/);
  const polls = calls.filter((row) => row.path.endsWith("/broadcasts/b-1")).length;
  advanceTimers(20000);
  await tick();
  await tick();
  assert.equal(calls.filter((row) => row.path.endsWith("/broadcasts/b-1")).length, polls);
});

test("回执未终结时持续轮询，终结后停止", async (t) => {
  const mount = await mountPage();
  choose(mount, "001 张三");
  await tick();
  t.mock.timers.tick(400);
  await tick();
  await tick();

  button(mount, "显示并播报").click();
  await tick();
  await tick();
  const polls = () => calls.filter((row) => row.path.endsWith("/broadcasts/b-1")).length;
  assert.equal(polls(), 1);
  assert.match(mount.textContent, /教室屏幕已显示，扬声器已播报/);

  advanceTimers(20000);
  await tick();
  await tick();
  assert.equal(polls(), 1);

  // 回执尚未终结时才会继续轮询。
  detailPayload = { ...detailPayload, display_status: "authorized", speak_status: "authorized",
    status_note: "已下发教室终端，尚未收到显示与播报回执" };
  button(mount, "显示并播报").click();
  await tick();
  await tick();
  const pending = polls();
  advanceTimers(2000);
  await tick();
  await tick();
  assert.ok(polls() > pending);
  assert.match(mount.textContent, /尚未收到显示与播报回执/);
});

test("自定义句子模式不选人也不套模板", async (t) => {
  const text = "请今天负责卫生的同学现在带好工具到教室后门集合。";
  previewPayload = { ...previewPayload, mode: "custom", texts: [text], text, recipients: [], segment_count: 1 };
  const mount = await mountPage();
  const customButton = button(mount, "自定义句子");
  customButton.click();
  await tick();
  assert.equal(customButton.getAttribute("aria-pressed"), "true");
  assert.equal(button(mount, "三段式").getAttribute("aria-pressed"), "false");

  const textarea = mount.querySelectorAll("textarea").find((node) => node.getAttribute("aria-label") === "自定义句子");
  textarea.value = text;
  textarea.dispatchEvent(new Event("input"));
  await tick();
  t.mock.timers.tick(400);
  await tick();
  await tick();

  const sent = lastPostBody("/api/v1/classes/class-a/classroom/broadcasts/preview");
  assert.equal(sent.mode, "custom");
  assert.equal(sent.text, text);
  assert.equal(sent.student_ids, undefined);
  assert.match(mount.textContent, /请今天负责卫生的同学现在带好工具到教室后门集合。/);
});

test("逐人播报切换后重新预览", async (t) => {
  previewPayload = { ...previewPayload, merge_mode: "per_student", segment_count: 2,
    texts: ["请张三同学现在去扫地。", "请李四同学现在去扫地。"],
    recipients: [{ student_no: "001", name: "张三" }, { student_no: "002", name: "李四" }] };
  const mount = await mountPage();
  choose(mount, "001 张三");
  choose(mount, "002 李四");
  const perStudent = mount.querySelectorAll("input").find((node) => node.getAttribute("name") === "merge-mode" && node.value === "per_student");
  perStudent.checked = true;
  perStudent.dispatchEvent(new Event("change"));
  await tick();
  t.mock.timers.tick(400);
  await tick();
  await tick();
  const sent = lastPostBody("/api/v1/classes/class-a/classroom/broadcasts/preview");
  assert.equal(sent.merge_mode, "per_student");
  assert.deepEqual(sent.student_ids, ["s1", "s2"]);
  assert.match(mount.textContent, /2 句，逐人/);
});

test("能力缺失时明确提示而不是静默发送", async (t) => {
  warnings = ["终端未确认可用中文语音，请先在 Windows 安装中文 TTS"];
  const mount = await mountPage();
  choose(mount, "001 张三");
  await tick();
  t.mock.timers.tick(400);
  await tick();
  await tick();
  assert.match(mount.textContent, /安装中文 TTS/);
});

test("预览失败时禁用发送并显示原因", async (t) => {
  globalThis.fetch = async (path, options = {}) => {
    calls.push({ path, options });
    if (path.endsWith("/classroom/status")) return response(statusPayload());
    if (path.startsWith("/api/v1/students?")) return response({ items: STUDENTS, total: 3, page: 1, page_size: 100 });
    if (path.endsWith("/broadcasts/preview")) {
      return new Response(JSON.stringify({ success: false, error: { code: "BROADCAST_TOO_LONG", message: "广播句子最多 160 字" } }), { status: 422 });
    }
    if (path.includes("/broadcasts?")) return response({ items: [], total: 0, page: 1, page_size: 10 });
    throw new Error(`Unexpected request: ${path}`);
  };
  const mount = await mountPage();
  choose(mount, "001 张三");
  await tick();
  t.mock.timers.tick(400);
  await tick();
  await tick();
  assert.match(mount.textContent, /广播句子最多 160 字/);
  assert.equal(button(mount, "显示并播报").disabled, true);
});

test("离页停止轮询，不再更新已卸载的视图", async (t) => {
  const mount = await mountPage();
  choose(mount, "001 张三");
  await tick();
  t.mock.timers.tick(400);
  await tick();
  await tick();
  detailPayload = { ...detailPayload, display_status: "authorized", speak_status: "authorized" };
  button(mount, "显示并播报").click();
  await tick();
  await tick();
  const before = calls.filter((row) => row.path.endsWith("/broadcasts/b-1")).length;

  page.dispose();
  mount.remove();
  advanceTimers(20000);
  await tick();
  await tick();
  assert.equal(calls.filter((row) => row.path.endsWith("/broadcasts/b-1")).length, before);
});
