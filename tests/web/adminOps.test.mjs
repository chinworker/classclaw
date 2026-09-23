import assert from "node:assert/strict";
import { afterEach, test } from "node:test";
import { installDom, response, tick } from "./dom.mjs";
import { trendChart, barList } from "../../web/js/charts.js";
import { logViewer, logParams, logText } from "../../web/js/logViewer.js";
import { gatewayChanges, gatewaySettingsEditor } from "../../web/js/gatewaySettingsEditor.js";
import { proposalReview } from "../../web/js/components.js";
import { adminView } from "../../web/js/adminView.js";
import { agentPanel } from "../../web/js/adminAgentPanel.js";
import { ADMIN_ALIASES, ADMIN_NAV } from "../../web/js/adminRoutes.js";
import * as usage from "../../web/js/pages/adminUsage.js";
import * as access from "../../web/js/pages/adminAccess.js";
import * as settings from "../../web/js/pages/adminSettings.js";
import * as ops from "../../web/js/pages/adminOps.js";
import * as maintenance from "../../web/js/pages/adminMaintenance.js";
import { defineRoutes, setRouteResolver, dispatch, currentRoute, setNavigationGuard } from "../../web/js/router.js";

let viewer;
let view;
afterEach(() => { viewer?.dispose(); view?.dispose(); usage.dispose(); access.dispose(); settings.dispose(); ops.dispose(); maintenance.dispose(); });
const find = (tag, label, root = document.body) => root.querySelectorAll(tag).find((node) => node.getAttribute("aria-label") === label);
const button = (label) => document.body.querySelectorAll("button").find((node) => node.textContent === label);
const log = { timestamp: "2026-09-15T09:21:33.120+08:00", level: "ERROR", logger: "request", request_id: "req-a", message: "Failed" };

test("charts render empty states, single points and sorted distribution without NaN", () => {
  installDom();
  assert.ok(trendChart([]).textContent.includes("暂无"));
  assert.ok(barList([]).textContent.includes("暂无"));
  const chart = trendChart([{ date: "2026-09-15", requests: 1, total_tokens: 100 }]);
  assert.equal(chart.querySelectorAll("circle").length, 2);
  for (const node of chart.querySelectorAll("circle")) assert.equal(node.getAttribute("cx"), "380");
  const bars = barList([{ name: "small", tokens: 1 }, { name: "large", tokens: 9 }]);
  assert.ok(bars.textContent.startsWith("large90.0%"));
});

test("log query includes precise request filters, bounds, time and incremental cursor", () => {
  const params = logParams({ source: "openclaw", level: "ERROR", q: " fail ", request_id: "abc", range: "15m" }, { cursor: 42, fileId: "1:2", clock: 900_000 });
  assert.equal(params.get("request_id"), "abc");
  assert.equal(params.get("q"), "fail");
  assert.equal(params.get("cursor"), "42");
  assert.equal(params.get("since"), "1970-01-01T00:00:00.000Z");
  assert.ok(logText([log]).includes("req:req-a"));
});

test("request_id refills the filter and details drop level to show the request chain", async () => {
  installDom(); const calls = [];
  globalThis.fetch = async (url) => { calls.push(url); return response({ items: [log], cursor: 5, available: true }); };
  viewer = logViewer({ level: "ERROR" }); document.body.append(viewer.el); await viewer.load();
  button("req-a").click(); await tick();
  assert.equal(find("input", "request_id").value, "req-a");
  assert.ok(calls.at(-1).includes("request_id=req-a"));
  button("请求详情").click(); await tick();
  assert.equal(new URL(calls.at(-1), "http://test").searchParams.get("level"), null);
  assert.ok(document.body.querySelector(".drawer"));
});

test("real-time log refresh sends cursor and stops completely on dispose", async () => {
  installDom(); let scheduled; let count = 0; let lastUrl;
  window.setTimeout = (fn) => { scheduled = fn; return 1; }; window.clearTimeout = () => { scheduled = null; };
  globalThis.fetch = async (url) => { count++; lastUrl = url; return response({ items: [log], cursor: 17, file_id: "f", available: true }); };
  viewer = logViewer(); document.body.append(viewer.el); await viewer.load();
  const live = find("input", "实时刷新"); live.checked = true; live.dispatchEvent(new Event("change"));
  const callback = scheduled; callback(); await tick();
  assert.equal(count, 2); assert.ok(lastUrl.includes("cursor=17"));
  const late = scheduled;
  viewer.dispose(); assert.equal(scheduled, null);
  late(); await tick(); assert.equal(count, 2);
});

