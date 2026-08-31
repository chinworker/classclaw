// 班级助手与微信绑定。

import { el, clear, fmtDateTime } from "../util.js";
import { api } from "../api.js";
import { state, refreshOpenclaw } from "../state.js";
import { pageHeader, errorPanel, skeleton, statusBadge, qrBindingPanel, openclawBlocked, emptyState } from "../components.js";

export async function render(mount, ctx, helpers) {
  clear(mount);
  mount.append(pageHeader("班级助手与微信", "绑定或更换本班微信。也可不绑定。",
    el("button", { class: "secondary", type: "button", onclick: () => render(mount, ctx, helpers) }, "刷新")));
  const host = el("div");
  mount.append(host);
  host.append(skeleton(4));

  let status;
  try {
    status = await refreshOpenclaw({ force: true });
    helpers.refreshOpenclawDot();
  } catch (error) {
    clear(host);
    host.append(errorPanel(error, { onRetry: () => render(mount, ctx, helpers) }));
    return;
  }

  clear(host);
  if (state.user.role === "admin") {
    host.append(el("div", { class: "card" },
      el("h3", {}, "智能服务状态"),
      status.ready
        ? el("p", {}, statusBadge("active", "已连接"), el("span", { class: "muted", style: { marginLeft: "8px" } }, `Gateway：${status.gateway_url} · 主智能体：${status.agent_id}`))
        : openclawBlocked(status, () => render(mount, ctx, helpers)),
      el("p", { class: "muted" }, "全部班级助手请到“智能体管理”查看。")));
    return;
  }

  if (!state.classId) {
    host.append(el("div", { class: "card" }, emptyState("尚未创建班级", "创建班级后会自动生成专属智能体绑定记录。")));
    return;
  }

  // 班主任：展示本班绑定
  const bindHost = el("div", { class: "card" }, skeleton(3));
  host.append(bindHost);
  try {
    const binding = await api(`/classes/${state.classId}/agent-binding`);
    clear(bindHost);
    bindHost.append(
      el("h3", {}, "本班专属助手"),
      el("p", {},
        statusBadge(binding.status),
        el("span", { class: "muted", style: { marginLeft: "8px" } }, `展示名：${binding.agent_name}`)),
      el("p", { class: "muted", style: { fontSize: "13px" } },
        `微信：${binding.channel_account_id ? "已连接" : "尚未绑定"}`,
        binding.linked_at ? ` · 绑定于：${fmtDateTime(binding.linked_at)}` : ""),
      binding.last_error ? el("p", { class: "field-error" }, `最近错误：${binding.last_error}`) : null);
    if (binding.status !== "linked") {
      const panel = qrBindingPanel(state.classId, { onDone: () => render(mount, ctx, helpers) });
      bindHost.append(panel.el);
    } else {
      bindHost.append(el("p", {}, statusBadge("linked", "微信已绑定，可以直接发消息")));
    }
  } catch (error) {
    clear(bindHost);
    if (error.code === "NOT_FOUND") {
      const provisionBtn = el("button", { class: "primary", type: "button" }, "创建班级助手");
      provisionBtn.addEventListener("click", async () => {
        provisionBtn.disabled = true;
        try { await api(`/classes/${state.classId}/agent-binding/provision`, { method: "POST", body: {} }); render(mount, ctx, helpers); }
        catch (err) { bindHost.append(errorPanel(err)); provisionBtn.disabled = false; }
      });
      bindHost.append(el("h3", {}, "本班专属助手"), el("p", { class: "muted" }, "班级还没有专属助手。"), provisionBtn);
    } else {
      bindHost.append(errorPanel(error, { onRetry: () => render(mount, ctx, helpers) }));
    }
  }
}
