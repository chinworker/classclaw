import { el, clear, toast } from "../util.js";
import { api } from "../api.js";
import { pageHeader, confirmDanger, errorPanel, jsonDetails, skeleton, statusBadge } from "../components.js";
import { clearSession } from "../state.js";

export async function render(mount) {
  const host = el("div");
  const resultHost = el("div");
  mount.append(pageHeader("静态配置", "显示当前启动配置。请停机修改 classclaw.toml 或环境变量，校验后重启服务；密钥不会在此暴露。"), host);

  async function load() {
    clear(host); host.append(skeleton(6));
    try {
      const rows = await api("/admin/settings"); clear(host);
      for (const group of ["features", "constants"]) {
        const items = rows.filter((row) => row.group === group);
        host.append(el("div", { class: "card" }, el("h3", {}, group === "features" ? "功能开关" : "运行常量"),
          el("div", { class: "settings-list" }, items.map(settingRow))));
      }
      host.append(el("div", { class: "card danger-zone" },
        el("div", { class: "setting-row" },
          el("div", {}, el("h3", {}, "完整系统初始化"), el("p", { class: "muted" }, "删除全部旧数据、账号、会话、数据库运行状态、日志、统计、附件和班级智能体；不会修改 classclaw.toml 或 .env。只重新生成默认管理员，并保留 Main 与数据提取两个默认智能体。")),
          el("button", { class: "danger", type: "button", onclick: initialize }, "完整初始化")),
        resultHost));
    } catch (error) { clear(host); host.append(errorPanel(error, { onRetry: load })); }
  }

  function settingRow(item) {
    const value = item.type === "boolean"
      ? statusBadge(item.value ? "active" : "inactive", item.value ? "已开启" : "已关闭")
      : el("code", {}, String(item.value));
    return el("div", { class: "setting-row" },
      el("div", {},
        el("div", { class: "row-gap" }, el("b", {}, item.label), el("code", {}, item.config_path)),
        el("p", { class: "muted" }, item.description),
        el("small", { class: "muted" }, "来源：启动配置 · 修改后需要重启")),
      el("div", { class: `setting-control ${item.type}` }, value));
  }

  async function initialize() {
    const accepted = await confirmDanger({
      title: "完整初始化系统",
      lines: [
        "全部班级、学生、业务记录、草稿和附件将被彻底删除。",
        "全部用户、登录会话、数据库运行状态、审计日志、Token 和调用统计将被删除；静态配置文件保持不变。",
        "全部班级智能体及微信绑定将被删除；只保留 Main 和数据提取两个默认智能体。",
        "完成后只存在新建的默认管理员，当前登录会立即失效。",
      ],
      requireText: "INITIALIZE", confirmLabel: "执行初始化",
    });
    if (!accepted) return;
    clear(resultHost);
    resultHost.append(el("div", { class: "blocked-panel" }, el("b", {}, "初始化执行中"), el("span", {}, "正在清空数据库、附件和班级智能体，请勿关闭页面。")));
    try {
      const result = await api("/admin/system/initialize", { method: "POST", body: { confirmation: "INITIALIZE" } });
      clear(resultHost);
      resultHost.append(el("div", { class: `init-result ${result.status}` },
        el("div", { class: "row-gap" }, el("h3", {}, "初始化状态"), statusBadge(result.status === "completed" ? "active" : result.status === "partial" ? "pending" : "failed", result.status.toUpperCase())),
        el("p", {}, `已删除数据库记录 ${result.database_rows_deleted} 条、班级 ${result.deleted_classes} 个；外部清理错误 ${result.errors.length} 条。`),
        el("p", { class: "muted" }, `新默认管理员：${result.default_admin.username}。请使用环境配置中的默认密码重新登录。`),
        jsonDetails(result, "查看完整初始化报告"),
        el("button", { class: "primary", type: "button", onclick: () => location.reload() }, "重新登录")));
      clearSession();
      toast(result.status === "completed" ? "系统完整初始化完成" : "系统数据已初始化，但存在外部清理错误", result.status === "completed" ? "success" : "error");
    } catch (error) {
      clear(resultHost); resultHost.append(errorPanel(error)); toast(error.message, "error");
    }
  }
  await load();
}
