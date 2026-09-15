import { api } from "./api.js";
import { el, toast } from "./util.js";
import { field, openModal, statusBadge } from "./components.js";

function selectModel(items, selected, emptyLabel, { speech = false } = {}) {
  const select = el("select", {}, el("option", { value: "", selected: !selected }, emptyLabel));
  let found = false;
  for (const item of items) {
    const unavailable = item.available === false || (speech && !item.configured);
    found ||= item.id === selected;
    select.append(el("option", {
      value: item.id,
      selected: item.id === selected,
      disabled: unavailable && item.id !== selected,
    }, `${item.name || item.id} · ${item.provider}${unavailable ? "（未配置凭据）" : ""}`));
  }
  if (selected && !found) select.append(el("option", { value: selected, selected: true }, `${selected}（当前配置不可用）`));
  select.dataset.initial = selected || "";
  return select;
}

export async function openAgentModelSettings(classId, { onSaved = null, isActive = () => true, onClosed = null } = {}) {
  let settings;
  try {
    settings = await api(`/classes/${classId}/agent-chat/models`);
  } catch (error) {
    if (isActive()) toast(error.message, "error");
    return;
  }
  if (!isActive()) return;
  const current = settings.configured;
  const main = selectModel(settings.models, current.main_model, `继承默认模型（${settings.effective.main_model || "未配置"}）`);
  const image = selectModel(settings.image_models, current.image_model, "跟随主模型 / OpenClaw 全局图片模型");
  const speech = selectModel(settings.speech_models, current.speech_model, "浏览器语音识别（不上传录音）", { speech: true });
  const body = el("div", { class: "agent-model-settings" },
    el("div", { class: "agent-model-summary" },
      el("div", {}, el("span", { class: "muted" }, "当前主模型"), el("code", {}, settings.effective.main_model || "—")),
      el("div", {}, el("span", { class: "muted" }, "图片模型"), el("code", {}, settings.effective.image_model)),
      el("div", {}, el("span", { class: "muted" }, "语音识别"), el("code", {}, settings.effective.speech_model))),
    el("div", { class: "agent-model-form" },
      field("主模型", main, "决定日常对话、班级查询和微信回复；保存后 Gateway 会短暂重启。"),
      field("图片理解模型", image, "仅在网页消息包含图片时覆盖主模型，并继续使用本班 Agent 的工具和权限。"),
      field("语音识别模型", speech, "选择后网页使用服务端 OpenClaw STT；未选择时使用浏览器自带识别。")),
    settings.speech_models.some((item) => item.configured)
      ? el("p", { class: "muted" }, "仅显示 OpenClaw 当前发现的模型；语音模型只允许选择已配置凭据的 Provider。")
      : el("div", { class: "agent-model-warning" }, statusBadge("pending", "尚无可用 STT Provider"), el("span", {}, "请先由管理员在 OpenClaw 配置语音 Provider 凭据。")));
  let modal;
  modal = openModal({
    title: `模型设置 · ${settings.agent_name}`,
    body,
    wide: true,
    onClose: onClosed,
    actions: [
      { label: "取消", kind: "secondary" },
      {
        label: "保存模型设置",
        kind: "primary",
        closeOnDone: false,
        onClick: async ({ setSubmitting }) => {
          if (!isActive()) { modal.close(); return false; }
          const values = { main_model: main.value || null, image_model: image.value || null, speech_model: speech.value || null };
          const changes = Object.fromEntries(Object.entries(values).filter(([key, value]) => value !== (current[key] || null)));
          if (!Object.keys(changes).length) {
            modal.close();
            return false;
          }
          setSubmitting(true);
          try {
            const result = await api(`/classes/${classId}/agent-chat/models`, { method: "PATCH", body: changes });
            if (!isActive()) { modal.close(); return false; }
            toast(result.restart_requested ? "模型设置已保存，Gateway 正在重启" : "模型设置已保存", "success");
            modal.close();
            if (onSaved) await onSaved(result);
          } catch (error) {
            if (isActive()) toast(error.message, "error");
            setSubmitting(false);
          }
          return false;
        },
      },
    ],
  });
  return modal;
}
