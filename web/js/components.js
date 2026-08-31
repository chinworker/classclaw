// 可复用组件：Modal、Drawer、DataTable、状态徽章、错误面板、指标卡、上传区、二维码面板等。
// 组件只接受结构化数据，动态文本一律经 textContent 安全插入。

import { el, clear, escapeHtml, fmtDateTime, toast, copyText } from "./util.js";
import { api, ApiError } from "./api.js";

/* ---------------- Modal ---------------- */

const FOCUSABLE = 'a[href], button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])';

export function openModal({ title, body, actions = [], wide = false, onClose = null, dismissible = true }) {
  const overlay = el("div", { class: "modal-overlay" });
  const modal = el("div", { class: `modal${wide ? " modal-wide" : ""}`, role: "dialog", "aria-modal": "true", "aria-label": title });
  const closeBtn = el("button", { class: "modal-close", type: "button", "aria-label": "关闭" }, "关闭");
  const head = el("div", { class: "modal-head" }, el("h3", {}, title), closeBtn);
  const bodyBox = el("div", { class: "modal-body" });
  if (body) bodyBox.append(body);
  const foot = el("div", { class: "modal-foot" });
  modal.append(head, bodyBox, foot);
  overlay.append(modal);

  let submitting = false;
  let closed = false;
  const previousFocus = document.activeElement;

  function close() {
    if (closed || submitting) return;
    closed = true;
    overlay.remove();
    document.removeEventListener("keydown", onKey, true);
    if (previousFocus && previousFocus.focus) previousFocus.focus();
    if (onClose) onClose();
  }

  function setSubmitting(value) {
    submitting = value;
    closeBtn.disabled = value;
    foot.querySelectorAll("button").forEach((b) => { b.disabled = value; });
  }

  function onKey(event) {
    if (event.key === "Escape" && dismissible && !submitting) { event.stopPropagation(); close(); }
    if (event.key === "Tab") {
      const nodes = [...modal.querySelectorAll(FOCUSABLE)];
      if (!nodes.length) return;
      const first = nodes[0]; const last = nodes[nodes.length - 1];
      if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus(); }
      else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus(); }
    }
  }

  closeBtn.addEventListener("click", close);
  overlay.addEventListener("mousedown", (event) => { if (event.target === overlay && dismissible && !submitting) close(); });
  document.addEventListener("keydown", onKey, true);

  for (const action of actions) {
    const btn = el("button", { class: action.kind || "secondary", type: "button" }, action.label);
    btn.addEventListener("click", async () => {
      if (action.onClick) {
        const result = await action.onClick({ close, setSubmitting, btn });
        if (result !== false && action.closeOnDone) close();
      } else close();
    });
    foot.append(btn);
  }

  document.body.append(overlay);
  const firstInput = modal.querySelector("input, select, textarea") || modal.querySelector(FOCUSABLE);
  if (firstInput) firstInput.focus();
  return { close, setSubmitting, body: bodyBox, modal };
}

// 危险操作二次确认：写明对象与影响，可要求输入特定文本。
export function confirmDanger({ title, lines = [], requireText = null, confirmLabel = "确认执行" }) {
  return new Promise((resolve) => {
    const body = el("div", {},
      el("div", { class: "danger-lines" }, lines.map((line) => el("p", {}, line))),
      requireText ? el("label", { class: "field" }, el("span", {}, `请输入「${requireText}」以确认`), el("input", { type: "text", autocomplete: "off", id: "danger-confirm-input" })) : null,
    );
    let modal;
    const run = () => {
      if (requireText) {
        const value = body.querySelector("#danger-confirm-input").value.trim();
        if (value !== requireText) { toast(`请输入完整文本：${requireText}`, "error"); return false; }
      }
      resolve(true);
      return undefined;
    };
    modal = openModal({
      title,
      body,
      onClose: () => resolve(false),
      actions: [
        { label: "取消", kind: "secondary" },
        { label: confirmLabel, kind: "danger", onClick: run, closeOnDone: true },
      ],
    });
  });
}

/* ---------------- Drawer ---------------- */

export function openDrawer({ title, body, wide = false }) {
  const overlay = el("div", { class: "drawer-overlay" });
  const drawer = el("div", { class: `drawer${wide ? " drawer-wide" : ""}`, role: "dialog", "aria-modal": "true", "aria-label": title });
  const closeBtn = el("button", { class: "modal-close", type: "button", "aria-label": "关闭" }, "关闭");
  drawer.append(el("div", { class: "modal-head" }, el("h3", {}, title), closeBtn), el("div", { class: "drawer-body" }, body));
  overlay.append(drawer);
  let closed = false;
  const previousFocus = document.activeElement;
  function close() {
    if (closed) return; closed = true;
    overlay.remove(); document.removeEventListener("keydown", onKey, true);
    if (previousFocus && previousFocus.focus) previousFocus.focus();
  }
  function onKey(event) { if (event.key === "Escape") { event.stopPropagation(); close(); } }
  closeBtn.addEventListener("click", close);
  overlay.addEventListener("mousedown", (event) => { if (event.target === overlay) close(); });
  document.addEventListener("keydown", onKey, true);
  document.body.append(overlay);
  closeBtn.focus();
  return { close, drawer };
}

