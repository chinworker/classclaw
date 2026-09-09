import { el, clear, fmtDateTime } from "../util.js";
import { api } from "../api.js";
import { pageHeader, metricCard, statusBadge, dataTable, errorPanel, skeleton } from "../components.js";
import { navigate } from "../router.js";

function bytes(value) {
  if (value === null || value === undefined) return "—";
  const units = ["B", "KiB", "MiB", "GiB"];
  let n = Number(value); let index = 0;
  while (n >= 1024 && index < units.length - 1) { n /= 1024; index += 1; }
  return `${n.toFixed(index ? 1 : 0)} ${units[index]}`;
}

export async function render(mount) {
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
        el("div", { class: "admin-status-line" },
          el("div", {}, el("b", {}, "API"), statusBadge("active", "ONLINE")),
          el("div", {}, el("b", {}, "OpenClaw Gateway"), statusBadge(oc.gateway_live ? "active" : "failed", oc.gateway_live ? "ONLINE" : "OFFLINE")),
          el("div", {}, el("b", {}, "ClassClaw Plugin"), statusBadge(oc.plugin_ready ? "active" : "failed", oc.plugin_ready ? "READY" : "UNAVAILABLE")),
          el("code", {}, oc.gateway_url || "—")),
        el("div", { class: "metric-grid" },
          metricCard("班主任账号", c.active_teachers, `用户总数 ${c.users}`),
          metricCard("班级", c.classes), metricCard("学生", c.students),
          metricCard("智能体", c.agents, `微信已连接 ${c.linked_agents}`),
          metricCard("有效会话", c.active_sessions),
          metricCard("业务库", bytes(storage.database_bytes), storage.database_dialect),
          metricCard("用量库", bytes(storage.usage_database_bytes), "独立 SQLite"),
          metricCard("附件", bytes(storage.attachment_bytes), `${storage.attachment_count} 个文件`)),
        el("div", { class: "admin-quick-grid" },
          quick("用户与权限", "创建、停用、重置班主任账号。", "/admin/users"),
          quick("班级资源", "重命名、分配负责人和彻底删除班级；新建由班主任账号走创建向导。", "/admin/classes"),
          quick("系统配置", "ClassClaw 启动配置、文件位置与生效来源。", "/admin/settings"),
          quick("OpenClaw 配置", "Gateway 全局运行参数与模型服务商。", "/admin/openclaw"),
          quick("班级 Agent", "各班级模型、提示词、语音与微信绑定。", "/admin/agents"),
          quick("使用量", "请求、写入、登录与 Token 消耗。", "/admin/usage")),
        el("div", { class: "card" }, el("h3", {}, "最近审计"), dataTable({
          columns: [
            { key: "created_at", label: "时间", render: (r) => fmtDateTime(r.created_at) },
            { key: "action", label: "动作", render: (r) => el("code", {}, r.action) },
            { key: "entity_type", label: "实体" },
            { key: "entity_id", label: "ID", render: (r) => el("code", {}, r.entity_id.slice(0, 12)) },
          ], rows: data.recent_audit,
        })),
      );
    } catch (error) { clear(host); host.append(errorPanel(error, { onRetry: load })); }
  }

  function quick(title, desc, path) {
    return el("button", { class: "admin-quick-card", type: "button", onclick: () => navigate(path) },
      el("b", {}, title), el("span", {}, desc), el("code", {}, path));
  }
  await load();
}
