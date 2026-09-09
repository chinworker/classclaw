import { el, clear, toast, fmtDateTime } from "../util.js";
import { api } from "../api.js";
import { pageHeader, confirmDanger, errorPanel, jsonDetails, skeleton, statusBadge } from "../components.js";
import { clearSession } from "../state.js";

export async function render(mount, ctx = {}) {
  if (ctx.path === "/admin/openclaw/maintenance") { await renderSessions(mount); return; }
  const host = el("div");
  const resultHost = el("div");
  mount.append(pageHeader("备份与系统维护", "服务器备份与恢复步骤，以及完整系统初始化。"), host);

  async function load() {
    clear(host); host.append(skeleton(6));
    try {
      clear(host);
      host.append(el("section", { class: "card config-file-guide" }, el("h3", {}, "备份与恢复"),
        el("p", {}, "在服务器项目目录、已激活的 Python 环境中执行。备份包含业务库、独立用量库和附件，工作区、OpenClaw 状态与配置文件需另外备份。"),
        el("p", {}, "创建备份：", el("code", {}, "python scripts/backup.py ./backups")),
        el("p", {}, "恢复：先停止 ClassClaw，核对备份目录，再执行 ", el("code", {}, "python scripts/restore.py ./backups/classclaw-backup-YYYYMMDD-HHMMSS")),
        el("p", { class: "muted" }, "恢复会覆盖业务库、用量库及附件；双库不能一起原子替换，务必先停服务。完成后重启并检查 /health 和使用量页面。"),
        el("p", {}, "检查无引用附件（只报告）：", el("code", {}, "python scripts/cleanup_attachments.py")),
        el("a", { href: "#/admin/settings" }, "查看当前数据路径与系统配置")));
      host.append(el("div", { class: "card danger-zone" },
        el("div", { class: "setting-row" },
          el("div", {}, el("h3", {}, "完整系统初始化"), el("p", { class: "muted" }, "删除全部旧数据、账号、会话、数据库运行状态、日志、统计、附件和班级智能体；不会修改 classclaw.toml 或 .env。只重新生成默认管理员，并保留 Main 与数据提取两个默认智能体。")),
          el("button", { class: "danger", type: "button", onclick: initialize }, "完整初始化")),
        resultHost));
    } catch (error) { clear(host); host.append(errorPanel(error, { onRetry: load })); }
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

async function renderSessions(mount) {
  const host = el("div", { class: "card" });
  const resultHost = el("div");
  const preview = el("button", { class: "secondary", type: "button", onclick: dryRun }, "预览会话维护（不删除）");
  mount.append(pageHeader("OpenClaw 会话维护", "查看定时维护状态，使用 OpenClaw 原生维护策略进行只读预览。",
    el("button", { class: "secondary", type: "button", onclick: load }, "刷新")), host,
    el("div", { class: "card" }, el("h3", {}, "维护预览"),
      el("p", { class: "muted" }, "提取会话是一次性的，班级聊天会话长期复用。实际清理范围取决于服务器 OpenClaw 维护策略，请先核对预览结果。"),
      preview, resultHost));
  async function load() {
    clear(host); host.append(skeleton(3));
    try {
      const status = await api("/admin/openclaw/sessions/cleanup");
      clear(host);
      host.append(el("h3", {}, "定时维护状态"), statusBadge(status.enabled ? "active" : "inactive", status.enabled ? "已启用" : "已关闭"),
        el("p", {}, `间隔：${status.interval_hours} 小时`),
        el("p", {}, `上次执行：${status.last_run_at ? fmtDateTime(status.last_run_at * 1000) : "本进程尚未执行"}`),
        status.last_result ? jsonDetails(status.last_result, "上次结果") : null,
        el("a", { href: "#/admin/openclaw/settings" }, "修改维护间隔与 CLI 配置"));
    } catch (error) { clear(host); host.append(errorPanel(error, { onRetry: load })); }
  }
  async function dryRun() {
    preview.disabled = true; clear(resultHost);
    try {
      const result = await api("/admin/openclaw/sessions/cleanup?enforce=false", { method: "POST" });
      resultHost.append(el("p", {}, result.ok ? "预览完成，未执行删除。" : "预览失败，请检查 CLI 与 OpenClaw 配置。"),
        el("pre", { class: "admin-error-log" }, result.output || "无输出"));
      await load();
    } catch (error) { resultHost.append(errorPanel(error)); }
    finally { preview.disabled = false; }
  }
  await load();
}
