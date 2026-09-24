import { el, clear, toast, copyText } from "./util.js";
import { dataTable, openModal, errorPanel, emptyState, statusBadge } from "./components.js";
import { sourceEditor, applyTomlChanges } from "./sourceEditor.js";

const secretPath = (path) => /token|password|credential|secret|api.?key/i.test(path);
const displayValue = (value) => value === undefined || value === null ? "—" : typeof value === "object" ? JSON.stringify(value) : String(value);
export const sourceLabel = (source) => source?.startsWith("environment:") ? "ENV" : source === "file" ? "TOML" : "默认";

export function diffPreviewModal(rows, { view, onSave, title = "预览配置差异", onError = () => {} }) {
  const errors = el("div");
  const dialog = openModal({ title, wide: true, body: el("div", {}, dataTable({ columns: [
    { key: "path", label: "配置路径", render: (row) => el("code", {}, row.path) },
    { label: "当前生效值", render: (row) => el("span", { class: "diff-old" }, secretPath(row.path) ? "***" : displayValue(row.old)) },
    { label: "新值", render: (row) => el("span", { class: "diff-new" }, secretPath(row.path) ? "***" : displayValue(row.new)) },
    { label: "来源变化", render: (row) => `${sourceLabel(row.old_source)} → ${sourceLabel(row.new_source)}` },
  ], rows, empty: "配置值未变化，仅更新源码格式或注释。" }), errors),
  actions: [{ label: "取消" }, { label: "确认保存", kind: "primary", onClick: async ({ close, setSubmitting }) => {
    setSubmitting(true); clear(errors);
    try { await onSave(); if (view.active) close(); }
    catch (error) {
      if (!view.active) return;
      errors.append(errorPanel(error));
      for (const issue of error.details?.errors || []) errors.append(el("p", { class: "field-error" }, `${issue.config_path || (issue.line ? `第 ${issue.line} 行` : "")} ${issue.message || issue.msg}`));
      onError(error); setSubmitting(false);
    }
  } }],
  });
  return view.overlay(dialog);
}

export function needsRestart(snapshot) {
  return !!snapshot.active_config_hash && snapshot.active_config_hash !== snapshot.config_hash;
}

