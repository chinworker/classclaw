import { el, clear } from "../util.js";
import { pageHeader, errorPanel, skeleton, emptyState } from "../components.js";
import { adminView } from "../adminView.js";
import { settingsEditor } from "../settingsEditor.js";
import { gatewaySettingsEditor } from "../gatewaySettingsEditor.js";
import { agentPanel } from "../adminAgentPanel.js";
import { navigate } from "../router.js";

let activeView = null;
export function dispose() { activeView?.dispose(); activeView = null; }

export async function render(mount, ctx = {}) {
  dispose();
  const view = adminView(); activeView = view;
  const domain = ctx.query?.domain === "openclaw" ? "openclaw" : "classclaw";
  const section = ctx.query?.section || (domain === "openclaw" ? "gateway" : "");
  let editor = null;
  view.guard(() => editor?.isDirty() || false);
  const host = el("div");
  mount.append(pageHeader("设置中心", "查看配置来源、预览差异并保存。ClassClaw 启动配置写入后需重启生效。"),
    el("div", { class: "settings-domains tabs", "aria-label": "设置域" },
      ...["classclaw", "openclaw"].map((value) => el("button", { type: "button", class: value === domain ? "active" : "",
        "aria-pressed": String(value === domain), onclick: () => navigate("/admin/settings", { domain: value }) }, value === "classclaw" ? "ClassClaw" : "OpenClaw"))), host);
  const body = el("div", { class: "settings-content" });
  if (domain === "openclaw") {
    const navigation = el("nav", { class: "settings-tree", "aria-label": "OpenClaw 分组" });
    for (const [key, title] of [["gateway", "Gateway 全局"], ["system-agents", "系统 Agent"], ["class-agents", "班级 Agent"], ["connection", "微信与连接"]]) {
      navigation.append(el("button", { type: "button", class: section === key ? "active" : "", onclick: () => navigate("/admin/settings", { domain, section: key }) }, title));
    }
    host.append(el("div", { class: "settings-layout" }, navigation, body));
  } else host.append(body);
  async function load() {
    clear(body); body.append(skeleton(5));
    try {
      if (domain === "classclaw" || section === "connection") {
        const snapshot = await view.api("/admin/settings/document");
        editor = settingsEditor({ snapshot, view, sectionIds: domain === "openclaw" ? ["openclaw", "workspaces", "wechat"] : null,
          hideTree: domain === "openclaw", initialSection: domain === "classclaw" ? section : "" });
        clear(body); body.append(editor.el);
      } else if (section === "gateway") {
        editor = gatewaySettingsEditor({ view }); clear(body); body.append(editor.el); await editor.load();
      } else {
        const rows = await view.api("/admin/openclaw/agents/catalog");
        clear(body);
        const selected = rows.filter((agent) => section === "class-agents" ? agent.kind === "class" : agent.kind !== "class");
        const search = el("input", { type: "search", value: ctx.query?.class_id || "", placeholder: "搜索名称、班级、负责人或 Agent ID", "aria-label": "搜索 Agent" });
        const status = el("select", { "aria-label": "Agent 状态" },
          ...[["", "全部状态"], ["available", "Agent 已创建"], ["missing", "未创建 / 不可用"], ["linked", "微信已绑定"]]
            .map(([value, title]) => el("option", { value }, title)));
        const agents = el("div", { class: "admin-agent-list" });
        const empty = emptyState("尚无匹配的 Agent", "班级由班主任通过创建向导建立；系统 Agent 请检查 Gateway 配置。");
        const panels = new Map();
        body.append(el("div", { class: "settings-toolbar" }, search, status), agents, empty);
        function filter() {
          const needle = search.value.trim().toLowerCase();
          const matches = selected.filter((agent) => `${agent.label} ${agent.identifier} ${agent.agent_id || ""} ${agent.owner?.username || ""} ${agent.owner?.display_name || ""}`.toLowerCase().includes(needle)
            && (!status.value || (status.value === "available" ? agent.present : status.value === "missing" ? !agent.present : agent.binding?.status === "linked")));
          empty.classList.toggle("hidden", matches.length > 0);
          for (const agent of matches) if (!panels.has(agent.identifier)) {
            const panel = agentPanel(agent, view, { onRefresh: load }); panels.set(agent.identifier, panel); agents.append(panel);
          }
          for (const [identifier, panel] of panels) panel.classList.toggle("hidden", !matches.some((agent) => agent.identifier === identifier));
        }
        search.addEventListener("input", filter); status.addEventListener("change", filter); filter();
      }
    } catch (error) { if (view.active) { clear(body); body.append(errorPanel(error, { onRetry: load })); } }
  }
  await load();
}
