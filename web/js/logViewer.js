import { el, clear, todayStr, dateStrSH } from "./util.js";
import { field, errorPanel, emptyState, openDrawer, skeleton } from "./components.js";
import { adminView } from "./adminView.js";
import { appConfig } from "./config.js";

export function logParams(filters, { cursor = null, fileId = null, clock = Date.now() } = {}) {
  const params = new URLSearchParams({ source: filters.source, limit: "500" });
  for (const key of ["level", "q", "request_id"]) if (filters[key]?.trim()) params.set(key, filters[key].trim());
  if (filters.range === "today") params.set("since", `${todayStr()}T00:00:00`);
  else if (["15m", "1h"].includes(filters.range)) params.set("since", new Date(clock - (filters.range === "15m" ? 15 : 60) * 60_000).toISOString());
  if (cursor !== null) params.set("cursor", String(cursor));
  if (fileId) params.set("file_id", fileId);
  return params;
}
const messageText = (item) => typeof item.message === "string" ? item.message : JSON.stringify(item.message ?? item.msg ?? item);
export function logText(items) {
  return items.map((item) => `${item.timestamp || "—"} ${item.level || "INFO"} ${item.logger || "runtime"} ${messageText(item)}${item.request_id ? ` req:${item.request_id}` : ""}${item.duration_ms !== undefined ? ` ${item.duration_ms}ms` : ""}`).join("\n");
}
function timeLabel(value) {
  if (!value) return "—";
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? String(value) : date.toLocaleString("zh-CN", { timeZone: appConfig.runtime.timezone,
    year: "numeric", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", second: "2-digit", fractionalSecondDigits: 3, hour12: false });
}

export function logViewer({ source: initialSource = "classclaw", level: initialLevel = "", requestId = "", query = "", range: initialRange = "all", onFilters = () => {} } = {}) {
  const view = adminView();
  const root = el("div"); const host = el("div"); const errors = el("div", { "aria-live": "polite" });
  const source = el("select", { "aria-label": "日志来源" }, ...["classclaw", "openclaw"].map((value) => el("option", { value }, value === "classclaw" ? "ClassClaw" : "OpenClaw"))); source.value = initialSource;
  const level = el("select", { "aria-label": "日志级别" }, ...["", "DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"].map((value) => el("option", { value }, value || "全部级别"))); level.value = initialLevel;
  const range = el("select", { "aria-label": "日志时间范围" }, ...[["15m", "最近 15 分钟"], ["1h", "最近 1 小时"], ["today", "今天"], ["all", "全部"]].map(([value, title]) => el("option", { value }, title))); range.value = initialRange;
  const search = el("input", { type: "search", value: query, maxlength: "200", "aria-label": "日志关键字", placeholder: "搜索关键字" });
  const request = el("input", { value: requestId, maxlength: "200", "aria-label": "request_id", placeholder: "精确匹配 request_id" });
  const live = el("input", { type: "checkbox", role: "switch", "aria-label": "实时刷新" });
  const onlyErrors = el("input", { type: "checkbox", checked: initialLevel === "ERROR", "aria-label": "只看错误" });
  const refresh = el("button", { class: "secondary", type: "button", onclick: () => load() }, "刷新");
  const exportButton = el("button", { class: "secondary", type: "button", onclick: exportLogs }, "导出当前视图");
  root.append(el("div", { class: "card filter-bar log-filters" }, field("来源", source), field("级别", level), field("时间范围", range),
    field("搜索", search), field("request_id", request), field("只看错误", onlyErrors), field("实时刷新 · 3 秒", live), refresh, exportButton), errors, host);
  let rows = []; let cursor = null; let fileId = null; let timer = null; let revision = 0; let controller = null;
  view.own(() => controller?.abort());
  const filters = () => ({ source: source.value, level: level.value, range: range.value, q: search.value, request_id: request.value });
  function schedule() {
    view.cancelTimer(timer);
    if (live.checked && view.active) timer = view.delay(() => load({ incremental: true }), 3000);
  }
  function selectRequest(id) { request.value = id; void load(); }
  function renderRows(data) {
    clear(host);
    host.append(el("div", { class: "log-status row-gap" }, el("code", {}, data.file || source.value),
      el("span", { class: "muted", role: "status" }, `${rows.length} 条 · 单次最多扫描最近 5000 行`),
      data.truncated ? el("span", { class: "tag tag-warn" }, "已达到读取上限，可缩小时间或搜索范围") : null),
    rows.length ? el("div", { class: "log-console", "aria-label": "日志列表" }, rows.map((item) => logRow(item, selectRequest, () => details(item))))
      : emptyState(data.available === false ? "日志文件暂不可用" : "没有匹配的日志", "调整筛选条件或在产生请求后刷新。"));
  }
  async function load({ incremental = false } = {}) {
    const version = ++revision;
    controller?.abort(); controller = new AbortController();
    view.cancelTimer(timer);
    if (!incremental) { cursor = null; fileId = null; rows = []; clear(host); host.append(skeleton(5)); }
    clear(errors); refresh.disabled = true;
    const selected = filters();
    onFilters(selected);
    try {
      const data = await view.api(`/admin/logs?${logParams(selected, { cursor, fileId })}`, { signal: controller.signal });
      if (version !== revision) return;
      if (data.reset) rows = [];
      const fresh = data.items || [];
      const seen = new Set();
      rows = [...fresh, ...(incremental ? rows : [])].filter((row) => {
        const key = JSON.stringify(row); if (seen.has(key)) return false; seen.add(key); return true;
      }).slice(0, 5000);
      const since = logParams(selected).get("since");
      if (since && selected.range !== "today") rows = rows.filter((row) => new Date(row.timestamp).getTime() >= new Date(since).getTime());
      if (selected.range === "today") rows = rows.filter((row) => dateStrSH(row.timestamp) >= todayStr());
      cursor = data.cursor ?? null; fileId = data.file_id ?? null;
      renderRows(data);
    } catch (error) {
      if (view.active && version === revision) { errors.append(errorPanel(error, { onRetry: () => load() })); if (!rows.length) clear(host); }
    } finally { if (view.active && version === revision) { refresh.disabled = false; schedule(); } }
  }
  async function details(item) {
    const panel = adminView(); view.own(() => panel.dispose());
    const body = el("div", {}, skeleton(4));
    view.overlay(openDrawer({ title: item.request_id ? `请求详情 · ${item.request_id}` : "日志详情", wide: true, body, onClose: () => panel.dispose() }));
    if (!item.request_id) { clear(body); body.append(el("pre", { class: "log-detail" }, JSON.stringify(item, null, 2))); return; }
    try {
      const data = await panel.api(`/admin/logs?${logParams({ source: source.value, request_id: item.request_id, range: "all" })}`);
      clear(body); body.append(el("p", { class: "muted" }, "该请求在当前来源最近日志中的全部级别记录（有界读取）。"),
        ...[...(data.items || [])].reverse().map((row) => logRow(row, selectRequest)));
    } catch (error) { if (panel.active) { clear(body); body.append(errorPanel(error)); } }
  }
  function exportLogs() {
    const url = URL.createObjectURL(new Blob([logText(rows)], { type: "text/plain;charset=utf-8" }));
    const link = el("a", { href: url, download: `classclaw-${source.value}-${todayStr()}.log` });
    root.append(link); link.click(); link.remove();
    view.own(() => URL.revokeObjectURL(url)); view.delay(() => URL.revokeObjectURL(url), 1000);
  }
  for (const input of [source, level, range]) input.addEventListener("change", () => { onlyErrors.checked = level.value === "ERROR"; void load(); });
  for (const input of [search, request]) input.addEventListener("keydown", (event) => { if (event.key === "Enter") void load(); });
  onlyErrors.addEventListener("change", () => { level.value = onlyErrors.checked ? "ERROR" : ""; void load(); });
  live.addEventListener("change", schedule);
  return { el: root, load, dispose: () => view.dispose() };
}

function logRow(item, onRequest, onDetails = null) {
  const level = String(item.level || "INFO").toUpperCase();
  return el("div", { class: `log-row level-${level.toLowerCase()}` },
    el("span", { class: "log-time" }, timeLabel(item.timestamp)), el("span", { class: "log-level" }, level),
    el("span", { class: "log-logger" }, item.logger || "runtime"),
    el("details", { class: "log-message" }, el("summary", {}, messageText(item).split("\n")[0]), el("pre", {}, messageText(item))),
    item.request_id ? el("button", { class: "text-button log-request", type: "button", title: "按此 request_id 过滤", onclick: () => onRequest(item.request_id) }, item.request_id) : el("span", { class: "muted" }, "—"),
    el("span", { class: "mono" }, item.duration_ms !== undefined ? `${item.duration_ms} ms` : "—"),
    onDetails ? el("button", { class: "text-button", type: "button", onclick: onDetails }, "请求详情") : null);
}
