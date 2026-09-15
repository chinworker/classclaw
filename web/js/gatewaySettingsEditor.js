import { el, clear, toast } from "./util.js";
import { errorPanel, skeleton, statusBadge } from "./components.js";
import { sourceEditor } from "./sourceEditor.js";
import { diffPreviewModal } from "./settingsEditor.js";

const valueAt = (object, path) => path.split(".").reduce((value, key) => value?.[key], object);
function setValue(object, path, value) {
  const parts = path.split("."); let group = object;
  for (const part of parts.slice(0, -1)) group = group[part] ||= {};
  group[parts.at(-1)] = value;
}
function changedPaths(old, next, prefix = "") {
  if (JSON.stringify(old) === JSON.stringify(next)) return [];
  if (old && next && typeof old === "object" && typeof next === "object" && !Array.isArray(old) && !Array.isArray(next)) {
    return [...new Set([...Object.keys(old), ...Object.keys(next)])].flatMap((key) => changedPaths(old[key], next[key], prefix ? `${prefix}.${key}` : key));
  }
  if (old === undefined && next && typeof next === "object" && !Array.isArray(next)) return Object.entries(next).flatMap(([key, value]) => changedPaths(undefined, value, prefix ? `${prefix}.${key}` : key));
  return [prefix];
}

export function gatewayChanges(snapshot, text) {
  let next;
  try { next = JSON.parse(text); }
  catch { throw new Error("JSON 语法错误，请检查引号、逗号和括号。"); }
  const paths = changedPaths(snapshot.config, next);
  const allowed = new Set(snapshot.fields.map((item) => item.path));
  for (const path of paths) {
    if (!allowed.has(path)) throw new Error(`只允许修改列出的 Gateway 字段：${path || "配置根节点"}`);
    const value = valueAt(next, path);
    const field = snapshot.fields.find((item) => item.path === path);
    if (field.type === "boolean" && typeof value !== "boolean" || field.enum && !field.enum.includes(value)) throw new Error(`${path} 的值无效`);
  }
  return Object.fromEntries(paths.map((path) => [path, valueAt(next, path)]));
}