/* ---------------- 基础展示组件 ---------------- */

export function pageHeader(title, desc, ...actions) {
  return el("div", { class: "page-header" },
    el("div", {}, el("h2", {}, title), desc ? el("p", { class: "page-desc" }, desc) : null),
    actions.length ? el("div", { class: "page-actions" }, actions) : null);
}

export function metricCard(label, value, sub = null, tone = "") {
  return el("div", { class: `metric-card${tone ? ` tone-${tone}` : ""}` },
    el("span", { class: "metric-value" }, value ?? "—"),
    el("span", { class: "metric-label" }, label),
    sub ? el("span", { class: "metric-sub" }, sub) : null);
}

export function emptyState(title, hint = null, actionEl = null) {
  return el("div", { class: "empty-state" },
    el("b", {}, title),
    hint ? el("p", {}, hint) : null,
    actionEl || null);
}

export function skeleton(rows = 4) {
  return el("div", { class: "skeleton-box", "aria-busy": "true" },
    Array.from({ length: rows }, () => el("div", { class: "skeleton-line" })));
}

export function errorPanel(error, { onRetry = null } = {}) {
  const isApi = error instanceof ApiError;
  const box = el("div", { class: "error-panel", role: "alert" },
    el("b", {}, isApi ? `${error.message}` : `出现错误：${error.message}`),
    el("div", { class: "error-meta" },
      isApi && error.requestId ? el("button", { class: "tag tag-id", type: "button", title: "点击复制问题编号", onclick: () => copyText(error.requestId) }, `问题编号：${error.requestId}`) : null),
    onRetry ? el("button", { class: "secondary", type: "button", onclick: onRetry }, "重试") : null);
  return box;
}

export function jsonDetails(value, summary = "查看 JSON") {
  const pre = el("pre", { class: "json-pre" });
  pre.textContent = JSON.stringify(value, null, 2);
  return el("details", { class: "json-details" }, el("summary", {}, summary), pre);
}

const STATUS_LABELS = {
  active: ["启用", "ok"], inactive: ["停用", "muted"], draft: ["草稿", "muted"], published: ["已发布", "ok"],
  closed: ["已关闭", "muted"], cancelled: ["已取消", "muted"], pending: ["待处理", "warn"], in_progress: ["进行中", "info"],
  completed: ["已完成", "ok"], overdue: ["已逾期", "error"], pending_review: ["待复核", "warn"],
  submitted: ["已交", "ok"], missing: ["未交", "error"], late: ["迟交", "warn"], exempt: ["免交", "muted"],
  revision_required: ["需订正", "warn"], revised: ["已订正", "ok"],
  present: ["出勤", "ok"], absent: ["缺勤", "error"], leave: ["请假", "warn"],
  pending_agent: ["待创建智能体", "warn"], agent_created: ["智能体已创建", "info"], awaiting_qr: ["待扫码", "warn"],
  linked: ["已绑定微信", "ok"], failed: ["失败", "error"],
  replaced: ["已替换", "muted"],
  positive: ["正向", "ok"], neutral: ["中性", "muted"], negative: ["负向", "error"],
  normal: ["普通", "muted"], attention: ["关注", "warn"], serious: ["严重", "error"],
  high: ["高", "error"], medium: ["中", "warn"], low: ["低", "muted"],
  awaiting_confirmation: ["等待确认", "warn"],
};

export function statusBadge(status, label = null) {
  const [text, tone] = STATUS_LABELS[status] || [label || status || "—", "muted"];
  return el("span", { class: `tag tag-${tone}` }, label || text);
}

/* ---------------- DataTable ---------------- */

