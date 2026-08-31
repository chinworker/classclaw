// 管理员 · 用户管理：创建班主任、停用/启用、重置密码、分配历史班级。

import { el, clear, toast, fmtDateTime } from "../util.js";
import { api } from "../api.js";
import {
  pageHeader, dataTable, statusBadge, openModal, confirmDanger, field, fieldError, errorPanel, skeleton, emptyState,
} from "../components.js";

export async function render(mount) {
  const listHost = el("div");
  mount.append(
    pageHeader("用户管理", "管理员是创建账号的唯一入口；系统不提供公开注册。",
      el("button", { class: "primary", type: "button", onclick: openCreateModal }, "添加班主任")),
    listHost);

  async function load() {
    clear(listHost);
    listHost.append(skeleton(4));
    let users;
    let classNames = new Map();
    try {
      [users] = await Promise.all([api("/admin/users")]);
      const classes = await api("/classes?page_size=100").catch(() => null);
      if (classes?.items) classNames = new Map(classes.items.map((c) => [c.id, c.name]));
    } catch (error) {
      clear(listHost);
      listHost.append(errorPanel(error, { onRetry: load }));
      return;
    }
    clear(listHost);
    if (!users.length) { listHost.append(emptyState("暂无用户", "点击“添加班主任”创建第一个班主任账号。")); return; }
    listHost.append(dataTable({
      columns: [
        { key: "username", label: "账号", render: (u) => el("div", {}, el("b", {}, u.username), u.must_change_password ? el("div", {}, el("span", { class: "tag tag-warn" }, "初始密码")) : null) },
        { key: "role", label: "角色", render: (u) => (u.role === "admin" ? "管理员" : "班主任") },
        { key: "display_name", label: "显示名", render: (u) => u.display_name || "—" },
        { key: "class_id", label: "班级", render: (u) => (u.class_id ? classNames.get(u.class_id) || u.class_id : "未创建") },
        { key: "is_active", label: "状态", render: (u) => statusBadge(u.is_active ? "active" : "inactive") },
        { key: "last_login_at", label: "最近登录", render: (u) => fmtDateTime(u.last_login_at) },
        { key: "actions", label: "操作", render: (u) => renderActions(u) },
      ],
      rows: users,
    }));
  }

  function renderActions(user) {
    if (user.role === "admin") return el("span", { class: "muted" }, "唯一管理员（不可在此停用或重置）");
    const box = el("div", { class: "row-gap" });
    box.append(el("button", { class: "secondary", type: "button", onclick: () => openEditModal(user) }, "编辑"));
    box.append(el("button", {
      class: "secondary", type: "button",
      onclick: async () => {
        const ok = await confirmDanger({
          title: `重置「${user.username}」的密码`,
          lines: [
            `密码将被重置为初始值 32767。`,
            `该用户的所有现有登录会话将立即失效。`,
            `用户下次登录后会被要求修改密码。`,
          ],
          confirmLabel: "重置密码",
        });
        if (!ok) return;
        try {
          await api(`/admin/users/${user.id}/reset-password`, { method: "POST", body: {} });
          toast(`已重置 ${user.username} 的密码为 32767`, "success");
          load();
        } catch (error) { toast(error.message, "error"); }
      },
    }, "重置密码"));
    box.append(el("button", {
      class: "secondary", type: "button",
      onclick: async () => {
        const target = !user.is_active;
        if (!target) {
          const ok = await confirmDanger({
            title: `停用账号「${user.username}」`,
            lines: [`停用后该用户将无法登录，所有会话立即失效。`, `班级数据保留，可随时重新启用。`],
            confirmLabel: "停用账号",
          });
          if (!ok) return;
        }
        try {
          await api(`/admin/users/${user.id}`, { method: "PATCH", body: { is_active: target } });
          toast(target ? "账号已启用" : "账号已停用", "success");
          load();
        } catch (error) { toast(error.message, "error"); }
      },
    }, user.is_active ? "停用" : "启用"));
    box.append(el("button", { class: "secondary", type: "button", onclick: () => openAssignModal(user) }, "分配班级"));
    box.append(el("button", { class: "text-button danger-text", type: "button", onclick: () => deleteUser(user) }, "删除"));
    return box;
  }

  function openEditModal(user) {
    const usernameInput = el("input", { type: "text", value: user.username, maxlength: "100", autocomplete: "off" });
    const displayInput = el("input", { type: "text", value: user.display_name || "", maxlength: "100", autocomplete: "off" });
    const activeInput = el("input", { type: "checkbox", checked: user.is_active });
    const body = el("div", {},
      field("用户名", usernameInput, "修改后原用户名立即失效；用户名全局唯一。"),
      field("显示名", displayInput),
      el("label", { class: "field check-field" }, activeInput, el("span", {}, "允许登录")));
    openModal({
      title: `编辑用户 · ${user.username}`, body,
      actions: [{ label: "取消", kind: "secondary" }, { label: "保存", kind: "primary", onClick: async ({ close, setSubmitting }) => {
        const username = usernameInput.value.trim();
        if (username.length < 2 || /\s/.test(username)) { toast("用户名至少 2 个字符且不能包含空格", "error"); return; }
        setSubmitting(true);
        try {
          await api(`/admin/users/${user.id}`, { method: "PATCH", body: { username, display_name: displayInput.value.trim() || null, is_active: activeInput.checked } });
          close(); toast("用户资料已更新", "success"); load();
        } catch (error) { toast(error.message, "error"); } finally { setSubmitting(false); }
      } }],
    });
  }

  async function deleteUser(user) {
    const accepted = await confirmDanger({
      title: `删除用户「${user.username}」`,
      lines: ["该账号和全部登录会话将被删除。", user.class_id ? "现有班级和业务数据保留，但会变为未分配状态。" : "该账号当前没有班级。", "管理员账号不能删除。"],
      requireText: user.username,
      confirmLabel: "删除用户",
    });
    if (!accepted) return;
    try { await api(`/admin/users/${user.id}`, { method: "DELETE" }); toast("用户已删除", "success"); load(); }
    catch (error) { toast(error.message, "error"); }
  }

  function openCreateModal() {
    const usernameInput = el("input", { type: "text", autocomplete: "off", maxlength: "100", placeholder: "登录用户名，不含空格" });
    const displayInput = el("input", { type: "text", autocomplete: "off", maxlength: "100", placeholder: "例如：李老师" });
    const passwordInput = el("input", { type: "text", autocomplete: "off", value: "32767", maxlength: "128" });
    const usernameError = el("div");
    const body = el("div", {},
      field("用户名 *", usernameInput, "至少 2 个字符，不能包含空格"),
      usernameError,
      field("显示名", displayInput),
      field("初始密码", passwordInput, "默认 32767；用户首次登录后必须修改"),
    );
    openModal({
      title: "添加班主任账号",
      body,
      actions: [
        { label: "取消", kind: "secondary" },
        {
          label: "创建", kind: "primary",
          onClick: async ({ close, setSubmitting }) => {
            clear(usernameError);
            const username = usernameInput.value.trim();
            if (username.length < 2 || /\s/.test(username)) {
              usernameError.append(fieldError("用户名至少 2 个字符且不能包含空格"));
              return;
            }
            setSubmitting(true);
            try {
              await api("/admin/users", {
                method: "POST",
                body: { username, display_name: displayInput.value.trim() || null, password: passwordInput.value },
              });
              close();
              toast(`班主任账号 ${username} 已创建，初始密码 32767（仅本次提示，请线下转交）`, "success");
              load();
            } catch (error) {
              if (error.code === "USERNAME_CONFLICT") usernameError.append(fieldError("用户名已存在，请更换"));
              else toast(error.message, "error");
            } finally { setSubmitting(false); }
          },
        },
      ],
    });
  }

  async function openAssignModal(user) {
    if (user.class_id) { toast("该账号已有班级；一个班主任账号只能绑定一个班级，不能重复分配", "error"); return; }
    let classes;
    try {
      classes = (await api("/classes?page_size=100")).items || [];
    } catch (error) { toast(error.message, "error"); return; }
    const available = classes.filter((c) => !c.owner_user_id);
    if (!available.length) { toast("没有未分配的历史班级", "info"); return; }
    const select = el("select", {}, available.map((c) => el("option", { value: c.id }, `${c.name}（${c.grade}）`)));
    openModal({
      title: `为「${user.username}」分配历史班级`,
      body: el("div", {},
        el("p", { class: "muted" }, "仅用于迁移前已存在、尚无负责人的历史班级。分配后该账号登录将直接进入此班级。"),
        field("选择班级", select)),
      actions: [
        { label: "取消", kind: "secondary" },
        {
          label: "分配", kind: "primary",
          onClick: async ({ close, setSubmitting }) => {
            setSubmitting(true);
            try {
              await api(`/admin/users/${user.id}/assign-class`, { method: "POST", body: { class_id: select.value } });
              close();
              toast("班级已分配", "success");
              load();
            } catch (error) {
              toast(error.code === "CLASS_ALREADY_ASSIGNED" ? "该班级已分配给其他班主任" : error.message, "error");
            } finally { setSubmitting(false); }
          },
        },
      ],
    });
  }

  await load();
}
