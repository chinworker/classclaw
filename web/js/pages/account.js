// 账户设置：资料展示与修改密码。

import { el, clear, toast, fmtDateTime } from "../util.js";
import { changePassword } from "../auth.js";
import { state } from "../state.js";
import { pageHeader, field, fieldError, statusBadge } from "../components.js";

export async function render(mount) {
  clear(mount);
  const user = state.user;
  mount.append(pageHeader("账户设置", "查看账号信息并修改密码。"));

  mount.append(el("div", { class: "card" },
    el("h3", {}, "账号信息"),
    el("p", {}, el("b", {}, "用户名："), user.username),
    el("p", {}, el("b", {}, "显示名："), user.display_name || "未设置"),
    el("p", {}, el("b", {}, "角色："), user.role === "admin" ? "管理员" : "班主任"),
    el("p", {}, el("b", {}, "最近登录："), fmtDateTime(user.last_login_at)),
    user.must_change_password ? el("p", {}, statusBadge("attention", "建议在下方设置自己的密码")) : null));

  const currentInput = el("input", { type: "password", autocomplete: "current-password" });
  const newInput = el("input", { type: "password", autocomplete: "new-password" });
  const confirmInput = el("input", { type: "password", autocomplete: "new-password" });
  const errorLine = el("div");
  const submit = el("button", { class: "primary", type: "submit" }, "修改密码");
  const form = el("form", {},
    field("当前密码", currentInput),
    field("新密码", newInput, "至少 8 个字符，且不能与当前密码相同"),
    field("再次输入新密码", confirmInput),
    errorLine,
    submit);
  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    clear(errorLine);
    if (newInput.value.length < 8) { errorLine.append(fieldError("新密码至少 8 个字符")); return; }
    if (newInput.value !== confirmInput.value) { errorLine.append(fieldError("两次输入的新密码不一致")); return; }
    submit.disabled = true;
    try {
      await changePassword(currentInput.value, newInput.value);
      currentInput.value = newInput.value = confirmInput.value = "";
      toast("密码修改成功", "success");
      render(mount); // 刷新初始密码状态
    } catch (error) {
      errorLine.append(fieldError(error.message));
    } finally {
      submit.disabled = false;
    }
  });
  mount.append(el("div", { class: "card" }, el("h3", {}, "修改密码"), form));
}
