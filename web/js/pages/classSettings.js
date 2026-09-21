// 班级设置：编辑班级资料、停用班级、彻底删除。

import { el, clear, toast } from "../util.js";
import { api } from "../api.js";
import { appConfig } from "../config.js";
import { refreshClassInfo, refreshIdentity } from "../state.js";
import { navigate } from "../router.js";
import { pageHeader, field, errorPanel, skeleton, confirmDanger, statusBadge } from "../components.js";
import { deleteClass } from "../featureGaps.js";

export async function render(mount, ctx, helpers) {
  clear(mount);
  mount.append(skeleton(4));
  let cls;
  try {
    cls = await refreshClassInfo();
  } catch (error) {
    clear(mount);
    mount.append(errorPanel(error, { onRetry: () => render(mount, ctx, helpers) }));
    return;
  }
  clear(mount);
  mount.append(pageHeader("班级设置", `当前班级：${cls.name}（班主任账号唯一班级）`));

  /* ---------- 基本资料 ---------- */
  const inputs = {};
  const specs = [
    ["name", "班级名称 *"], ["grade", "年级 *"], ["head_teacher", "班主任"], ["room", "教室"],
    ["semester_name", "学期名称"], ["semester_start", "开学日期"], ["semester_end", "学期结束"],
  ];
  const grid = el("div", { class: "form-grid" });
  for (const [key, label] of specs) {
    const input = el("input", {
      type: key.startsWith("semester_") && key !== "semester_name" ? "date" : "text",
      value: cls[key] || appConfig.semester_defaults?.[key] || "",
    });
    inputs[key] = input;
    grid.append(field(label, input));
  }
  const saveBtn = el("button", { class: "primary", type: "button" }, "保存班级资料");
  saveBtn.addEventListener("click", async () => {
    const body = {};
    for (const [key, input] of Object.entries(inputs)) body[key] = input.value.trim() || null;
    if (!body.name || !body.grade) { toast("班级名称和年级不能为空", "error"); return; }
    saveBtn.disabled = true;
    try {
      await api(`/classes/${cls.id}`, { method: "PATCH", body });
      toast("班级资料已保存", "success");
      await refreshClassInfo();
      helpers.refreshShell();
    } catch (error) {
      if (error.code === "CLASS_NAME_CONFLICT") toast(`班级名称冲突：${error.message}`, "error");
      else toast(error.message, "error");
    } finally { saveBtn.disabled = false; }
  });
  mount.append(el("div", { class: "card" },
    el("h3", {}, "基本资料"),
    el("p", {}, el("b", {}, "状态："), statusBadge(cls.status), el("span", { class: "muted", style: { marginLeft: "8px" } }, `学期：${cls.semester_name || "未设置"}`)),
    el("p", { class: "muted" }, "未设置学期时优先使用当前学期；寒暑假期间使用未来最近的一学期。日期由服务端按农历自动换算。"),
    grid, saveBtn));

  /* ---------- 危险区 ---------- */
  const deactivateBtn = el("button", { class: "secondary", type: "button", disabled: cls.status === "inactive" }, "停用班级");
  deactivateBtn.addEventListener("click", async () => {
    const ok = await confirmDanger({
      title: `停用班级「${cls.name}」`,
      lines: [
        "停用后该班级不再出现在活跃业务中，但数据完整保留。",
        "停用不是删除：学生、成绩、考勤等记录仍可恢复。",
        "管理员可随时将状态改回启用。",
      ],
      confirmLabel: "停用班级",
    });
    if (!ok) return;
    try {
      await api(`/classes/${cls.id}/deactivate`, { method: "POST", body: {} });
      toast("班级已停用", "success");
      render(mount, ctx, helpers);
    } catch (error) { toast(error.message, "error"); }
  });

  const deleteBtn = el("button", { class: "danger", type: "button" }, "彻底删除班级");
  deleteBtn.addEventListener("click", async () => {
    let students = 0;
    try { students = (await api(`/students?class_id=${cls.id}&page_size=1`)).total ?? 0; } catch { /* 忽略计数失败 */ }
    const ok = await confirmDanger({
      title: `彻底删除班级「${cls.name}」`,
      lines: [
        `班级：${cls.name}（${cls.grade}），包含 ${students} 名学生。`,
        "班级及全部相关记录会永久删除。",
        "删除后当前账号恢复为无班级状态，可重新创建班级。",
      ],
      requireText: cls.name,
      confirmLabel: "彻底删除",
    });
    if (!ok) return;
    try {
      await deleteClass(cls.id);
      toast("班级已彻底删除", "success");
      await refreshIdentity();
      helpers.refreshShell();
      navigate("/welcome");
    } catch (error) {
      toast(`删除失败：${error.message}（${error.code}）`, "error");
    }
  });

  mount.append(el("div", { class: "card", style: { borderColor: "#EAC9C4" } },
    el("h3", { style: { color: "var(--danger)" } }, "危险区"),
    el("p", { class: "muted" }, "停用会保留数据。彻底删除无法恢复。"),
    el("div", { class: "row-gap" }, deactivateBtn, deleteBtn)));
}
