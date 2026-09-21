// 通用工具：DOM 构建、文本安全、按后端启动时区显示日期、toast、防抖。

let displayTimeZone = "Asia/Shanghai";

export function configureTime(config) {
  if (typeof config?.timezone === "string" && config.timezone) displayTimeZone = config.timezone;
}

// el("div", {class: "x", onclick: fn, dataset: {...}, aria: {...}}, ...children)
// children 可以是 Node、字符串（作为 textContent，安全）、数组或 null。
export function el(tag, attrs = {}, ...children) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(attrs || {})) {
    if (value === null || value === undefined || value === false) continue;
    if (key === "class") node.className = value;
    else if (key === "dataset") Object.assign(node.dataset, value);
    else if (key === "aria") for (const [name, v] of Object.entries(value)) node.setAttribute(`aria-${name}`, v);
    else if (key === "style" && typeof value === "object") Object.assign(node.style, value);
    else if (key.startsWith("on") && typeof value === "function") node.addEventListener(key.slice(2).toLowerCase(), value);
    else if (key === "value") node.value = value;
    else if (key === "checked") node.checked = !!value;
    else if (key === "disabled") node.disabled = !!value;
    else if (value === true) node.setAttribute(key, "");
    else node.setAttribute(key, value);
  }
  appendChildren(node, children);
  return node;
}

function appendChildren(node, children) {
  for (const child of children.flat(20)) {
    if (child === null || child === undefined || child === false) continue;
    node.append(child instanceof Node ? child : document.createTextNode(String(child)));
  }
}

export function clear(node) { node.replaceChildren(); return node; }

const pad = (n) => String(n).padStart(2, "0");

// 以后端配置时区显示日期时间。后端时间戳为 ISO 8601（带时区）。
export function fmtDate(value) {
  if (!value) return "—";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return String(value);
  return date.toLocaleDateString("zh-CN", { timeZone: displayTimeZone, year: "numeric", month: "2-digit", day: "2-digit" });
}