// columns: [{ key, label, render?(row), width? }]
export function dataTable({ columns, rows, empty = "暂无数据", caption = null }) {
  const wrap = el("div", { class: "table-wrap", tabindex: "0" });
  const table = el("table", { class: "data-table" });
  if (caption) table.append(el("caption", { class: "sr-only" }, caption));
  table.append(el("thead", {}, el("tr", {}, columns.map((col) => el("th", { scope: "col", style: col.width ? { width: col.width } : null }, col.label)))));
  const tbody = el("tbody");
  for (const row of rows || []) {
    tbody.append(el("tr", {}, columns.map((col) => {
      const td = el("td");
      const content = col.render ? col.render(row) : row[col.key];
      if (content instanceof Node) td.append(content); else td.textContent = content ?? "—";
      return td;
    })));
  }
  table.append(tbody);
  wrap.append(table);
  if (!rows || !rows.length) wrap.append(emptyState(typeof empty === "string" ? empty : empty.title, empty.hint || null, empty.action || null));
  return wrap;
}

export function pagination({ total, page, pageSize, onPage }) {
  const pages = Math.max(1, Math.ceil(total / pageSize));
  if (pages <= 1) return el("div");
  const box = el("div", { class: "pagination" });
  const prev = el("button", { class: "secondary", type: "button", disabled: page <= 1, onclick: () => onPage(page - 1) }, "上一页");
  const next = el("button", { class: "secondary", type: "button", disabled: page >= pages, onclick: () => onPage(page + 1) }, "下一页");
  box.append(prev, el("span", { class: "page-info" }, `第 ${page} / ${pages} 页 · 共 ${total} 条`), next);
  return box;
}

/* ---------------- 表单辅助 ---------------- */

export function field(labelText, input, hint = null, errorId = null) {
  const label = el("label", { class: "field" }, el("span", {}, labelText), input);
  if (hint) label.append(el("small", { class: "field-hint", id: errorId || undefined }, hint));
  return label;
}

export function fieldError(message) {
  return el("small", { class: "field-error", role: "alert" }, message);
}

/* ---------------- FileDropzone ---------------- */

export function fileDropzone({ hint = "拖拽文件到这里，或点击选择", accept = null, multiple = false, onFiles, busyText = "正在上传…" }) {
  const input = el("input", { type: "file", hidden: true });
  if (accept) input.setAttribute("accept", accept);
  if (multiple) input.setAttribute("multiple", "");
  const zone = el("div", { class: "dropzone", tabindex: "0", role: "button", "aria-label": hint },
    el("b", {}, hint), el("span", { class: "muted" }, "文件会安全上传并进行识别"));
  const progress = el("div", { class: "drop-progress hidden" });
  zone.append(input, progress);
  const fire = (files) => { if (files?.length) onFiles([...files], { zone, setBusy }); };
  function setBusy(busy, text = busyText) {
    zone.classList.toggle("busy", busy);
    progress.classList.toggle("hidden", !busy);
    progress.textContent = busy ? text : "";
  }
  zone.addEventListener("click", () => input.click());
  zone.addEventListener("keydown", (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); input.click(); } });
  input.addEventListener("change", () => { fire(input.files); input.value = ""; });
  zone.addEventListener("dragover", (e) => { e.preventDefault(); zone.classList.add("dragging"); });
  zone.addEventListener("dragleave", () => zone.classList.remove("dragging"));
  zone.addEventListener("drop", (e) => { e.preventDefault(); zone.classList.remove("dragging"); fire(e.dataTransfer.files); });
  return zone;
}

/* ---------------- QrBindingPanel（微信绑定，onboarding 与智能体页复用） ---------------- */

