import { el, clear } from "./util.js";

export function sourceEditor(text = "", { onChange = () => {}, errors = [], label = "TOML 配置源码" } = {}) {
  const numbers = el("pre", { class: "source-lines", "aria-hidden": "true" });
  const input = el("textarea", { class: "source-input", value: text, spellcheck: "false", wrap: "off", "aria-label": label });
  const root = el("div", { class: "source-editor" }, numbers, input);
  let currentErrors = errors;
  function renderLines() {
    clear(numbers);
    input.value.split("\n").forEach((_, index) => {
      const error = currentErrors.find((item) => item.line === index + 1);
      numbers.append(el("span", { class: error ? "source-line-error" : "", title: error?.message || "" }, `${index + 1}\n`));
    });
    numbers.scrollTop = input.scrollTop;
  }
  input.addEventListener("input", () => { currentErrors = []; renderLines(); onChange(input.value); });
  input.addEventListener("scroll", () => { numbers.scrollTop = input.scrollTop; });
  renderLines();
  return { el: root, input, setText(value) { input.value = value; renderLines(); },
    setErrors(value) { currentErrors = value; renderLines(); } };
}

// Layout offsets are Unicode code points, supplied by Python. No TOML parser in the browser.
export function applyTomlChanges(text, layout, changes) {
  const groups = new Map();
  const insertions = new Map();
  const encode = (value) => Array.isArray(value) ? `[${value.map(encode).join(", ")}]`
    : value && typeof value === "object"
      ? `{ ${Object.entries(value).map(([key, item]) => `${JSON.stringify(key)} = ${encode(item)}`).join(", ")} }`
      : JSON.stringify(value);
  const assignment = (path, value, scope = "") => {
    const relative = scope ? path.slice(scope.length + 1) : path;
    return `${relative.split(".").map((part) => /^[A-Za-z0-9_-]+$/.test(part) ? part : JSON.stringify(part)).join(".")} = ${encode(value)}\n`;
  };
  for (const [path, value] of Object.entries(changes)) {
    const span = layout.spans.find((item) => item.root === path || path.startsWith(`${item.root}.`));
    if (span) {
      if (!groups.has(span)) groups.set(span, { ...span.values });
      groups.get(span)[path] = value;
    } else {
      const scope = Object.keys(layout.tables).filter((key) => !key || path.startsWith(`${key}.`)).sort((a, b) => b.length - a.length)[0] || "";
      const position = layout.tables[scope] || 0;
      insertions.set(position, (insertions.get(position) || "") + assignment(path, value, scope));
    }
  }
  const replacements = [...groups].map(([span, values]) => ({ start: span.start, end: span.end,
    text: Object.entries(values).map(([path, value]) => assignment(path, value, span.scope)).join("") }));
  for (const [position, content] of insertions) replacements.push({ start: position, end: position, text: content });
  let chars = Array.from(text);
  for (const edit of replacements.sort((a, b) => b.start - a.start || b.end - a.end)) {
    const prefix = edit.start > 0 && chars[edit.start - 1] !== "\n" ? "\n" : "";
    chars = [...chars.slice(0, edit.start), ...Array.from(prefix + edit.text), ...chars.slice(edit.end)];
  }
  return chars.join("");
}
