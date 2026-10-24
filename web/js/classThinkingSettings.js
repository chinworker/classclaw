import { api } from "./api.js";
import { el, clear, toast } from "./util.js";
import { field, errorPanel, skeleton } from "./components.js";

export function thinkingControl(profile) {
  const saved = profile.class_default_level || "";
  const labels = new Map(profile.levels.map((item) => [item.id, item.label]));
  const select = el("select", { class: "class-thinking-select", aria: { label: "班级 Agent 默认思考强度" } },
    el("option", { value: "" }, `跟随系统默认（${labels.get(profile.system_default_level) || profile.system_default_level || "未设置"}）`),
    profile.levels.map((item) => el("option", { value: item.id }, item.label)));
  if (saved && !labels.has(saved)) select.append(el("option", { value: saved, disabled: true }, `${saved}（当前模型不支持）`));
  select.value = saved;
  const note = el("p", { class: "muted class-thinking-note" }, profile.default_adjusted
    ? `配置的默认档位不受当前模型支持，目前采用${labels.get(profile.default_level) || "模型有效默认"}。请重新选择可用档位。`
    : `当前默认：${labels.get(profile.default_level) || "未设置"}`);
  return { select, note, el: el("div", {}, field("班级 Agent 默认思考强度", select,
    "用于网页、微信等渠道；网页中的单个对话可独立覆盖。"), note) };
}

export function classThinkingSettings(classId, { isActive = () => true, signal = null } = {}) {
  const host = el("div", { class: "class-thinking-settings" });
  const controller = new AbortController();
  signal?.addEventListener("abort", () => controller.abort(), { once: true });
  let saved = "";
  let select = null;
  const live = () => !controller.signal.aborted && isActive();
  async function load() {
    if (!live()) return;
    clear(host).append(skeleton(2));
    try {
      const profile = await api(`/classes/${classId}/agent-chat/thinking`, { signal: controller.signal });
      if (!live()) return;
      saved = profile.class_default_level || "";
      const control = thinkingControl(profile);
      select = control.select;
      const save = el("button", { type: "button", class: "primary", disabled: true }, "保存默认思考强度");
      const note = control.note;
      const errors = el("div");
      select.addEventListener("change", () => { save.disabled = select.value === saved; });
      save.addEventListener("click", async () => {
        if (!live() || save.disabled) return;
        const selected = select.value;
        save.disabled = true; select.disabled = true; clear(errors);
        try {
          const result = await api(`/classes/${classId}/agent-chat/thinking`, {
            method: "PATCH", body: { thinking_default: selected || null }, signal: controller.signal,
          });
          if (!live()) return;
          saved = selected;
          note.textContent = result.restart_requested ? "已保存，智能服务正在重新加载配置。" : "已保存。";
          toast("班级默认思考强度已保存", "success");
        } catch (error) {
          if (live()) errors.append(errorPanel(error, { onRetry: load }));
        } finally {
          if (live()) { select.disabled = false; save.disabled = select.value === saved; }
        }
      });
      clear(host).append(control.el, save, errors);
    } catch (error) {
      if (live()) clear(host).append(errorPanel(error, { onRetry: load }));
    }
  }
  return { el: host, load, isDirty: () => Boolean(select && select.value !== saved), dispose: () => controller.abort() };
}