test("log requests in progress abort on page disposal and late results cannot render", async () => {
  installDom(); let finish; let signal;
  globalThis.fetch = (url, options) => { signal = options.signal; return new Promise((resolve) => { finish = resolve; }); };
  viewer = logViewer(); document.body.append(viewer.el);
  const pending = viewer.load(); viewer.dispose();
  assert.equal(signal.aborted, true);
  finish(response({ items: [log] })); await pending;
  assert.equal(viewer.el.textContent.includes("Failed"), false);
});

const gateway = { hash: "old", config: { session: { dmScope: "per-peer" }, gateway: { auth: { token: "***" } } }, fields: [
  { path: "session.dmScope", label: "会话隔离", type: "string", enum: ["main", "per-peer"], default: "main" },
] };
test("Gateway source accepts only whitelist edits and preserves redacted credentials", () => {
  const next = structuredClone(gateway.config); next.session.dmScope = "main";
  assert.deepEqual(gatewayChanges(gateway, JSON.stringify(next)), { "session.dmScope": "main" });
  next.gateway.auth.token = "new";
  assert.throws(() => gatewayChanges(gateway, JSON.stringify(next)), /只允许/);
  assert.throws(() => gatewayChanges(gateway, "{}"), /只允许/);
});

test("Gateway conflicts keep the error visible and automatically refresh the source", async () => {
  installDom(); view = adminView(); let reads = 0;
  globalThis.fetch = async (url, options) => {
    if (options.method === "PATCH") return new Response(JSON.stringify({ success: false, error: { code: "CONFIG_CONFLICT", message: "配置已被其他操作修改" } }), { status: 409 });
    reads++; return response(gateway);
  };
  const editor = gatewaySettingsEditor({ view }); document.body.append(editor.el); await editor.load();
  const select = find("select", "会话隔离"); select.value = "main"; select.dispatchEvent(new Event("change"));
  button("预览差异并保存").click(); await tick(); button("确认保存").click(); await tick();
  assert.equal(reads, 2);
  assert.ok(document.body.textContent.includes("配置已被其他操作修改"));
  assert.equal(editor.isDirty(), false);
});

test("shared Agent panel previews only this class's changed model and keeps pending alias unlinked", async () => {
  installDom(); view = adminView(); const writes = [];
  globalThis.fetch = async (url, options) => {
    if (options.method === "PATCH") { writes.push({ url, body: JSON.parse(options.body) }); return response({ restart_requested: false }); }
    return response({ configured: {}, models: [{ id: "provider/main", name: "Main", provider: "provider" }], image_models: [], speech_models: [] });
  };
  document.body.append(agentPanel({ kind: "class", identifier: "class-a", label: "一班", present: true, status: "awaiting_qr",
    class: { id: "class-a" }, binding: { status: "awaiting_qr", channel_account_id: "pending-alias" }, model_summary: {},
  }, view));
  await tick();
  assert.ok(document.body.textContent.includes("尚未连接"));
  const main = document.body.querySelectorAll("select")[0]; main.value = "provider/main"; main.dispatchEvent(new Event("change"));
  assert.equal(view.isDirty(), true);
  button("保存模型").click(); await tick(); assert.equal(writes.length, 0);
  button("确认保存").click(); await tick();
  assert.deepEqual(writes, [{ url: "/api/v1/classes/class-a/agent-chat/models", body: { main_model: "provider/main" } }]);
  assert.equal(view.isDirty(), false);
  button("ClassClaw Channels").click(); await tick();
  assert.ok(document.body.querySelector(".classclaw-channels-panel"));
  assert.ok(document.body.textContent.includes("微信尚未绑定完成"));
});

test("usage remains usable when Gateway is offline and displays unknown cost honestly", async () => {
  installDom();
  globalThis.fetch = async (url) => url.includes("/agents?")
    ? new Response(JSON.stringify({ success: false, error: { message: "Gateway 离线" } }), { status: 502 })
    : response({ totals: { ai_requests: 1, total_tokens: 120, input_tokens: 100, cached_input_tokens: 20 }, daily: [{ date: "2026-09-15", requests: 1, total_tokens: 120 }], by_operation: [], by_model: [] });
  const mount = document.createElement("main"); document.body.append(mount); await usage.render(mount);
  assert.ok(mount.textContent.includes("Gateway 侧数据缺失"));
  assert.ok(mount.textContent.includes("120"));
  assert.ok(mount.textContent.includes("20.0%"));
  assert.ok(mount.textContent.includes("Gateway 未提供费用"));
});