export function settingsEditor({ snapshot: initial, view, sectionIds = null, initialSection = "", hideTree = false, onSaved = () => {} }) {
  let snapshot = initial;
  let text = snapshot.toml_text || "";
  let formText = text;
  let layout = snapshot.layout;
  let formValues = { ...snapshot.values };
  let values = { ...formValues };
  let edits = {};
  let checkedDiff = [];
  let mode = "form";
  let section = initialSection;
  let query = "";
  let issues = [];
  let dismissedHash = null;
  const root = el("div", { class: "settings-editor" });
  const banner = el("div");
  const metadata = el("details", { class: "settings-document-meta" });
  const tree = el("nav", { class: "settings-tree", "aria-label": "配置分组" });
  if (hideTree) tree.classList.add("hidden");
  const content = el("div", { class: "settings-content" });
  const messages = el("div", { "aria-live": "polite" });
  const count = el("b", { class: "settings-dirty-count", role: "status" });
  const save = el("button", { class: "primary", type: "button", onclick: preview }, "预览差异并保存");
  const discard = el("button", { class: "secondary", type: "button", onclick: reset }, "放弃全部");
  const search = el("input", { type: "search", "aria-label": "搜索全部配置", placeholder: "搜索名称、说明、配置路径或环境变量" });
  const formButton = el("button", { type: "button", onclick: () => changeMode("form") }, "表单");
  const sourceButton = el("button", { type: "button", onclick: () => changeMode("source") }, "源码");
  const rollback = el("button", { class: "text-button", type: "button", onclick: rollbackDocument }, "恢复上一版");
  const source = sourceEditor(text, { onChange: (value) => { text = value; issues = []; updateDirty(); } });
  root.append(banner, metadata, el("div", { class: "settings-toolbar" }, search,
    el("div", { class: "tabs", "aria-label": "配置视图" }, formButton, sourceButton), rollback),
  messages, el("div", { class: `settings-layout${hideTree ? " single" : ""}` }, tree, content),
  el("div", { class: "settings-savebar" }, count, el("span", { class: "spacer" }), discard, save, statusBadge("pending", "需重启生效")));
  search.addEventListener("input", () => { query = search.value.trim().toLowerCase(); renderTree(); renderContent(); });

  function items() { return snapshot.catalog.items.filter((item) => !sectionIds || sectionIds.includes(item.section)); }
  function dirtyPaths() {
    return new Set([...checkedDiff.map((row) => row.path), ...Object.keys(edits)]
      .filter((path) => values[path] !== snapshot.values[path]
        || checkedDiff.some((row) => row.path === path && row.old_source !== row.new_source)));
  }
  function isDirty() { return text !== (snapshot.toml_text || ""); }
  function updateDirty() {
    const paths = dirtyPaths();
    const dirty = isDirty();
    count.textContent = dirty ? (mode === "source" ? "源码有未保存修改" : `已修改 ${paths.size || 1} 项`) : "所有修改已保存";
    save.disabled = !dirty || issues.length > 0; discard.disabled = !dirty;
    for (const row of content.querySelectorAll(".config-setting-row")) row.classList.toggle("is-dirty", paths.has(row.dataset.path));
    renderTree();
  }
  function renderBanner() {
    clear(banner);
    clear(metadata);
    metadata.append(el("summary", {}, "配置文件与版本"), el("p", {}, el("code", {}, snapshot.config_file || snapshot.catalog.config_file || "config/classclaw.toml")),
      el("p", { class: "muted" }, snapshot.catalog.precedence || "环境变量 > TOML > 程序默认值"),
      el("p", {}, "文件配置版本：", el("code", {}, snapshot.config_hash)),
      el("p", {}, "当前进程版本：", el("code", {}, snapshot.active_config_hash || "—")));
    if (!needsRestart(snapshot) || dismissedHash === snapshot.config_hash) return;
    banner.append(el("div", { class: "settings-restart-banner", role: "status" },
      el("b", {}, "配置已修改，重启后生效"), el("code", {}, "sudo systemctl restart classclaw"),
      el("button", { class: "text-button", type: "button", onclick: () => copyText("sudo systemctl restart classclaw").catch(() => toast("复制失败，请手动复制上方命令", "error")) }, "复制命令"),
      el("button", { class: "text-button", type: "button", onclick: () => { dismissedHash = snapshot.config_hash; renderBanner(); } }, "知道了")));
  }
  function matches(item) { return `${item.label} ${item.description} ${item.config_path} ${item.env_var}`.toLowerCase().includes(query); }
  function renderTree() {
    clear(tree);
    const paths = dirtyPaths();
    const groups = snapshot.catalog.sections.filter((group) => !sectionIds || sectionIds.includes(group.id));
    tree.append(el("button", { class: !section ? "active" : "", type: "button", onclick: () => { section = ""; renderTree(); renderContent(); } }, "全部设置"));
    for (const group of groups) {
      if (query && !items().some((item) => item.section === group.id && matches(item))) continue;
      const modified = snapshot.catalog.items.filter((item) => item.section === group.id && paths.has(item.config_path)).length;
      tree.append(el("button", { class: section === group.id ? "active" : "", type: "button", onclick: () => { section = group.id; renderTree(); renderContent(); } }, group.label,
        modified ? el("span", { class: "tag tag-warn", "aria-label": `${modified} 项未保存` }, modified) : null));
    }
  }
  function localError(item, value) {
    if (item.type === "integer" && !Number.isInteger(value)) return "请输入整数";
    if (["integer", "number"].includes(item.type) && !Number.isFinite(value)) return "请输入有效数字";
    if (item.minimum !== undefined && value < item.minimum) return `不能小于 ${item.minimum}`;
    if (item.maximum !== undefined && value > item.maximum) return `不能大于 ${item.maximum}`;
    if (item.exclusiveMinimum !== undefined && value <= item.exclusiveMinimum) return `必须大于 ${item.exclusiveMinimum}`;
    if (typeof value === "string" && item.minLength && value.trim().length < item.minLength) return `至少 ${item.minLength} 个字符`;
    if (typeof value === "string" && !value.trim() && (item.config_path.startsWith("storage.") || item.config_path === "runtime.log_file")) return "路径不能为空";
    return "";
  }
  function settingRow(item) {
    const path = item.config_path;
    const locked = snapshot.env_locked_paths.includes(path);
    const env = item.value_source?.startsWith("environment:") ? item.value_source.slice(12) : item.env_var;
    const tooltip = locked ? `被环境变量 ${env} 覆盖，需在服务器移除该变量后界面修改才会生效` : "";
    const displayed = locked ? item.value : values[path];
    const control = item.type === "boolean" ? el("input", { type: "checkbox", class: "setting-switch", role: "switch", checked: displayed })
      : item.enum ? el("select", {}, item.enum.map((value) => el("option", { value, selected: value === displayed }, value)))
        : el("input", { type: ["integer", "number"].includes(item.type) ? "number" : "text",
          value: item.type === "array" ? JSON.stringify(displayed) : displayed,
          min: item.minimum ?? item.exclusiveMinimum, max: item.maximum, maxlength: item.maxLength, step: item.type === "integer" ? "1" : "any" });
    control.disabled = locked; control.title = tooltip; control.setAttribute("aria-label", item.label);
    control.dataset.path = path;
    const error = el("small", { class: "field-error", role: "alert" });
    const showError = () => {
      error.textContent = issues.find((issue) => issue.config_path === path)?.message || "";
      control.setAttribute("aria-invalid", error.textContent ? "true" : "false");
    };
    control.addEventListener(item.enum || item.type === "boolean" ? "change" : "input", () => {
      if (locked) return;
      let value = item.type === "boolean" ? control.checked : ["integer", "number"].includes(item.type) ? (control.value === "" ? NaN : Number(control.value)) : control.value;
      if (item.type === "array") {
        try {
          value = JSON.parse(control.value);
          if (!Array.isArray(value)) throw new Error("需要数组");
        } catch {
          issues = issues.filter((issue) => issue.config_path !== path);
          issues.push({ config_path: path, message: "请输入有效 JSON 数组" });
          showError();
          updateDirty();
          return;
        }
      }
      values[path] = value;
      if (value === formValues[path]) delete edits[path]; else edits[path] = value;
      issues = issues.filter((issue) => issue.config_path !== path);
      const message = localError(item, value);
      if (message) issues.push({ config_path: path, message });
      text = applyTomlChanges(formText, layout, edits);
      source.setText(text); showError(); updateDirty();
    });
    showError();
    return el("div", { class: "setting-row config-setting-row", dataset: { path } },
      el("div", {}, el("b", {}, item.label), el("div", {}, el("code", {}, path)), el("p", { class: "muted" }, item.description),
        el("small", { class: "config-field-meta", title: tooltip }, el("code", {}, item.env_var), `当前来源：${sourceLabel(item.value_source)}`)),
      el("div", { class: "config-setting-control" }, control, error,
        el("div", { class: "row-gap" }, statusBadge(locked ? "pending" : "inactive", sourceLabel(item.value_source)), statusBadge("inactive", "需重启")),
        path.startsWith("storage.") && item.type === "string" || path === "runtime.log_file"
          ? el("small", { class: "muted" }, "相对路径以配置文件目录为基准") : null));
  }
  function renderContent() {
    clear(content);
    formButton.classList.toggle("active", mode === "form"); sourceButton.classList.toggle("active", mode === "source");
    formButton.setAttribute("aria-pressed", String(mode === "form")); sourceButton.setAttribute("aria-pressed", String(mode === "source"));
    if (mode === "source") { source.setText(text); content.append(source.el); source.setErrors(issues); return; }
    for (const group of snapshot.catalog.sections) {
      const rows = items().filter((item) => item.section === group.id && matches(item) && (query || !section || section === group.id));
      if (rows.length) content.append(el("section", { class: "card settings-group" }, el("h3", {}, group.label), el("p", { class: "muted" }, group.description), rows.map(settingRow)));
    }
    const credentials = snapshot.catalog.credentials.filter((item) => matches(item) && (!sectionIds || item.category === "openclaw"));
    if ((!section || query) && credentials.length) content.append(el("section", { class: "card" }, el("h3", {}, "凭据与安全"), credentials.map((item) =>
      el("div", { class: "setting-row" }, el("div", {}, el("b", {}, item.label), el("p", { class: "muted" }, item.description), el("code", {}, item.env_var)),
        statusBadge(item.configured ? "active" : "pending", item.configured ? "已设置" : "未设置 / 内置默认")))));
    if (!content.children.length) content.append(emptyState("没有匹配的配置", "调整搜索内容或分组。"));
    updateDirty();
  }
  function showIssues(error) {
    issues = (error.details?.errors || []).map((issue) => ({ ...issue, message: issue.message || issue.msg }));
    clear(messages); messages.append(errorPanel(error));
    if (mode === "source") {
      source.setErrors(issues);
      for (const issue of issues) messages.append(el("p", { class: "field-error" },
        `${issue.line ? `第 ${issue.line} 行 · ` : ""}${issue.config_path || ""} ${issue.message || "配置无效"}`));
    } else renderContent();
  }
  async function check(candidate = text) {
    const result = await view.api("/admin/settings/check", { method: "POST", body: { toml_text: candidate } });
    if (text === candidate) { issues = []; clear(messages); checkedDiff = result.diff; }
    return result;
  }
  async function changeMode(next) {
    if (next === mode) return;
    if (next === "source" && issues.length) { toast("请先修正行内错误", "error"); return; }
    if (next === "form") {
      try {
        const candidate = text;
        const checked = await check(candidate);
        if (text !== candidate) return;
        formText = text; layout = checked.layout; formValues = checked.values; values = { ...formValues }; edits = {};
      } catch (error) { if (view.active) showIssues(error); return; }
    }
    mode = next; renderContent(); updateDirty();
  }
  function reset() {
    text = snapshot.toml_text || ""; formText = text; layout = snapshot.layout;
    values = { ...snapshot.values }; formValues = { ...values }; edits = {}; checkedDiff = []; issues = [];
    clear(messages); source.setErrors([]); renderContent(); updateDirty();
  }
  async function refresh() {
    snapshot = await view.api("/admin/settings/document"); reset(); renderBanner();
  }
  async function preview() {
    save.disabled = true;
    try {
      const draft = text;
      const checked = await check(draft);
      if (text !== draft) { toast("内容已更新，请重新预览差异"); return; }
      const baseHash = snapshot.config_hash;
      const baseDocumentHash = snapshot.document_hash;
      diffPreviewModal(checked.diff, { view, onSave: async () => {
        const result = await view.api("/admin/settings/document", { method: "PATCH", body: {
          toml_text: draft, base_hash: baseHash, base_document_hash: baseDocumentHash,
        } });
        // Mark clean immediately, even if the following read fails after a successful write.
        snapshot = { ...snapshot, toml_text: draft, config_hash: result.new_config_hash, document_hash: result.document_hash,
          values: checked.values, layout: checked.layout };
        reset(); renderBanner(); toast("配置已写入，需重启 ClassClaw 生效", "success");
        try { await refresh(); }
        catch (error) { if (view.active) messages.append(errorPanel(error, { onRetry: refresh })); }
        onSaved(result);
      }, onError: (error) => {
        showIssues(error);
        if (error.code === "CONFIG_CONFLICT") refresh().catch(() => {});
      } });
    } catch (error) { if (view.active) showIssues(error); }
    finally { if (view.active) updateDirty(); }
  }
  async function rollbackDocument() {
    if (isDirty()) { toast("请先保存或放弃当前修改", "error"); return; }
    view.overlay(openModal({ title: "恢复上一版配置", body: el("p", {}, "恢复最近一次保存前的配置文件，当前文件会另行备份；完成后需重启 ClassClaw。"),
      actions: [{ label: "取消" }, { label: "确认恢复", kind: "primary", onClick: async ({ close, setSubmitting }) => {
        setSubmitting(true);
        try {
          await view.api("/admin/settings/rollback", { method: "POST", body: { base_hash: snapshot.config_hash, base_document_hash: snapshot.document_hash } });
          await refresh(); close(); toast("已恢复上一版配置，需重启生效", "success");
        } catch (error) { if (view.active) { showIssues(error); setSubmitting(false); } }
      } }],
    }));
  }
  renderBanner(); renderTree(); renderContent(); updateDirty();
  return { el: root, isDirty, refresh, reset };
}
