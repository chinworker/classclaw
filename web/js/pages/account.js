// 账户设置：资料、密码、本班 Agent 和 ClassClaw Channels。

import { el, clear, toast, fmtDateTime } from "../util.js";
import { changePassword } from "../auth.js";
import { state } from "../state.js";
import { api } from "../api.js";
import { openAgentModelSettings } from "../agentModelSettings.js";
import { pageHeader, field, fieldError, statusBadge, errorPanel, skeleton } from "../components.js";
import { classclawChannelsPanel } from "../classclawChannels.js";

let activeView = null;

export function dispose() {
  if (!activeView) return;
  activeView.qr?.dispose();
  activeView.modelDialog?.close();
  window.clearTimeout(activeView.refreshTimer);
  activeView = null;
}

export async function render(mount) {
  if (!mount.isConnected) return;
  dispose();
  const view = { qr: null, modelDialog: null, refreshTimer: null, bindingRevision: 0 };
  activeView = view;
  clear(mount);
  const user = state.user;
  const classId = state.classId;
  const live = () => activeView === view && mount.isConnected && state.user?.id === user.id && state.classId === classId;
  mount.append(pageHeader("账户设置", user.role === "head_teacher" ? "管理账号、密码与本班 Agent 的绑定和设置。" : "查看账号信息并修改密码。"));

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
      if (!live()) return;
      currentInput.value = newInput.value = confirmInput.value = "";
      toast("密码修改成功", "success");
      void render(mount); // 刷新初始密码状态
    } catch (error) {
      if (live()) errorLine.append(fieldError(error.message));
    } finally {
      submit.disabled = false;
    }
  });
  mount.append(el("div", { class: "card" }, el("h3", {}, "修改密码"), form));

  if (user.role !== "head_teacher") return;
  const agentHost = el("div", { class: "account-agent-settings" });
  mount.append(el("section", { class: "card", aria: { label: "班级 Agent 绑定与设置" } },
    el("h3", {}, "班级 Agent · 绑定与设置"),
    el("p", { class: "muted" }, "网页对话无需绑定渠道。主模型、图片理解和语音识别在此设置；每个对话的思考强度仍在该对话内独立选择。"),
    agentHost));
  const channelsHost = el("div", { class: "account-channels" });
  mount.append(el("section", { class: "card", aria: { label: "ClassClaw Channels" } },
    el("h3", {}, "ClassClaw Channels"), channelsHost));
  if (!classId) {
    channelsHost.append(el("p", { class: "muted" }, "创建班级和专属 Agent 后即可连接渠道。"));
    agentHost.append(el("p", { class: "muted" }, "尚未创建班级。创建班级后即可管理专属 Agent。"),
      el("a", { href: "#/onboarding" }, "前往创建班级"));
    return;
  }

  const memoryHost = el("div", { class: "account-agent-memories" });
  const memoryButton = el("button", { type: "button", class: "secondary" }, "查看记忆");
  let memoryRevision = 0;
  memoryButton.addEventListener("click", async () => {
    if (!live()) return;
    const revision = ++memoryRevision;
    memoryButton.disabled = true;
    clear(memoryHost).append(skeleton(2));
    try {
      const result = await api(`/classes/${classId}/agent-memories?include_expired=true`);
      if (!live() || revision !== memoryRevision) return;
      clear(memoryHost);
      if (!result.items.length) memoryHost.append(el("p", { class: "muted" }, "还没有已确认的记忆。可以在对话中告诉 Agent 作息表或固定偏好。"));
      for (const item of result.items) {
        memoryHost.append(el("p", {}, item.description, item.expired ? el("span", { class: "muted" }, " · 已过期，不再使用") : null));
      }
      memoryButton.textContent = "刷新记忆";
    } catch (error) {
      if (live()) clear(memoryHost).append(errorPanel(error));
    } finally { memoryButton.disabled = false; }
  });
  mount.append(el("section", { class: "card", aria: { label: "班级 Agent 记忆" } },
    el("h3", {}, "班级 Agent · 记忆"),
    el("p", { class: "muted" }, "Agent 会从你提供的作息、偏好和约定中提炼记忆，经你确认后用于后续对话。临时规则到期后自动恢复长期约定。"),
    el("p", { class: "muted" }, "需要更正或忘记时，直接在对话中告诉 Agent，并核对它给出的变化。"),
    el("div", { class: "row-gap" }, memoryButton, el("a", { href: "#/agent" }, "前往对话")), memoryHost));

  function showProvision() {
    clear(channelsHost).append(el("p", { class: "muted" }, "请先创建本班 Agent，再连接渠道。"));
    const button = el("button", { class: "primary", type: "button" }, "创建班级 Agent");
    const errorHost = el("div");
    button.addEventListener("click", async () => {
      if (!live()) return;
      button.disabled = true;
      button.textContent = "正在创建…";
      clear(errorHost);
      try {
        await api(`/classes/${classId}/agent-binding/provision`, { method: "POST", body: {} });
        if (live()) await loadAgent();
      } catch (error) {
        if (live()) errorHost.append(errorPanel(error));
      } finally {
        button.disabled = false;
        button.textContent = "创建班级 Agent";
      }
    });
    agentHost.append(el("p", { class: "muted" }, "本班专属 Agent 尚未创建。"), button, errorHost);
  }

  async function loadAgent() {
    if (!live()) return;
    const revision = ++view.bindingRevision;
    const current = () => live() && revision === view.bindingRevision;
    view.qr?.dispose();
    view.qr = null;
    clear(agentHost).append(skeleton(3));
    clear(channelsHost).append(skeleton(2));
    let binding;
    try {
      binding = await api(`/classes/${classId}/agent-binding`);
    } catch (error) {
      if (!current()) return;
      clear(agentHost);
      clear(channelsHost).append(el("p", { class: "muted" }, "暂时无法读取渠道信息，请重试加载 Agent。"));
      if (error.code === "NOT_FOUND") showProvision();
      else agentHost.append(errorPanel(error, { onRetry: loadAgent }));
      return;
    }
    if (!current()) return;
    clear(agentHost);
    if (!binding.openclaw_agent_id) { showProvision(); return; }
    const modelButton = el("button", { class: "secondary agent-model-button", type: "button" }, "模型设置");
    modelButton.addEventListener("click", async () => {
      if (!live() || view.modelDialog) return;
      modelButton.disabled = true;
      try {
        const dialog = await openAgentModelSettings(classId, {
          isActive: live,
          onClosed: () => { if (view.modelDialog === dialog) view.modelDialog = null; },
          onSaved: (result) => {
            if (!live()) return;
            window.clearTimeout(view.refreshTimer);
            view.refreshTimer = window.setTimeout(() => { if (live()) void loadAgent(); }, result.restart_requested ? 1600 : 0);
          },
        });
        if (dialog) view.modelDialog = dialog;
      } finally { modelButton.disabled = false; }
    });
    agentHost.append(
      el("p", {}, el("b", {}, binding.agent_name)),
      binding.last_error ? el("p", { class: "field-error" }, `最近错误：${binding.last_error}`) : null,
      el("div", { class: "row-gap" }, modelButton, el("a", { href: "#/agent" }, "进入班级 Agent")));
    // An account alias exists as soon as QR login starts; it is not proof of a
    // completed binding. Always keep the explicit (re)binding action available.
    view.qr = classclawChannelsPanel(classId, { binding, onDone: () => { if (live()) void loadAgent(); } });
    clear(channelsHost).append(view.qr.el);
  }

  await loadAgent();
}