export function gatewaySettingsEditor({ view }) {
  const root = el("div", { class: "settings-editor" });
  const host = el("div"); const messages = el("div", { "aria-live": "polite" });
  let snapshot = null; let text = ""; let savedText = ""; let mode = "form"; let source; let waiting = false;
  const search = el("input", { type: "search", placeholder: "搜索 Gateway 配置", "aria-label": "搜索 Gateway 配置" });
  const formButton = el("button", { type: "button", onclick: () => switchMode("form") }, "表单");
  const sourceButton = el("button", { type: "button", onclick: () => switchMode("source") }, "源码");
  const counter = el("b", { role: "status" });
  const save = el("button", { type: "button", class: "primary", disabled: true, onclick: preview }, "预览差异并保存");
  const discard = el("button", { type: "button", class: "secondary", disabled: true, onclick: () => { text = savedText; clear(messages); draw(); } }, "放弃全部");
  root.append(el("div", { class: "settings-toolbar" }, search, el("div", { class: "tabs" }, formButton, sourceButton)), messages, host,
    el("div", { class: "settings-savebar" }, counter, el("span", { class: "spacer" }), discard, save, statusBadge("pending", "Gateway 将重启")));
  search.addEventListener("input", () => { if (snapshot) draw(); });
  function update() {
    const dirty = text !== savedText;
    try { const changes = gatewayChanges(snapshot, text); counter.textContent = `已修改 ${Object.keys(changes).length} 项`; save.disabled = !Object.keys(changes).length; }
    catch { counter.textContent = "源码有未保存修改"; save.disabled = !dirty; }
    if (!dirty) counter.textContent = "所有修改已保存";
    save.disabled ||= waiting || !dirty || !snapshot?.hash;
    discard.disabled = waiting || !dirty;
  }
  function setWaiting(value) {
    waiting = value;
    for (const tag of ["input", "select", "textarea"]) for (const input of root.querySelectorAll(tag)) input.disabled = value;
    formButton.disabled = sourceButton.disabled = value;
    update();
  }
  function draw() {
    clear(host);
    formButton.classList.toggle("active", mode === "form"); sourceButton.classList.toggle("active", mode === "source");
    host.append(el("p", { class: "muted" }, "保存后 Gateway 会短暂重启。源码中的凭据已由后端脱敏，仅可编辑下列白名单字段。"));
    if (mode === "source") {
      source = sourceEditor(text, { label: "Gateway 脱敏 JSON 源码", onChange: (value) => { text = value; update(); } });
      host.append(source.el);
    } else {
      const config = JSON.parse(text);
      const query = search.value.trim().toLowerCase();
      const card = el("section", { class: "card" }, el("h3", {}, "Gateway 全局"));
      for (const field of snapshot.fields.filter((item) => `${item.label} ${item.path} ${item.description}`.toLowerCase().includes(query))) {
        const value = valueAt(config, field.path) ?? field.default;
        const input = field.type === "boolean" ? el("input", { type: "checkbox", class: "setting-switch", role: "switch", checked: value })
          : el("select", {}, field.enum.map((choice) => el("option", { value: choice, selected: choice === value }, choice)));
        input.setAttribute("aria-label", field.label);
        const row = el("div", { class: "setting-row config-setting-row" }, el("div", {}, el("b", {}, field.label), el("div", {}, el("code", {}, field.path)), el("p", { class: "muted" }, field.description)), input);
        row.classList.toggle("is-dirty", valueAt(config, field.path) !== valueAt(snapshot.config, field.path));
        input.addEventListener("change", () => {
          setValue(config, field.path, field.type === "boolean" ? input.checked : input.value);
          text = JSON.stringify(config, null, 2);
          row.classList.toggle("is-dirty", valueAt(config, field.path) !== valueAt(snapshot.config, field.path)); update();
        });
        card.append(row);
      }
      host.append(card, el("section", { class: "card" }, el("h3", {}, "全局默认模型"),
        el("code", {}, JSON.stringify(snapshot.config.agents?.defaults?.model ?? "未配置")),
        el("p", { class: "muted" }, "各 Agent 模型可在系统 Agent 或班级 Agent 分组中设置。")));
    }
    update();
  }
  function switchMode(next) {
    if (!snapshot || mode === next) return;
    try { if (next === "form") gatewayChanges(snapshot, text); mode = next; clear(messages); draw(); }
    catch (error) { clear(messages); messages.append(errorPanel(error)); }
  }
  async function load({ restarting = false, attempt = 0 } = {}) {
    if (!restarting) { clear(host); host.append(skeleton(4)); }
    try {
      snapshot = await view.api("/admin/openclaw/config/raw");
      savedText = text = JSON.stringify(snapshot.config, null, 2); clear(messages); waiting = false; draw(); setWaiting(false);
    } catch (error) {
      if (!view.active) return;
      if (restarting && attempt < 5) { view.delay(() => load({ restarting: true, attempt: attempt + 1 }), 1600); return; }
      clear(messages); messages.append(errorPanel(error, { onRetry: load }));
      if (!snapshot) clear(host);
    }
  }
  async function preview() {
    clear(messages);
    try {
      const patch = gatewayChanges(snapshot, text);
      const baseHash = snapshot.hash;
      const draft = text;
      const rows = Object.entries(patch).map(([path, value]) => ({ path, old: valueAt(snapshot.config, path), new: value }));
      if (!rows.length) return;
      diffPreviewModal(rows, { view, title: "预览 Gateway 配置差异", onSave: async () => {
        const result = await view.api("/admin/openclaw/config/raw", { method: "PATCH", body: { patch, base_hash: baseHash } });
        savedText = draft; text = draft; update();
        toast("配置已提交，Gateway 正在重启", "success");
        messages.append(el("div", { class: "settings-restart-banner", role: "status" }, "Gateway 正在重启，稍后自动刷新连接与配置。"));
        setWaiting(true);
        view.delay(() => load({ restarting: true }), result.restart_after_ms || 1600);
      }, onError: (error) => { if (error.code === "CONFIG_CONFLICT") void load(); } });
    } catch (error) { messages.append(errorPanel(error)); }
  }
  return { el: root, load, isDirty: () => text !== savedText };
}
