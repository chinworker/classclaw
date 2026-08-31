import { el, clear, toast } from "../util.js";
import { api } from "../api.js";
import { pageHeader, confirmDanger, errorPanel, jsonDetails, skeleton, statusBadge } from "../components.js";

export async function render(mount) {
  const host = el("div");
  const resultHost = el("div");
  mount.append(pageHeader("功能与常量", "运行时配置保存在 system_settings；仅白名单项目可修改，密钥和环境变量不在此暴露。"), host);

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
          el("div", {}, el("h3", {}, "系统初始化"), el("p", { class: "muted" }, "删除全部班级、班级业务数据、待处理草稿、附件和 OpenClaw 班级智能体。账号、系统配置、审计和 Token 统计保留。")),
          el("button", { class: "danger", type: "button", onclick: initialize }, "初始化班级数据")),
        resultHost));
    } catch (error) { clear(host); host.append(errorPanel(error, { onRetry: load })); }
  }

  function settingRow(item) {
    const input = item.type === "boolean"
      ? el("input", { type: "checkbox", checked: item.value })
      : el("input", { type: "number", value: item.value, min: item.minimum, max: item.maximum, step: "1" });
    const button = el("button", { class: "secondary", type: "button" }, "保存");
    button.addEventListener("click", async () => {
      button.disabled = true;
      const value = item.type === "boolean" ? input.checked : Number(input.value);
      try { await api(`/admin/settings/${encodeURIComponent(item.key)}`, { method: "PUT", body: { value } }); toast(`${item.label}已保存`, "success"); }
      catch (error) { toast(error.message, "error"); }
      finally { button.disabled = false; }
    });
    return el("div", { class: "setting-row" },
      el("div", {}, el("div", { class: "row-gap" }, el("b", {}, item.label), el("code", {}, item.key)), el("p", { class: "muted" }, item.description)),
      el("div", { class: `setting-control ${item.type}` }, input, button));
  }

  async function initialize() {
    const accepted = await confirmDanger({
      title: "初始化全部班级数据",
      lines: ["所有班级及学生、课表、作业、考勤等业务数据将被彻底删除。", "班级附件与 OpenClaw 班级智能体将被清理。", "用户账号、系统配置、审计日志和 Token 统计保留。"],
      requireText: "INITIALIZE", confirmLabel: "执行初始化",
    });
    if (!accepted) return;
    clear(resultHost);
    resultHost.append(el("div", { class: "blocked-panel" }, el("b", {}, "初始化执行中"), el("span", {}, "正在逐个清理班级数据库和 OpenClaw 资源，请勿关闭页面。")));
    try {
      const result = await api("/admin/system/initialize", { method: "POST", body: { confirmation: "INITIALIZE" } });
      clear(resultHost);
      resultHost.append(el("div", { class: `init-result ${result.status}` },
        el("div", { class: "row-gap" }, el("h3", {}, "初始化状态"), statusBadge(result.status === "completed" ? "active" : result.status === "partial" ? "pending" : "failed", result.status.toUpperCase())),
        el("p", {}, `已删除班级 ${result.deleted_classes} 个；失败 ${result.failed_classes} 个；外部清理错误 ${result.errors.length} 条。`),
        jsonDetails(result, "查看完整初始化报告")));
      toast(result.status === "completed" ? "班级数据初始化完成" : "初始化完成，但存在部分清理错误", result.status === "completed" ? "success" : "error");
    } catch (error) {
      clear(resultHost); resultHost.append(errorPanel(error)); toast(error.message, "error");
    }
  }
  await load();
}
