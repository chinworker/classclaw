import { el } from "../util.js";
import { pageHeader, errorPanel } from "../components.js";
import { adminView } from "../adminView.js";
import { navigate } from "../router.js";
import { renderSessions } from "./adminMaintenance.js";

const tabs = [
  ["usage", "用量", () => import("./adminUsage.js")],
  ["logs", "日志", () => import("./adminLogs.js")],
  ["maintenance", "备份与维护", () => import("./adminMaintenance.js")],
  ["database", "数据库", () => import("./adminDatabase.js")],
  ["audit", "审计", () => import("./adminAudit.js")],
];
let activeView = null;
export function dispose() { activeView?.dispose(); activeView = null; }
export async function render(mount, ctx = {}) {
  dispose(); const view = adminView(); activeView = view;
  const selected = tabs.find(([key]) => key === ctx.query?.tab) || tabs[0];
  const body = el("section", { class: "ops-tab-content", role: "tabpanel", "aria-label": selected[1] });
  mount.append(pageHeader("运维中心", "用量、日志、备份维护与数据排查。"),
    el("nav", { class: "tabs ops-tabs", "aria-label": "运维模块" }, ...tabs.map(([key, title]) => el("button", {
      type: "button", class: selected[0] === key ? "active" : "", "aria-pressed": String(selected[0] === key),
      onclick: () => navigate("/admin/ops", { tab: key }),
    }, title))), body);
  try {
    const module = await selected[2]();
    if (!view.active) return;
    view.own(() => module.dispose?.());
    await module.render(body, ctx);
    if (view.active && selected[0] === "logs") await renderSessions(body, view);
  } catch (error) { if (view.active) body.append(errorPanel(error)); }
}
