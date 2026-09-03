import { el, clear } from "../util.js";
import { api } from "../api.js";
import { appConfig } from "../config.js";
import { pageHeader, metricCard, dataTable, field, errorPanel, skeleton, openDrawer } from "../components.js";

const num = (value) => Number(value || 0).toLocaleString("zh-CN");
const duration = (value) => {
  const ms = Number(value);
  if (!Number.isFinite(ms) || ms < 0) return "—";
  if (ms < 1000) return `${Math.round(ms)} ms`;
  return `${(ms / 1000).toFixed(ms < 10000 ? 2 : 1)} s`;
};

export async function render(mount) {
  const host = el("div");
  const configuredDays = Number(appConfig.web.usage_window_days || 30);
  const dayOptions = [...new Set([7, 14, 30, 90, 180, 365, configuredDays])].sort((a, b) => a - b);
  const days = el("select", {}, dayOptions.map((value) => el("option", { value, selected: value === configuredDays }, `${value} 天`)));
  mount.append(pageHeader("使用量与 Token", "统计网页与智能体的请求、写入、登录及 OpenClaw Responses API 返回的 Token 用量。"),
    el("div", { class: "filter-bar" }, field("统计窗口", days), el("button", { class: "secondary", type: "button", onclick: load }, "查询")), host);

  async function load() {
    clear(host); host.append(skeleton(6));
    try {
      const [data, agentResult] = await Promise.all([
        api(`/admin/usage?days=${days.value}`),
        api(`/admin/usage/agents?days=${days.value}`).then((value) => ({ value })).catch((error) => ({ error })),
      ]);
      const t = data.totals;
      clear(host);
      host.append(
        el("div", { class: "metric-grid" },
          metricCard("总 Token", num(t.total_tokens), `${num(t.input_tokens)} input / ${num(t.output_tokens)} output`),
          metricCard("缓存输入", num(t.cached_input_tokens)), metricCard("AI 请求", num(t.ai_requests)),
          metricCard("交互分析", num(t.interaction_analyses)), metricCard("写入预览", num(t.write_proposals)),
          metricCard("审计动作", num(t.audit_actions)), metricCard("登录", num(t.logins))),
        el("div", { class: "card" }, el("h3", {}, "按日"), dataTable({
          columns: [
            { key: "date", label: "日期", render: (r) => el("code", {}, r.date) },
            { key: "requests", label: "交互分析" }, { key: "writes", label: "审计动作" }, { key: "logins", label: "登录" },
            { key: "input_tokens", label: "Input", render: (r) => num(r.input_tokens) },
            { key: "output_tokens", label: "Output", render: (r) => num(r.output_tokens) },
            { key: "total_tokens", label: "Total", render: (r) => num(r.total_tokens) },
          ], rows: [...data.daily].reverse(),
        })),
        el("div", { class: "admin-two-col" },
          el("div", { class: "card" }, el("h3", {}, "按调用类型"), dataTable({ columns: [{ key: "name", label: "类型", render: (r) => el("code", {}, r.name) }, { key: "requests", label: "请求" }, { key: "tokens", label: "Token", render: (r) => num(r.tokens) }], rows: data.by_operation })),
          el("div", { class: "card" }, el("h3", {}, "按模型"), dataTable({ columns: [{ key: "name", label: "模型", render: (r) => el("code", {}, r.name) }, { key: "requests", label: "请求" }, { key: "tokens", label: "Token", render: (r) => num(r.tokens) }], rows: data.by_model }))),
        agentResult.value ? el("div", { class: "card" },
          el("div", { class: "row-gap admin-section-title" }, el("div", {}, el("h3", {}, "按智能体"), el("p", { class: "muted" }, "包含 Main、数据提取和班级智能体；基于 OpenClaw 会话记录统计调用、回复和响应耗时，结果缓存 30 秒。"))),
          dataTable({
            columns: [
              { key: "label", label: "智能体", render: (r) => el("div", {}, el("b", {}, r.label || r.class_name || r.agent_id), el("span", { class: "tag tag-muted" }, (r.kind || "class").toUpperCase()), el("code", {}, r.agent_id || "NOT CREATED"), (r.warnings || []).map((warning) => el("div", { class: "field-error" }, warning))) },
              { key: "calls", label: "调用次数", render: (r) => num(r.calls) },
              { key: "assistant", label: "Agent 回复", render: (r) => num(r.messages?.assistant) },
              { key: "avg_latency", label: "平均延迟", render: (r) => duration(r.latency?.avgMs) },
              { key: "p95_latency", label: "P95 延迟", render: (r) => duration(r.latency?.p95Ms) },
              { key: "latency_samples", label: "延迟样本", render: (r) => num(r.latency?.count) },
              { key: "cacheRead", label: "Cache read", render: (r) => num(r.totals?.cacheRead) },
              { key: "totalTokens", label: "Total", render: (r) => el("b", { class: "mono" }, num(r.totals?.totalTokens)) },
              { key: "totalCost", label: "Cost", render: (r) => r.totals ? `$${Number(r.totals.totalCost || 0).toFixed(4)}` : "—" },
              { key: "errors", label: "错误", render: (r) => num(r.messages?.errors) },
              { key: "details", label: "趋势", render: (r) => el("button", { class: "text-button", type: "button", onclick: () => openAgentDetail(r) }, "查看") },
            ], rows: agentResult.value,
          })) : errorPanel(agentResult.error),
        !data.token_collection_started ? el("div", { class: "blocked-panel" }, el("b", {}, "暂无 Token 数据"), el("span", {}, "统计从本次升级后开始采集；历史调用无法补算。")) : null,
      );
    } catch (error) { clear(host); host.append(errorPanel(error, { onRetry: load })); }
  }

  function openAgentDetail(row) {
    const summary = el("div", { class: "metric-grid" },
      metricCard("调用次数", num(row.calls)), metricCard("Agent 回复", num(row.messages?.assistant)),
      metricCard("平均延迟", duration(row.latency?.avgMs), `${num(row.latency?.count)} 个有效样本`),
      metricCard("P95 延迟", duration(row.latency?.p95Ms), `最短 ${duration(row.latency?.minMs)} / 最长 ${duration(row.latency?.maxMs)}`),
      metricCard("最近一次", duration(row.latency?.latestMs), `${num(row.metrics_files_scanned)} 个会话文件`));
    const daily = dataTable({
      columns: [
        { key: "date", label: "日期", render: (r) => el("code", {}, r.date) },
        { key: "model_calls", label: "调用次数", render: (r) => num(r.model_calls) },
        { key: "messages", label: "消息", render: (r) => num(r.messages) },
        { key: "errors", label: "错误", render: (r) => num(r.errors) },
        { key: "avg", label: "平均延迟", render: (r) => duration(r.latency?.avgMs) },
        { key: "p95", label: "P95", render: (r) => duration(r.latency?.p95Ms) },
        { key: "samples", label: "样本", render: (r) => num(r.latency?.count) },
      ], rows: [...(row.daily || [])].reverse(),
    });
    openDrawer({ title: `调用与延迟 · ${row.label || row.class_name || row.agent_id}`, wide: true, body: el("div", {}, summary,
      el("p", { class: "muted" }, "响应耗时优先使用 OpenClaw 记录的模型 durationMs；缺失时按用户消息到 Agent 回复记录的时间差计算。无有效样本时显示为“—”。"), daily) });
  }
  await load();
}