test("access combines accounts and expandable classes, independent of Gateway", async () => {
  installDom();
  globalThis.fetch = async (url) => {
    if (url.endsWith("/admin/users")) return response([{ id: "teacher", username: "李老师", role: "head_teacher", is_active: true }]);
    if (url.includes("/classes?")) return response({ items: [{ id: "class-a", name: "一班", grade: "高一", status: "active" }] });
    return new Response(JSON.stringify({ success: false, error: { message: "Gateway 离线" } }), { status: 502 });
  };
  const mount = document.createElement("main"); document.body.append(mount); await access.render(mount, { query: { focus: "agents" } });
  assert.ok(mount.textContent.includes("班主任账号"));
  assert.ok(mount.textContent.includes("一班"));
  assert.ok(mount.textContent.includes("归属班主任"));
  assert.ok(mount.textContent.includes("Gateway 离线"));
});

test("four admin entries and all old routes retain their documented landing pages", () => {
  assert.equal(ADMIN_NAV.flatMap((group) => group.items).length, 4);
  assert.deepEqual(ADMIN_ALIASES["/admin/agents"], ["/admin/access", { focus: "agents" }]);
  assert.deepEqual(ADMIN_ALIASES["/admin/openclaw/maintenance"], ["/admin/ops", { tab: "logs", source: "openclaw" }]);
  assert.equal(Object.keys(ADMIN_ALIASES).length, 12);
});

test("maintenance lists unfinished deletions and retries them in place", async () => {
  installDom();
  const calls = [];
  globalThis.fetch = async (url, options = {}) => {
    const method = options.method || "GET";
    calls.push([url, method]);
    if (url.endsWith("/admin/deletions")) return response([{
      id: "job-1", target_type: "class", target_id: "12345678-aaaa-bbbb-cccc-123456789012",
      phase: "gateway", status: "failed", error_code: "DELETION_WECHAT_INCOMPLETE",
      updated_at: "2026-09-16T09:00:00+08:00", retryable: true,
    }]);
    if (url.endsWith("/admin/deletions/job-1/retry")) return response({ deletion_id: "job-1", cleanup_status: "complete" });
    if (url.includes("/admin/openclaw/sessions/cleanup")) return response({ enabled: false, interval_hours: 0, last_run_at: null });
    return response({});
  };
  const mount = document.createElement("main"); document.body.append(mount);
  await maintenance.render(mount);
  assert.ok(mount.textContent.includes("未完成的删除清理"));
  assert.ok(mount.textContent.includes("DELETION_WECHAT_INCOMPLETE"));
  button("重试清理").click();
  await tick(); await tick();
  assert.ok(calls.some(([url, method]) => url.endsWith("/admin/deletions/job-1/retry") && method === "POST"));
});

test("proposal review renders row-level student data, change details and preview students", () => {
  installDom();
  const batch = proposalReview({
    preview_json: {
      title: "批量登记作业状态", ready: true,
      summary: { homework_title: "语文背诵", items: [
        { student_no: "001", name: "张三", status: "missing" },
        { student_no: "003", name: "王五", status: "missing" },
      ] },
      missing_fields: [], validation_errors: [], low_confidence_evidence: [],
    },
    revision: 1,
  });
  assert.ok(batch.textContent.includes("语文背诵"));
  assert.ok(batch.textContent.includes("学号"));
  assert.ok(batch.textContent.includes("张三"));
  assert.ok(batch.textContent.includes("003"));
  assert.ok(!batch.textContent.includes("student_id"));

  const update = proposalReview({
    preview_json: {
      title: "批量修改学生档案", ready: true,
      summary: { record_count: 2, changes: { gender: "女" }, only_if_empty: ["gender"] },
      students: [{ student_no: "001", name: "张三", before: {} }, { student_no: "003", name: "王五", before: {} }],
      missing_fields: [], validation_errors: [], low_confidence_evidence: [],
    },
    revision: 1,
  });
  assert.ok(update.textContent.includes('{"gender":"女"}'));
  assert.ok(update.textContent.includes("变更前"));
  assert.ok(update.textContent.includes("王五"));
});

test("route guard retains page and URL on cancellation and navigates after acceptance", async () => {
  installDom(); globalThis.location = { hash: "#/a" };
  globalThis.history = { replaceState: (_state, _title, url) => { location.hash = url; } };
  defineRoutes([{ path: "/a" }, { path: "/b" }]); const seen = [];
  setRouteResolver((route) => seen.push(route.path));
  await dispatch(); const remove = setNavigationGuard(() => false);
  location.hash = "#/b"; await dispatch();
  assert.equal(currentRoute().path, "/a"); assert.equal(location.hash, "#/a");
  remove(); location.hash = "#/b"; await dispatch();
  assert.equal(seen.at(-1), "/b");
});
