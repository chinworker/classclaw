// ClassClaw Channels includes built-in web Chat and external providers.
// The current API stores one WeChat connection on the Agent binding. Do not
// present it as a multi-connection API until the backend migration is complete.
import { el, fmtDateTime } from "./util.js";
import { qrBindingPanel, statusBadge } from "./components.js";

export function classclawChannelsPanel(classId, { binding = null, onDone = null, agentReady = true, allowChatNavigation = true } = {}) {
  const box = el("div", { class: "classclaw-channels-panel" },
    el("p", { class: "muted" }, "通过网页 Chat 或微信与本班 Agent 对话，共享班级记忆。后续将开放更多外部渠道及多个外部账号绑定。"));
  box.append(el("section", { class: "classclaw-channel classclaw-web-channel", aria: { label: "网页 Chat 渠道" } },
    el("div", { class: "row-gap" }, el("h4", {}, "网页 Chat"), statusBadge(agentReady ? "ok" : "attention", agentReady ? "默认启用" : "待创建 Agent")),
    el("p", { class: "muted" }, "内置渠道，无需扫码或绑定账号。创建本班 Agent 后即可在网页中对话。"),
    agentReady && allowChatNavigation ? el("a", { href: "#/agent" }, "进入网页 Chat") : null));
  const channelId = binding?.channel_id || "openclaw-weixin";
  if (channelId !== "openclaw-weixin") {
    box.append(el("p", { class: "muted" }, "当前渠道尚未开放网页绑定。"));
    return { el: box, stop() {}, dispose() {} };
  }
  const linked = binding?.status === "linked" && Boolean(binding.channel_account_id);
  const panel = qrBindingPanel(classId, { onDone });
  box.append(el("section", { class: "classclaw-channel", aria: { label: "微信渠道" } },
    el("div", { class: "row-gap" }, el("h4", {}, "微信"), statusBadge(linked ? "ok" : "attention", linked ? "已连接" : "待连接")),
    el("p", { class: "muted" }, linked
      ? `微信已绑定${binding.linked_at ? ` · ${fmtDateTime(binding.linked_at)}` : ""}，需要时可重新扫码绑定。`
      : "微信尚未绑定完成，请点击下方按钮生成二维码并扫码。"),
    binding?.last_error ? el("p", { class: "field-error" }, `最近错误：${binding.last_error}`) : null,
    panel.el));
  return { el: box, stop: panel.stop, dispose: panel.dispose };
}