export function fmtDateTime(value) {
  if (!value) return "—";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return String(value);
  return date.toLocaleString("zh-CN", { timeZone: displayTimeZone, year: "numeric", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", hour12: false });
}

// 本地（用户浏览器在中国使用场景下即 Asia/Shanghai）的 YYYY-MM-DD，请求参数用。
export function dateStr(date = new Date()) {
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}`;
}

// 任意时间值按后端配置时区归一为 YYYY-MM-DD（en-CA 产出 ISO 格式），用于与后端日期比较。
export function dateStrSH(value) {
  const date = value instanceof Date ? value : new Date(value);
  if (Number.isNaN(date.getTime())) return "";
  return date.toLocaleDateString("en-CA", { timeZone: displayTimeZone });
}

export function todayStr() { return dateStrSH(new Date()); }

export function addDays(date, days) {
  const next = new Date(date.getTime());
  next.setDate(next.getDate() + days);
  return next;
}

export function addDaysStr(base, days) { return dateStr(addDays(new Date(`${base}T00:00:00`), days)); }

export function weekdayOf(dateString) { return new Date(`${dateString}T00:00:00`).getDay() || 7; }

export const WEEKDAY_NAMES = { 1: "周一", 2: "周二", 3: "周三", 4: "周四", 5: "周五", 6: "周六", 7: "周日" };

export function periodLabel(periodOrNo, customName = null) {
  const periodNo = typeof periodOrNo === "object" ? Number(periodOrNo?.period_no) : Number(periodOrNo);
  const name = typeof periodOrNo === "object" ? periodOrNo?.name : customName;
  if (name?.trim()) return name.trim();
  const digits = "零一二三四五六七八九";
  let number;
  if (periodNo >= 1 && periodNo < 10) number = digits[periodNo];
  else if (periodNo < 20) number = `十${periodNo % 10 ? digits[periodNo % 10] : ""}`;
  else if (periodNo < 100) number = `${digits[Math.floor(periodNo / 10)]}十${periodNo % 10 ? digits[periodNo % 10] : ""}`;
  else number = String(periodNo);
  return `第${number}节`;
}

export function fileSize(bytes) {
  const value = Number(bytes) || 0;
  if (value < 1024) return `${value} B`;
  if (value < 1024 * 1024) return `${(value / 1024).toFixed(1)} KB`;
  return `${(value / 1024 / 1024).toFixed(1)} MB`;
}

export function debounce(fn, delay = 400) {
  let timer = null;
  const wrapped = (...args) => { clearTimeout(timer); timer = setTimeout(() => fn(...args), delay); };
  wrapped.cancel = () => clearTimeout(timer);
  return wrapped;
}

let toastBox = null;
export function toast(message, kind = "info") {
  if (!toastBox) {
    toastBox = el("div", { class: "toast-stack", role: "status", "aria-live": "polite" });
    document.body.append(toastBox);
  }
  const item = el("div", { class: `toast toast-${kind}` }, message);
  toastBox.append(item);
  setTimeout(() => item.classList.add("show"), 16);
  setTimeout(() => { item.classList.remove("show"); setTimeout(() => item.remove(), 250); }, kind === "error" ? 6000 : 3600);
}

export function pct(value) {
  if (value === null || value === undefined) return "—";
  return `${Math.round(value * 1000) / 10}%`;
}

export function copyText(text) {
  if (navigator.clipboard?.writeText) return navigator.clipboard.writeText(text).then(() => toast("已复制"));
  return Promise.reject(new Error("当前环境不支持剪贴板"));
}

/* 将原生单选 select 渐进增强为可搜索下拉框；业务代码仍读写原 select.value。 */
const SELECT_FREQ_KEY = "classclaw-select-frequency-v1";

function selectFrequency() {
  try { return JSON.parse(localStorage.getItem(SELECT_FREQ_KEY) || "{}"); }
  catch { return {}; }
}

function enhanceSelect(select) {
  if (!(select instanceof HTMLSelectElement) || select.multiple || select.dataset.smartReady) return;
  select.dataset.smartReady = "true";
  const wrapper = el("div", { class: "smart-select" });
  select.parentNode.insertBefore(wrapper, select);
  wrapper.append(select);
  select.classList.add("smart-select-native");
  select.tabIndex = -1;
  select.setAttribute("aria-hidden", "true");

  const input = el("input", {
    type: "text", class: "smart-select-input", autocomplete: "off",
    role: "combobox", "aria-autocomplete": "list", "aria-expanded": "false",
  });
  const menu = el("div", { class: "smart-select-menu hidden", role: "listbox" });

  /* 菜单用 fixed 定位：普通 absolute 会被 .table-wrap、.modal-body 等滚动容器裁剪，导致下拉列表显示不全。 */
  function placeMenu() {
    const rect = input.getBoundingClientRect();
    if (!rect.width) return;
    const width = Math.max(rect.width, 180);
    const left = Math.min(Math.max(8, rect.left), Math.max(8, window.innerWidth - width - 8));
    const spaceBelow = window.innerHeight - rect.bottom - 8;
    const spaceAbove = rect.top - 16;
    const openUp = spaceBelow < 140 && spaceAbove > spaceBelow;
    menu.style.position = "fixed";
    menu.style.width = `${width}px`;
    menu.style.left = `${left}px`;
    menu.style.right = "auto";
    menu.style.top = openUp ? "auto" : `${rect.bottom + 5}px`;
    menu.style.bottom = openUp ? `${window.innerHeight - rect.top + 5}px` : "auto";
    menu.style.maxHeight = `${Math.max(120, Math.min(260, openUp ? spaceAbove : spaceBelow))}px`;
  }
  const reposition = () => { if (!menu.classList.contains("hidden")) placeMenu(); };
  const arrow = el("span", { class: "smart-select-arrow", aria: { hidden: "true" } }, "⌄");
  wrapper.append(input, arrow, menu);

  const fieldLabel = select.closest(".field")?.querySelector(":scope > span")?.textContent || "";
  const optionSignature = [...select.options].map((option) => option.value).join("|").slice(0, 120);
  const scope = select.dataset.frequencyKey || select.name || select.id || `${location.hash.split("?")[0]}:${fieldLabel}:${optionSignature}`;
  let activeIndex = -1;

  function options(query = "") {
    const needle = query.trim().toLocaleLowerCase("zh-CN");
    const frequency = selectFrequency();
    return [...select.options]
      .map((option, index) => ({ option, index, count: frequency[`${scope}:${option.value}`] || 0 }))
      .filter(({ option }) => !option.disabled && (!needle || option.textContent.toLocaleLowerCase("zh-CN").includes(needle) || option.value.toLocaleLowerCase("zh-CN").includes(needle)))
      .sort((a, b) => {
        if (!a.option.value && b.option.value) return -1;
        if (a.option.value && !b.option.value) return 1;
        return (select.dataset.preserveOrder === "true" ? 0 : b.count - a.count) || a.index - b.index;
      });
  }

  function selectedText() {
    return select.selectedOptions[0]?.textContent || "";
  }

  function closeMenu() {
    menu.classList.add("hidden");
    window.removeEventListener("scroll", reposition, { capture: true });
    window.removeEventListener("resize", reposition);
    input.setAttribute("aria-expanded", "false");
    activeIndex = -1;
    input.value = selectedText();
  }

  function choose(option) {
    select.value = option.value;
    const frequency = selectFrequency();
    frequency[`${scope}:${option.value}`] = (frequency[`${scope}:${option.value}`] || 0) + 1;
    try { localStorage.setItem(SELECT_FREQ_KEY, JSON.stringify(frequency)); } catch { /* 无存储权限时仍可正常选择 */ }
    select.dispatchEvent(new Event("change", { bubbles: true }));
    closeMenu();
  }

  function draw(query = "") {
    clear(menu);
    const rows = options(query);
    activeIndex = rows.length ? 0 : -1;
    if (!rows.length) {
      menu.append(el("div", { class: "smart-select-empty" }, "没有匹配项"));
    } else {
      rows.forEach(({ option }, index) => {
        const button = el("button", {
          class: `smart-select-option${option.value === select.value ? " selected" : ""}${index === activeIndex ? " active" : ""}`,
          type: "button", role: "option", "aria-selected": option.value === select.value ? "true" : "false",
          onmousedown: (event) => event.preventDefault(), onclick: () => choose(option),
        }, option.textContent);
        menu.append(button);
      });
    }
    menu.classList.remove("hidden");
    placeMenu();
    window.addEventListener("scroll", reposition, { capture: true, passive: true });
    window.addEventListener("resize", reposition);
    input.setAttribute("aria-expanded", "true");
  }

  function moveActive(direction) {
    const buttons = [...menu.querySelectorAll(".smart-select-option")];
    if (!buttons.length) return;
    activeIndex = Math.max(0, Math.min(buttons.length - 1, activeIndex + direction));
    buttons.forEach((button, index) => button.classList.toggle("active", index === activeIndex));
    buttons[activeIndex].scrollIntoView({ block: "nearest" });
  }

  const descriptor = Object.getOwnPropertyDescriptor(HTMLSelectElement.prototype, "value");
  if (descriptor?.get && descriptor?.set) {
    Object.defineProperty(select, "value", {
      configurable: true,
      get: () => descriptor.get.call(select),
      set: (value) => { descriptor.set.call(select, value); input.value = selectedText(); },
    });
  }
  input.value = selectedText();
  input.disabled = select.disabled;
  input.addEventListener("focus", () => { input.select(); draw(""); });
  input.addEventListener("click", () => draw(input.value === selectedText() ? "" : input.value));
  input.addEventListener("input", () => draw(input.value));
  input.addEventListener("keydown", (event) => {
    if (event.key === "ArrowDown") { event.preventDefault(); if (menu.classList.contains("hidden")) draw(""); else moveActive(1); }
    if (event.key === "ArrowUp") { event.preventDefault(); moveActive(-1); }
    if (event.key === "Enter" && !menu.classList.contains("hidden")) {
      event.preventDefault(); menu.querySelectorAll(".smart-select-option")[activeIndex]?.click();
    }
    if (event.key === "Escape") closeMenu();
  });
  input.addEventListener("blur", () => setTimeout(closeMenu, 120));
  select.addEventListener("change", () => { input.value = selectedText(); });
  new MutationObserver(() => { input.value = selectedText(); input.disabled = select.disabled; }).observe(select, { childList: true, subtree: true, attributes: true });
}

export function installEnhancedControls(root = document) {
  const scan = (node) => {
    if (node instanceof HTMLSelectElement) enhanceSelect(node);
    if (node.querySelectorAll) node.querySelectorAll("select:not([multiple])").forEach(enhanceSelect);
  };
  scan(root);
  const observer = new MutationObserver((changes) => changes.forEach((change) => change.addedNodes.forEach(scan)));
  observer.observe(root === document ? document.documentElement : root, { childList: true, subtree: true });
  return observer;
}
