import { el, clear } from "../util.js";
import { appConfig } from "../config.js";
import { pageHeader, metricCard, dataTable, field, errorPanel, skeleton, emptyState, statusBadge } from "../components.js";
import { adminView } from "../adminView.js";
import { trendChart, barList } from "../charts.js";

const num = (value) => Number(value || 0).toLocaleString("zh-CN");
const duration = (value) => value === null || value === undefined ? "—" : `${Math.round(Number(value))} ms`;
const cost = (value) => value === null || value === undefined ? "—" : `$${Number(value).toFixed(4)}`;
let activeView = null;
export function dispose() { activeView?.dispose(); activeView = null; }

export async function render(mount) {
  dispose(); const view = adminView(); activeView = view;
  const host = el("div");
  const days = el("select", { "aria-label": "统计窗口" }, ...[7, 30, 90].map((value) => el("option", { value }, `${value} 天`)));
  days.value = [7, 30, 90].includes(Number(appConfig.web.usage_window_days)) ? String(appConfig.web.usage_window_days) : "30";
  mount.append(pageHeader("用量", "查看谁在使用 AI、消耗多少 Token，以及调用分布。"),
    el("div", { class: "filter-bar" }, field("统计窗口", days), el("button", { class: "secondary", type: "button", onclick: load }, "刷新")), host);
  let revision = 0;
  async function load() {
    const version = ++revision; const windowDays = days.value;
    clear(host); host.append(skeleton(5));
    const [local, gateway] = await Promise.allSettled([
      view.api(`/admin/usage?days=${windowDays}`), view.api(`/admin/usage/agents?days=${windowDays}`),
    ]);
    if (!view.active || version !== revision) return;
    clear(host);
    if (local.status === "rejected") { host.append(errorPanel(local.reason, { onRetry: load })); return; }
    const data = local.value; const totals = data.totals;
    const agents = gateway.status === "fulfilled" ? gateway.value : [];
    const costs = agents.map((agent) => agent.totals?.totalCost).filter((value) => value !== null && value !== undefined);
    const totalCost = costs.length ? costs.reduce((sum, value) => sum + Number(value), 0) : null;
    const active = agents.filter((agent) => agent.calls > 0 || agent.totals?.totalTokens > 0).length;
    if (gateway.status === "rejected") host.append(el("div", { class: "gateway-degraded" }, el("p", {}, "Gateway 侧数据缺失，以下本地调用统计仍可查看。"), errorPanel(gateway.reason, { onRetry: load })));
    host.append(el("div", { class: "metric-grid usage-summary" },
      metricCard("窗口内请求数", num(totals.ai_requests), `最近 ${windowDays} 天 · ClassClaw 记录`),
      metricCard("总 Token", num(totals.total_tokens), `缓存输入 ${totals.input_tokens ? (totals.cached_input_tokens / totals.input_tokens * 100).toFixed(1) : "0.0"}%`),
      metricCard("估算费用", cost(totalCost), costs.length ? "Gateway 记录 · USD" : "Gateway 未提供费用"),
      metricCard("活跃 Agent", gateway.status === "fulfilled" ? active : "—", "窗口内产生调用的 Agent")));
    if (!totals.ai_requests && !agents.some((agent) => agent.calls || agent.totals?.totalTokens)) host.append(emptyState("暂无用量数据，产生 AI 调用后自动统计"));
    host.append(el("section", { class: "card" }, el("h3", {}, "每日趋势"), trendChart(data.daily)),
      el("div", { class: "admin-two-col" }, el("section", { class: "card" }, el("h3", {}, "按操作"), barList(data.by_operation)),
        el("section", { class: "card" }, el("h3", {}, "按模型"), barList(data.by_model))));
    if (gateway.status === "fulfilled") host.append(el("section", { class: "card" }, el("h3", {}, "Agent 明细"),
      el("p", { class: "muted" }, "点击 Agent 展开每日调用趋势。Gateway 统计包含其会话调用，与 ClassClaw 本地请求统计口径不同；结果缓存 30 秒。"),
      dataTable({ columns: [
        { label: "Agent", render: (row) => el("details", { class: "usage-agent-detail" },
          el("summary", {}, el("b", {}, row.label), el("span", { class: "tag tag-muted" }, row.kind === "class" ? "班级" : row.kind === "main" ? "Main" : "提取")),
          el("code", {}, row.agent_id || "未创建"), trendChart(row.daily || [], { yKeys: ["model_calls", "messages"], labels: ["调用", "消息"], compact: true }),
          el("p", { class: "muted" }, `延迟样本 ${num(row.latency?.count)} · 扫描文件 ${num(row.metrics_files_scanned)}`)) },
        { label: "状态", render: (row) => row.available === false ? statusBadge("pending", "统计不可用") : statusBadge(row.status) },
        { label: "调用数", render: (row) => num(row.calls) },
        { label: "消息数", render: (row) => num(row.messages?.total ?? ((row.messages?.user || 0) + (row.messages?.assistant || 0))) },
        { label: "Token", render: (row) => row.totals?.totalTokens === undefined ? "—" : num(row.totals.totalTokens) },
        { label: "估算费用", render: (row) => cost(row.totals?.totalCost) },
        { label: "延迟 p50 / p95", render: (row) => `${duration(row.latency?.p50Ms)} / ${duration(row.latency?.p95Ms)}` },
        { label: "告警", render: (row) => el("div", { class: "usage-warnings" }, ...(row.warnings || []).map((warning) => el("span", { class: "tag tag-warn" }, warning))) },
      ], rows: agents })),
      el("details", { class: "card" }, el("summary", {}, "其他活动指标"), el("div", { class: "metric-grid" }, metricCard("交互分析", num(totals.interaction_analyses)), metricCard("写入预览", num(totals.write_proposals)), metricCard("审计动作", num(totals.audit_actions)), metricCard("登录", num(totals.logins)))));
  }
  days.addEventListener("change", load); await load();
}
