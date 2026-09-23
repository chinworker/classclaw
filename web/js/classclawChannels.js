// ClassClaw Channels is the product entry point; each provider owns its login UI.
// The current API stores one WeChat connection on the Agent binding. Do not
// present it as a multi-connection API until the backend migration is complete.
import { el, fmtDateTime } from "./util.js";
import { qrBindingPanel, statusBadge } from "./components.js";

export function classclawChannelsPanel(classId, { binding = null, onDone = null } = {}) {
  const box = el("div", { class: "classclaw-channels-panel" },
    el("p", { class: "muted" }, "通过已开放的渠道与本班 Agent 对话。目前支持微信，后续将开放更多渠道及同一 Agent 的多渠道绑定。"));
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