export function qrBindingPanel(classId, { onDone = null, compact = false } = {}) {
  const box = el("div", { class: "qr-panel" });
  const statusLine = el("p", { class: "muted" }, "尚未开始微信绑定");
  const img = el("img", { class: "qr-img hidden", alt: "微信绑定二维码" });
  const errLine = el("p", { class: "field-error hidden", role: "alert" });
  const startBtn = el("button", { class: "primary", type: "button" }, "绑定微信 / 重新生成二维码");
  box.append(statusLine, img, errLine, el("div", { class: "row-gap" }, startBtn));
  let pollTimer = null; let inFlight = false; let deadline = 0; let currentQr = null;

  function stopPoll() { clearTimeout(pollTimer); pollTimer = null; inFlight = false; }
  function schedule(delay) { clearTimeout(pollTimer); pollTimer = setTimeout(poll, delay); }

  function showResult(result) {
    const binding = result.binding || {};
    if (result.connected && result.route_ready !== false) {
      stopPoll();
      statusLine.textContent = `绑定完成：智能体 ${binding.agent_name || ""} 已连接微信，消息路由已就绪。`;
      img.classList.add("hidden"); errLine.classList.add("hidden");
      toast("微信绑定完成，消息路由已就绪");
      if (onDone) onDone(binding);
      return true;
    }
    statusLine.textContent = `${result.message || "请使用微信扫码。"} 页面正在自动等待扫码结果。`;
    if (result.qr_data_url) {
      currentQr = result.qr_data_url;
      errLine.classList.add("hidden");
      img.onerror = () => { img.classList.add("hidden"); errLine.textContent = "二维码图片加载失败，请点击重新生成。"; errLine.classList.remove("hidden"); };
      img.src = result.qr_data_url;
      img.classList.remove("hidden");
    } else {
      img.classList.add("hidden");
      errLine.textContent = "暂时没有拿到二维码，请点击重新生成。";
      errLine.classList.remove("hidden");
    }
    return false;
  }

  async function start(force = true) {
    stopPoll();
    deadline = Date.now() + 5 * 60 * 1000;
    startBtn.disabled = true;
    statusLine.textContent = "正在启动微信登录会话…";
    try {
      const result = await api(`/classes/${classId}/agent-binding/start`, { method: "POST", body: { force } });
      if (!showResult(result)) schedule(1500);
    } catch (error) {
      statusLine.textContent = `无法开始绑定：${error.message}`;
      if (error.code === "WECHAT_QR_UNAVAILABLE") { errLine.textContent = "二维码不可用，请重新生成。"; errLine.classList.remove("hidden"); }
      if (error.code === "OPENCLAW_CONNECTION_REQUIRED" || error.code === "OPENCLAW_ADMIN_UNAVAILABLE") {
        errLine.textContent = "智能服务暂时不可用，请稍后重试。"; errLine.classList.remove("hidden");
      }
    } finally { startBtn.disabled = false; }
  }

  async function poll() {
    if (inFlight) return;
    if (Date.now() >= deadline) { stopPoll(); statusLine.textContent = "自动等待扫码已超时，需要时请重新生成二维码。"; return; }
    inFlight = true;
    try {
      // 轮询不重复提交大 PNG：仅在第一次发送二维码内容，此后传 null
      const result = await api(`/classes/${classId}/agent-binding/wait`, { method: "POST", body: { current_qr_data_url: null } });
      if (!showResult(result)) schedule(2000);
    } catch (error) {
      statusLine.textContent = `自动确认暂未完成：${error.message}，页面会继续重试。`;
      schedule(3500);
    } finally { inFlight = false; }
  }

  startBtn.addEventListener("click", () => start(true));
  return { el: box, start, stop: stopPoll };
}

/* ---------------- ProposalReview（仅 onboarding 最终创建 / 聊天 proposal） ---------------- */

export function proposalReview(proposal) {
  const preview = proposal.preview_json || {};
  const summary = preview.summary || {};
  const box = el("div", { class: "proposal-review" });
  box.append(el("h3", {}, preview.title || "写入预览"));
  if (summary && typeof summary === "object" && !Array.isArray(summary)) {
    box.append(el("div", { class: "metric-grid" },
      Object.entries(summary).map(([key, value]) => metricCard(SUMMARY_LABELS[key] || key, value ?? "—"))));
  }
  const issues = [];
  for (const name of preview.missing_fields || []) issues.push({ kind: "error", text: `缺少字段：${MISSING_LABELS[name] || name}` });
  for (const err of preview.validation_errors || []) issues.push({ kind: "error", text: `${err.field ? `${err.field}：` : ""}${err.message || err.msg || JSON.stringify(err)}` });
  for (const ev of preview.low_confidence_evidence || []) issues.push({ kind: "warn", text: `请核对：${ev.location || ev.summary || "文件内容"}` });
  if (issues.length) {
    box.append(el("div", { class: "issue-list" }, issues.map((issue) => el("div", { class: `issue issue-${issue.kind}` }, issue.text))));
  } else {
    box.append(el("div", { class: "issue issue-ok" }, "内容检查通过，没有缺失或格式问题。"));
  }
  if (preview.confirmation_message) box.append(el("p", { class: "muted" }, preview.confirmation_message));
  if (proposal.expires_at) box.append(el("p", { class: "muted" }, `预览有效期至 ${fmtDateTime(proposal.expires_at)} · 版本 ${proposal.revision}`));
  return box;
}

const SUMMARY_LABELS = {
  class_name: "班级", grade: "年级", subject_count: "科目数", student_count: "学生数",
  period_count: "节次数", timetable_item_count: "课程条数", record_count: "记录数",
};
const MISSING_LABELS = { name: "班级名称", grade: "年级", students: "学生名单", base_timetable: "基础课表" };

/* ---------------- OpenClaw 阻止态 ---------------- */

export function openclawBlocked(status, onRetry) {
  return el("div", { class: "blocked-panel", role: "alert" },
    el("b", {}, "智能服务暂时不可用"),
    el("p", {}, "文件识别和微信助手暂时不能使用，其他班级工作不受影响。"),
    el("button", { class: "secondary", type: "button", onclick: onRetry }, "重新检查"));
}
