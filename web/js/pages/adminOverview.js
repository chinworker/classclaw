import { el, clear, fmtDateTime } from "../util.js";
import { adminView } from "../adminView.js";
import { pageHeader, metricCard, statusBadge, dataTable, errorPanel, skeleton } from "../components.js";
import { navigate } from "../router.js";

function bytes(value) {
  if (value === null || value === undefined) return "—";
  const units = ["B", "KiB", "MiB", "GiB"];
  let n = Number(value); let index = 0;
  while (n >= 1024 && index < units.length - 1) { n /= 1024; index += 1; }
  return `${n.toFixed(index ? 1 : 0)} ${units[index]}`;
}

let activeView = null;
export function dispose() { activeView?.dispose(); activeView = null; }
export async function render(mount) {
  dispose(); const view = adminView(); activeView = view;
  const api = view.api;
  const host = el("div");
  mount.append(pageHeader("运行概览", "ClassClaw 后端、OpenClaw 与核心资源的实时状态。",
    el("button", { class: "secondary", type: "button", onclick: load }, "刷新")), host);

  async function load() {
    clear(host); host.append(skeleton(6));
    try {
      const data = await api("/admin/overview");
      const c = data.counts; const storage = data.storage; const oc = data.openclaw;
      clear(host);
      host.append(
        el("div", { class: "health-grid" },
          health("ClassClaw 数据库", data.health?.database, "/admin/ops?tab=database"),
          health("用量库", data.health?.usage_database, "/admin/ops?tab=database"),
          health("附件目录可写", data.health?.attachments_writable, "/admin/settings?section=storage"),
          health("Gateway 连通", oc.gateway_live, "/admin/settings?domain=openclaw&section=connection"),
          health("插件在线", oc.plugin_ready, "/admin/settings?domain=openclaw&section=gateway"),
          health("admin-rpc 在线", oc.admin_rpc_ready, "/admin/settings?domain=openclaw&section=gateway")),
        el("div", { class: "metric-grid usage-summary" }, metricCard("学生", c.students), metricCard("班级", c.classes),
          metricCard("本周 AI 调用", c.ai_requests_week), metricCard("今日 Token", c.today_tokens === null ? "—" : Number(c.today_tokens || 0).toLocaleString("zh-CN"))),
        el("section", { class: "card" }, el("div", { class: "row-gap admin-section-title" }, el("h3", {}, "最近告警"),
          el("a", { href: "#/admin/ops?tab=logs&level=ERROR" }, "查看错误日志")),
          ...(data.recent_alerts?.length ? data.recent_alerts.map((item) => el("a", { class: "overview-alert", href: `#/admin/ops?tab=logs&level=ERROR${item.request_id ? `&request_id=${encodeURIComponent(item.request_id)}` : ""}` },
            statusBadge("failed", "ERROR"), el("span", {}, String(item.message || "错误日志")), el("small", {}, fmtDateTime(item.timestamp)))) : [el("p", { class: "muted" }, "最近日志中暂无 ERROR 告警。") ])),
        el("div", { class: "admin-quick-grid" },
          quick("设置中心", "配置来源、差异预览与 Agent 设置。", "/admin/settings"),
          quick("班级与账号", "管理班主任、班级归属及班级 Agent。", "/admin/access"),
          quick("运维中心", "用量、日志、备份、数据库和审计。", "/admin/ops")),
        el("details", { class: "card" }, el("summary", {}, "资源详情"), el("div", { class: "metric-grid" },
          metricCard("班主任", c.active_teachers, `账号总数 ${c.users}`), metricCard("智能体", c.agents, `微信已连接 ${c.linked_agents}`),
          metricCard("有效会话", c.active_sessions), metricCard("业务库", bytes(storage.database_bytes), storage.database_dialect),
          metricCard("用量库", bytes(storage.usage_database_bytes)), metricCard("附件", bytes(storage.attachment_bytes), `${storage.attachment_count} 个文件`))),
        el("div", { class: "card" }, el("h3", {}, "最近审计"), dataTable({
          columns: [
            { key: "created_at", label: "时间", render: (r) => fmtDateTime(r.created_at) },
            { key: "action", label: "动作", render: (r) => el("code", {}, r.action) },
            { key: "entity_type", label: "实体" },
            { key: "entity_id", label: "ID", render: (r) => el("code", {}, r.entity_id.slice(0, 12)) },
          ], rows: data.recent_audit,
        })),
      );
    } catch (error) { if (!view.active) return; clear(host); host.append(errorPanel(error, { onRetry: load })); }
  }

  function health(title, ready, path) {
    return el("a", { class: "health-light", href: `#${path}` }, el("span", { class: `status-dot ${ready ? "ok" : "bad"}` }),
      el("b", {}, title), el("small", { class: "muted" }, ready ? "正常" : "需检查"));
  }
  function quick(title, desc, path) {
    return el("button", { class: "admin-quick-card", type: "button", onclick: () => navigate(path) },
      el("b", {}, title), el("span", {}, desc), el("code", {}, path));
  }
  await load();
}
