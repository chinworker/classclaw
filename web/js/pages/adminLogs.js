// 管理员 · 运行日志：读取 ClassClaw 结构化日志和 OpenClaw Gateway 日志。

import { el, clear, fmtDateTime } from "../util.js";
import { api } from "../api.js";
import { pageHeader, field, errorPanel, skeleton, emptyState } from "../components.js";

const LEVELS = ["", "DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"];

function levelName(item) {
  const raw = String(item.level || item.levelName || "INFO").toUpperCase();
  return raw === "WARN" ? "WARNING" : raw;
}

function messageText(item) {
  const value = item.message ?? item.msg ?? item.event ?? item.name;
  if (typeof value === "string") return value;
  if (value !== undefined) return JSON.stringify(value);
  return JSON.stringify(item);
}

function renderLog(item) {
  const level = levelName(item);
  const detail = el("pre", { class: "log-detail hidden" }, JSON.stringify(item, null, 2));
  const row = el("div", { class: `log-row level-${level.toLowerCase()}` },
    el("span", { class: "log-time" }, fmtDateTime(item.timestamp || item.time || item.ts)),
    el("span", { class: "log-level" }, level),
    el("span", { class: "log-logger" }, item.logger || item.module || "runtime"),
    el("span", { class: "log-message" }, messageText(item)),
    el("button", { class: "text-button log-expand", type: "button", onclick: (event) => {
      detail.classList.toggle("hidden");
      event.currentTarget.textContent = detail.classList.contains("hidden") ? "详情" : "收起";
    } }, "详情"), detail);
  return row;
}

export async function render(mount) {
  const source = el("select", {},
    el("option", { value: "classclaw" }, "ClassClaw 后端"),
    el("option", { value: "openclaw" }, "OpenClaw Gateway"));
  const level = el("select", {}, LEVELS.map((value) => el("option", { value }, value || "全部级别")));
  const query = el("input", { type: "search", maxlength: "200", placeholder: "搜索消息、路径、request_id" });
  const limit = el("select", {}, [50, 100, 200, 500].map((value) => el("option", { value, selected: value === 100 }, `${value} 条`)));
  const auto = el("input", { type: "checkbox" });
  const refresh = el("button", { class: "secondary", type: "button" }, "刷新");
  const host = el("div");
  const filters = el("div", { class: "card filter-bar log-filters" },
    field("来源", source), field("级别", level), field("搜索", query), field("数量", limit),
    el("label", { class: "field check-field log-auto" }, auto, el("span", {}, "每 5 秒刷新")), refresh);
  mount.append(pageHeader("运行日志", "查看后端请求、错误和 OpenClaw Gateway 运行记录。日志不记录请求正文、密码或消息原文。"), filters, host);

  let loading = false;
  let timer = null;

  async function load({ quiet = false } = {}) {
    if (loading || !mount.isConnected) return;
    loading = true;
    refresh.disabled = true;
    if (!quiet) { clear(host); host.append(skeleton(7)); }
    try {
      const params = new URLSearchParams({ source: source.value, limit: limit.value });
      if (level.value) params.set("level", level.value);
      if (query.value.trim()) params.set("q", query.value.trim());
      const data = await api(`/admin/logs?${params}`);
      clear(host);
      host.append(
        el("div", { class: "admin-status-line log-status" },
          el("div", {}, el("span", { class: "muted" }, "SOURCE"), el("code", {}, data.source)),
          el("div", {}, el("span", { class: "muted" }, "FILE"), el("code", {}, data.file || "—")),
          data.size !== undefined ? el("div", {}, el("span", { class: "muted" }, "SIZE"), el("code", {}, Number(data.size || 0).toLocaleString("zh-CN"))) : null,
          data.truncated ? el("span", { class: "tag tag-warn" }, "TRUNCATED") : null,
          el("span", { class: "spacer" }), el("span", { class: "muted" }, `${data.items?.length || 0} 条 · ${new Date().toLocaleTimeString("zh-CN")}`)),
        data.items?.length ? el("div", { class: "log-console" }, data.items.map(renderLog)) : emptyState("没有匹配的日志", "调整来源、级别或搜索条件后重试。"));
    } catch (error) {
      clear(host); host.append(errorPanel(error, { onRetry: load }));
    } finally {
      loading = false;
      refresh.disabled = false;
    }
  }

  function schedule() {
    if (timer) clearTimeout(timer);
    if (!auto.checked || !mount.isConnected) return;
    timer = setTimeout(async () => { await load({ quiet: true }); schedule(); }, 5000);
  }

  refresh.onclick = () => load();
  source.onchange = () => load();
  level.onchange = () => load();
  limit.onchange = () => load();
  query.addEventListener("keydown", (event) => { if (event.key === "Enter") load(); });
  auto.onchange = schedule;
  await load();
}
