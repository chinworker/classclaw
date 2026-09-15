// 管理员 · 审计日志：轻量审计（操作人、动作、实体、时间），不展示完整前后快照。

import { el, clear, fmtDateTime } from "../util.js";
import { adminView } from "../adminView.js";
import { pageHeader, dataTable, errorPanel, skeleton, emptyState, field, statusBadge, jsonDetails } from "../components.js";

let activeView = null;
export function dispose() { activeView?.dispose(); activeView = null; }

export async function render(mount, ctx = {}) {
  dispose(); const view = adminView(); activeView = view;
  clear(mount);
  const api = async (path, options = {}) => {
    const result = await view.api(path, options);
    if (options.method && options.method !== "GET") ctx.onChanged?.();
    return result;
  };

  const host = el("div");
  const entityTypeInput = el("input", { type: "text", placeholder: "如 write_proposal / user" });
  const entityIdInput = el("input", { type: "text", placeholder: "实体 ID（可选）" });
  mount.append(
    pageHeader("审计日志", "仅管理员可见；最多返回最近 200 条。"),
    el("div", { class: "filter-bar" },
      field("实体类型", entityTypeInput),
      field("实体 ID", entityIdInput),
      el("button", { class: "secondary", type: "button", onclick: () => load() }, "筛选"),
      el("button", { class: "text-button", type: "button", onclick: () => { entityTypeInput.value = ""; entityIdInput.value = ""; load(); } }, "清空筛选")),
    host);

  async function load() {
    clear(host);
    host.append(skeleton(5));
    const params = new URLSearchParams();
    if (entityTypeInput.value.trim()) params.set("entity_type", entityTypeInput.value.trim());
    if (entityIdInput.value.trim()) params.set("entity_id", entityIdInput.value.trim());
    let rows;
    try {
      rows = await api(`/audit-logs${params.size ? `?${params}` : ""}`);
    } catch (error) { if (!view.active) return;
      clear(host);
      host.append(errorPanel(error, { onRetry: load }));
      return;
    }
    clear(host);
    if (!rows.length) { host.append(emptyState("没有匹配的审计记录", "当前筛选条件下没有记录。")); return; }
    host.append(dataTable({
      columns: [
        { key: "created_at", label: "时间", render: (r) => fmtDateTime(r.created_at) },
        { key: "operator_type", label: "操作人", render: (r) => el("span", {}, r.operator_type, r.operator_id ? el("code", { style: { marginLeft: "4px" } }, r.operator_id) : null) },
        { key: "action", label: "动作", render: (r) => statusBadge(null, r.action) },
        { key: "entity_type", label: "实体类型" },
        { key: "entity_id", label: "实体 ID", render: (r) => el("code", {}, `${r.entity_id.slice(0, 12)}…`) },
        { label: "配置变更", render: (r) => r.entity_type === "configuration" && r.after_json
          ? jsonDetails(r.after_json, "键名与版本") : "—" },
      ],
      rows,
    }));
  }

  await load();
}
