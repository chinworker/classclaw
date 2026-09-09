import { el, clear, fmtDateTime, toast } from "../util.js";
import { api } from "../api.js";
import { pageHeader, dataTable, statusBadge, openModal, confirmDanger, field, errorPanel, skeleton } from "../components.js";

export async function render(mount) {
  const host = el("div");
  let users = []; let classes = [];
  mount.append(pageHeader("班级管理", "管理员维护已有班级：修改信息、分配负责人和彻底删除。新班级由班主任账号通过创建向导创建。"), host);

  async function load() {
    clear(host); host.append(skeleton(5));
    try {
      const [classData, userData] = await Promise.all([api("/classes?page_size=100"), api("/admin/users")]);
      classes = classData.items || []; users = userData.filter((u) => u.role === "head_teacher");
      const ownerNames = new Map(users.map((u) => [u.id, u.display_name || u.username]));
      clear(host);
      host.append(dataTable({
        columns: [
          { key: "name", label: "班级", render: (c) => el("div", {}, el("b", {}, c.name), el("div", { class: "muted" }, `${c.grade}${c.room ? ` · ${c.room}` : ""}`)) },
          { key: "owner_user_id", label: "负责人", render: (c) => ownerNames.get(c.owner_user_id) || "未分配" },
          { key: "status", label: "状态", render: (c) => statusBadge(c.status) },
          { key: "students", label: "资源 ID", render: (c) => el("code", {}, c.id) },
          { key: "updated_at", label: "更新", render: (c) => fmtDateTime(c.updated_at) },
          { key: "actions", label: "操作", render: actions },
        ], rows: classes,
      }));
    } catch (error) { clear(host); host.append(errorPanel(error, { onRetry: load })); }
  }

  function ownerSelect(current = null) {
    const select = el("select", {}, el("option", { value: "" }, "未分配"));
    for (const user of users) {
      const occupied = user.class_id && user.class_id !== current?.id;
      select.append(el("option", { value: user.id, disabled: occupied, selected: current?.owner_user_id === user.id },
        `${user.display_name || user.username} · ${user.username}${occupied ? "（已有班级）" : ""}`));
    }
    return select;
  }

  function actions(cls) {
    return el("div", { class: "row-gap" },
      el("button", { class: "secondary", type: "button", onclick: () => openEdit(cls) }, "修改"),
      el("a", { href: `#/admin/agents?class_id=${encodeURIComponent(cls.id)}` }, "Agent 配置"),
      el("button", { class: "text-button danger-text", type: "button", onclick: () => remove(cls) }, "彻底删除"));
  }

  function openEdit(cls) {
    const name = el("input", { value: cls?.name || "", maxlength: "100", required: true });
    const grade = el("input", { value: cls?.grade || "", maxlength: "50", required: true, placeholder: "例如 高一" });
    const room = el("input", { value: cls?.room || "", maxlength: "100" });
    const teacher = el("input", { value: cls?.head_teacher || "", maxlength: "100" });
    const semester = el("input", { value: cls?.semester_name || "", maxlength: "100" });
    const status = el("select", {}, el("option", { value: "active", selected: cls?.status !== "inactive" }, "active"), el("option", { value: "inactive", selected: cls?.status === "inactive" }, "inactive"));
    const owner = ownerSelect(cls);
    const body = el("div", { class: "form-grid" }, field("名称 *", name), field("年级 *", grade), field("教室", room), field("班主任名称", teacher), field("学期", semester), field("负责人账号", owner), field("状态", status));
    openModal({
      title: `修改 ${cls.name}`, body, wide: true,
      actions: [{ label: "取消", kind: "secondary" }, { label: "保存", kind: "primary", onClick: async ({ close, setSubmitting }) => {
        if (!name.value.trim() || !grade.value.trim()) { toast("名称和年级不能为空", "error"); return; }
        setSubmitting(true);
        try {
          await api(`/classes/${cls.id}`, { method: "PATCH", body: { name: name.value.trim(), grade: grade.value.trim(), room: room.value.trim() || null, head_teacher: teacher.value.trim() || null, semester_name: semester.value.trim() || null, status: status.value } });
          if ((owner.value || null) !== (cls.owner_user_id || null)) await api(`/admin/classes/${cls.id}/owner`, { method: "PATCH", body: { owner_user_id: owner.value || null } });
          close(); toast("班级配置已保存", "success"); load();
        } catch (error) { toast(error.message, "error"); } finally { setSubmitting(false); }
      } }],
    });
  }

  async function remove(cls) {
    const accepted = await confirmDanger({ title: `彻底删除 ${cls.name}`, lines: ["将删除学生、课表、作业、考勤、附件关联等班级业务数据。", "将尝试移除 OpenClaw 智能体配置与工作目录。", "此操作不可恢复。"], requireText: cls.name, confirmLabel: "彻底删除" });
    if (!accepted) return;
    try { await api(`/classes/${cls.id}`, { method: "DELETE" }); toast("班级及关联资源已删除", "success"); load(); } catch (error) { toast(error.message, "error"); }
  }

  await load();
}
