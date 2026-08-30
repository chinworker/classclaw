const state = { session: null, proposal: null, step: 0, openclawConnected: false, classId: null, classNameCheck: null, classNameCheckTimer: null, agentProvisioned: false, bindingPollTimer: null, bindingPollInFlight: false, bindingPollDeadline: 0 };
const $ = (selector) => document.querySelector(selector);
const $$ = (selector) => [...document.querySelectorAll(selector)];
const form = $("#wizard");

function token() { return $("#apiToken").value.trim(); }
function operator() { return $("#operator").value.trim() || "网页端用户"; }
function toast(message, error = false) {
  const node = $("#toast"); node.textContent = message; node.className = `toast show${error ? " error" : ""}`;
  clearTimeout(toast.timer); toast.timer = setTimeout(() => { node.className = "toast"; }, 4200);
}
async function api(path, options = {}) {
  const headers = new Headers(options.headers || {}); headers.set("X-ClassClaw-Surface", "web");
  if (token()) headers.set("Authorization", `Bearer ${token()}`);
  if (options.body && !(options.body instanceof FormData)) headers.set("Content-Type", "application/json");
  const response = await fetch(`/api/v1${path}`, { ...options, headers });
  const payload = await response.json().catch(() => ({}));
  if (!response.ok || payload.success === false) {
    const error = new Error(payload.error?.message || `HTTP ${response.status}`); error.code = payload.error?.code; error.details = payload.error?.details;
    if (error.code === "OPENCLAW_CONNECTION_REQUIRED") setConnected(false, error.message);
    throw error;
  }
  return payload.data;
}
function setConnected(connected, detail = "") {
  state.openclawConnected = connected; form.classList.toggle("locked", !connected); $("#newSession").disabled = !connected;
  $("#openclawBadge").textContent = connected ? "已连接" : "未连接"; $("#openclawBadge").className = `link-badge ${connected ? "ok" : detail ? "error" : ""}`;
  $("#healthDot").className = `dot ${connected ? "ok" : detail ? "error" : ""}`; $("#healthText").textContent = connected ? "OpenClaw 已连接" : "OpenClaw 未连接";
  $("#connectionDetail").textContent = detail || "连接检查会同时验证 Gateway、ClassClaw 插件和创建专属智能体所需的管理接口。";
  $("#reloadSession").disabled = !connected || !state.session;
}
function ensureConnected() { if (!state.openclawConnected) throw new Error("请先连接 OpenClaw"); }
function clean(object) { return Object.fromEntries(Object.entries(object).filter(([, value]) => value !== "" && value !== null && value !== undefined)); }
function classInfo() {
  const data = new FormData(form); return clean({ name: data.get("class_name")?.trim(), grade: data.get("grade")?.trim(), head_teacher: data.get("head_teacher")?.trim(), room: data.get("room")?.trim(), semester_name: data.get("semester_name")?.trim(), semester_start: data.get("semester_start"), semester_end: data.get("semester_end") });
}
function renderClassNameStatus(message, kind = "") { const node = $("#classNameStatus"); node.textContent = message; node.className = `field-status${kind ? ` ${kind}` : ""}`; }
async function checkClassName() {
  const input = form.elements.class_name; const name = input.value.trim();
  clearTimeout(state.classNameCheckTimer);
  if (!name) { state.classNameCheck = null; input.setCustomValidity(""); renderClassNameStatus("输入后将检查已有班级和 OpenClaw 智能体"); return true; }
  if (!state.openclawConnected) { state.classNameCheck = null; input.setCustomValidity(""); renderClassNameStatus("连接 OpenClaw 后自动检查名称"); return false; }
  renderClassNameStatus("正在检查班级和智能体名称…", "checking");
  try {
    const result = await api(`/class-onboarding/name-check?class_name=${encodeURIComponent(name)}`);
    if (form.elements.class_name.value.trim() !== name) return false;
    state.classNameCheck = { name, available: result.available }; input.setCustomValidity(result.available ? "" : result.message);
    renderClassNameStatus(result.message, result.available ? "ok" : "error"); return result.available;
  } catch (error) {
    if (form.elements.class_name.value.trim() !== name) return false;
    state.classNameCheck = { name, available: false }; input.setCustomValidity(error.message); renderClassNameStatus(error.message, "error"); return false;
  }
}
async function ensureClassNameAvailable() {
  const name = form.elements.class_name.value.trim();
  if (!name) throw new Error("请填写班级名称");
  if (state.classNameCheck?.name !== name || !state.classNameCheck.available) {
    if (!await checkClassName()) throw new Error(form.elements.class_name.validationMessage || "班级名称不可用");
  }
}
function tableInput(value, key, type = "text") { const input = document.createElement("input"); input.type = type; input.value = value ?? ""; input.dataset.key = key; return input; }
function removeButton() { const button = document.createElement("button"); button.type = "button"; button.className = "row-remove"; button.textContent = "删除"; button.addEventListener("click", () => button.closest("tr").remove()); return button; }
function appendRow(tableName, row = {}) {
  const tbody = $(`#${tableName}Table tbody`); const tr = document.createElement("tr");
  const specs = tableName === "students"
    ? [["student_no", "text"], ["name", "text"], ["gender", "text"], ["phone", "text"], ["boarding_status", "text"], ["group_no", "text"], ["notes", "text"]]
    : tableName === "periods" ? [["period_no", "number"], ["name", "text"]]
      : [["weekday", "number"], ["period_no", "number"], ["subject", "text"], ["teacher", "text"], ["room", "text"]];
  for (const [key, type] of specs) { const td = document.createElement("td"); td.append(tableInput(row[key], key, type)); tr.append(td); }
  const action = document.createElement("td"); action.append(removeButton()); tr.append(action); tbody.append(tr);
}
function rows(tableName) {
  return [...$(`#${tableName}Table tbody`).rows].map((tr) => {
    const row = {}; tr.querySelectorAll("input[data-key]").forEach((input) => { let value = input.value.trim(); if (["weekday", "period_no"].includes(input.dataset.key)) value = value === "" ? null : Number(value); if (value !== "" && value !== null) row[input.dataset.key] = value; });
    if (tableName === "students") { row.tags = []; row.status = "active"; }
    if (tableName === "periods") { row.sort_order = row.period_no || 0; row.enabled = true; }
    return row;
  });
}
function renderTables(draft = {}) {
  for (const name of ["students", "periods", "timetable"]) $(`#${name}Table tbody`).replaceChildren();
  (draft.students || []).forEach((row) => appendRow("students", row)); (draft.periods || []).forEach((row) => appendRow("periods", row)); (draft.base_timetable || []).forEach((row) => appendRow("timetable", row));
  $("#studentsEmpty").classList.toggle("hidden", (draft.students || []).length > 0); $("#timetableEmpty").classList.toggle("hidden", (draft.base_timetable || []).length > 0);
}
function populate(draft = {}) {
  const info = draft.class_info || {}; const values = { class_name: info.name, grade: info.grade, head_teacher: info.head_teacher, room: info.room, semester_name: info.semester_name, semester_start: info.semester_start, semester_end: info.semester_end };
  Object.entries(values).forEach(([name, value]) => { if (form.elements.namedItem(name)) form.elements.namedItem(name).value = value || ""; }); renderTables(draft); checkClassName();
}
function draft() { return { class_info: classInfo(), students: rows("students"), periods: rows("periods"), base_timetable: rows("timetable") }; }
function setStep(step) {
  state.step = Math.max(0, Math.min(3, step)); $$('[data-panel]').forEach((node) => node.classList.toggle("active", Number(node.dataset.panel) === state.step)); $$('[data-step]').forEach((node) => node.classList.toggle("active", Number(node.dataset.step) === state.step));
  $("#previous").disabled = state.step === 0; $("#next").classList.toggle("hidden", state.step === 3); $("#preview").classList.toggle("hidden", state.step !== 3); window.scrollTo({ top: 0, behavior: "smooth" });
}
function setSession(session) {
  state.session = session; $("#sessionId").textContent = session?.id || "尚未创建"; $("#reloadSession").disabled = !state.openclawConnected || !session;
  if (session) { const url = new URL(location.href); url.searchParams.set("session", session.id); history.replaceState({}, "", url); $("#saveState").textContent = `草稿版本 ${session.revision}`; }
  if (session?.class_id) {
    state.classId = session.class_id; $("#bindingPanel").classList.remove("hidden"); $("#saveDraft").disabled = true; $("#preview").disabled = true;
    $("#startBinding").classList.remove("hidden"); $("#skipBinding").classList.remove("hidden"); $("#startBinding").disabled = false; $("#skipBinding").disabled = false; $("#startBinding").textContent = "绑定微信";
    $("#bindingMessage").textContent = "班级已创建。可选择绑定微信，也可以暂不绑定。";
  }
}
async function connect() {
  sessionStorage.setItem("classclaw-token", token()); sessionStorage.setItem("classclaw-operator", operator()); $("#connectOpenClaw").disabled = true;
  try { const status = await api("/openclaw/status?refresh=true"); if (!status.ready) throw new Error(status.error || "OpenClaw 连接检查未通过"); setConnected(true, `Gateway、ClassClaw 插件和管理接口均已连接；主智能体：${status.agent_id}`); const id = new URL(location.href).searchParams.get("session"); if (id) await loadSession(id); else await checkClassName(); toast("OpenClaw 已连接，可以创建班级"); }
  catch (error) { setConnected(false, error.message); toast(error.message, true); }
  finally { $("#connectOpenClaw").disabled = false; }
}
async function createSession() {
  ensureConnected(); if (form.elements.class_name.value.trim()) await ensureClassNameAvailable(); const session = await api("/class-onboarding/sessions", { method: "POST", body: JSON.stringify({ created_by: operator(), initial_draft: { class_info: classInfo(), students: [], periods: [], base_timetable: [] } }) });
  setSession(session); state.proposal = null; state.classId = null; renderTables(session.draft_json); setStep(0); $("#bindingPanel").classList.add("hidden"); toast("网页班级草稿已建立");
}
async function saveDraft() {
  ensureConnected(); await ensureClassNameAvailable(); if (!state.session) await createSession();
  const updated = await api(`/class-onboarding/sessions/${state.session.id}`, { method: "PATCH", body: JSON.stringify({ expected_revision: state.session.revision, current_step: ["class_info", "students", "timetable", "review"][state.step], draft_patch: draft(), replace_lists: true }) });
  setSession(updated); state.proposal = null; $("#reviewContent").classList.add("hidden"); $("#reviewEmpty").classList.remove("hidden"); toast("当前结构化数据已保存，不会再由智能体改写"); return updated;
}
async function loadSession(id = state.session?.id) { if (!id) return; const current = await api(`/class-onboarding/sessions/${id}`); setSession(current); populate(current.draft_json || {}); }
async function uploadFiles(zone, files) {
  ensureConnected(); if (!files.length) return; if (files.length > 8) throw new Error("每次最多上传 8 个文件"); if (!state.session) await createSession(); else await saveDraft();
  zone.classList.add("processing"); const body = new FormData(); body.set("target_section", zone.dataset.target); body.set("expected_revision", state.session.revision); [...files].forEach((file) => body.append("files", file));
  try { toast("OpenClaw 正在独立解析本次文件…"); const result = await api(`/class-onboarding/sessions/${state.session.id}/files`, { method: "POST", body }); setSession(result.session); populate(result.session.draft_json || {}); zone.classList.add("success"); const warnings = result.analysis?.warnings || []; toast(warnings.length ? `解析完成，有 ${warnings.length} 项需核对` : "解析完成，请核对下方结构化表格", warnings.length > 0); }
  catch (error) { zone.classList.add("failed"); toast(error.message, true); throw error; }
  finally { zone.classList.remove("processing"); setTimeout(() => zone.classList.remove("success", "failed"), 2600); }
}
function showReview(proposal) {
  const preview = proposal.preview_json; const summary = preview.summary || {}; const metrics = [[summary.student_count || 0, "学生"], [summary.subject_count || 0, "课表科目"], [summary.period_count || 0, "节次"], [summary.timetable_item_count || 0, "课程"]];
  $("#reviewSummary").innerHTML = metrics.map(([value, label]) => `<div class="metric"><b>${value}</b><span>${label}</span></div>`).join("");
  const issues = [...(preview.missing_fields || []).map((value) => `缺少：${value}`), ...(preview.validation_errors || []).map((value) => value.message || JSON.stringify(value)), ...(preview.low_confidence_evidence || []).map((value) => `低置信度：${value.location || value.summary || "文件字段"}`)];
  $("#reviewWarnings").innerHTML = issues.length ? issues.map((value) => `<div class="warning error"></div>`).join("") : '<div class="warning">后端校验通过。科目由当前课表归纳，未设置科目满分。</div>';
  $("#reviewWarnings").querySelectorAll(".warning.error").forEach((node, index) => { node.textContent = issues[index]; });
  $("#reviewEmpty").classList.add("hidden"); $("#reviewContent").classList.remove("hidden"); $("#commit").disabled = true;
}
async function preview() { await saveDraft(); state.proposal = await api(`/class-onboarding/sessions/${state.session.id}/preview`, { method: "POST", body: JSON.stringify({ requested_by: operator() }) }); showReview(state.proposal); toast(state.proposal.preview_json.ready ? "最终预览已生成，请逐项复核" : "预览仍有缺失或格式问题", !state.proposal.preview_json.ready); }
function updateCommit() { const expected = form.elements.class_name.value.trim(); $("#commit").disabled = !state.proposal?.preview_json?.ready || !$("#confirmCheck").checked || $("#confirmName").value.trim() !== expected; }
async function commit() {
  if (!state.proposal) return; $("#commit").disabled = true;
  try { const completed = await api(`/write-proposals/${state.proposal.id}/confirm`, { method: "POST", body: JSON.stringify({ revision: state.proposal.revision, confirmed_by: operator(), confirmation_note: "网页端已核对结构化名单与课表" }) }); state.classId = completed.result_json.class_id; $("#reviewContent").classList.add("hidden"); $("#reviewEmpty").classList.add("hidden"); $("#bindingPanel").classList.remove("hidden"); $("#saveDraft").disabled = true; $("#preview").disabled = true; toast("班级已创建，正在创建专属智能体…"); await provisionAgent(); }
  catch (error) { toast(error.message, true); updateCommit(); }
}
async function provisionAgent() {
  if (!state.classId) return null;
  $("#startBinding").disabled = true; $("#skipBinding").disabled = true; $("#bindingMessage").textContent = "正在创建专属智能体并写入身份与系统提示词…";
  try {
    const binding = await api(`/classes/${state.classId}/agent-binding/provision`, { method: "POST", body: "{}" });
    state.agentProvisioned = true; $("#startBinding").disabled = false; $("#skipBinding").disabled = false; $("#startBinding").textContent = "绑定微信"; $("#bindingMessage").textContent = `专属智能体 ${binding.agent_name} 已创建。微信为可选项，可现在绑定或暂时跳过。`; return binding;
  } catch (error) {
    state.agentProvisioned = false; $("#startBinding").disabled = false; $("#skipBinding").disabled = false; $("#bindingMessage").textContent = `智能体创建失败：${error.message}。可以重试绑定或暂后处理。`; toast(error.message, true); return null;
  }
}
async function startBinding(force = false) {
  if (!state.classId) return; stopBindingPolling(); state.bindingPollDeadline = Date.now() + 5 * 60 * 1000; $("#startBinding").disabled = true; $("#skipBinding").disabled = true; $("#bindingMessage").textContent = "正在启动微信登录会话…";
  try { const result = await api(`/classes/${state.classId}/agent-binding/start`, { method: "POST", body: JSON.stringify({ force }) }); if (!showBinding(result)) scheduleBindingPoll(); }
  catch (error) { $("#qrArea").classList.add("hidden"); $("#bindingMessage").textContent = `${error.message}。修复 OpenClaw 配置后可重试，或选择暂不绑定微信。`; $("#startBinding").disabled = false; $("#skipBinding").disabled = false; toast(error.message, true); }
}
function showBinding(result) {
  const binding = result.binding || {}; if (result.connected && result.route_ready !== false) { stopBindingPolling(); $("#bindingMessage").textContent = `绑定完成：${binding.agent_name} 已连接微信账号 ${binding.channel_account_id || ""}，消息路由已就绪。`; $("#qrArea").classList.add("hidden"); $("#startBinding").classList.add("hidden"); $("#skipBinding").classList.add("hidden"); toast("班级专属智能体、微信与消息路由已就绪"); return true; }
  $("#bindingMessage").textContent = `${result.message || "专属智能体已创建，请扫码绑定微信。"} 网页正在自动等待扫码结果。`;
  const qr = $("#wechatQr"); const qrError = $("#qrError");
  if (result.qr_data_url) { qr.onload = () => { qr.classList.remove("hidden"); qrError.classList.add("hidden"); }; qr.onerror = () => { qr.classList.add("hidden"); qrError.textContent = "二维码图片加载失败，请点击“重新生成二维码”。"; qrError.classList.remove("hidden"); }; qr.src = result.qr_data_url; $("#qrArea").classList.remove("hidden"); }
  else { qr.classList.add("hidden"); qrError.textContent = "未获得二维码，请点击“重新生成二维码”。"; qrError.classList.remove("hidden"); $("#qrArea").classList.remove("hidden"); }
  $("#startBinding").disabled = false; $("#skipBinding").disabled = false; $("#startBinding").textContent = "重新生成二维码";
  return false;
}
async function skipBinding() {
  stopBindingPolling(); $("#skipBinding").disabled = true; $("#startBinding").disabled = true;
  try {
    await api(`/classes/${state.classId}/agent-binding/provision`, { method: "POST", body: "{}" }); state.agentProvisioned = true;
    $("#qrArea").classList.add("hidden"); $("#skipBinding").classList.add("hidden"); $("#startBinding").disabled = false; $("#startBinding").textContent = "以后绑定微信"; $("#bindingMessage").textContent = "本班已选择暂不绑定微信；班级和专属智能体均已保留，以后仍可在这里绑定。"; toast("已跳过微信绑定");
  } catch (error) { $("#skipBinding").disabled = false; $("#startBinding").disabled = false; $("#bindingMessage").textContent = `暂时无法保存选择：${error.message}`; toast(error.message, true); }
}
function stopBindingPolling() { clearTimeout(state.bindingPollTimer); state.bindingPollTimer = null; state.bindingPollInFlight = false; }
function scheduleBindingPoll(delay = 1200) { clearTimeout(state.bindingPollTimer); state.bindingPollTimer = setTimeout(pollBinding, delay); }
async function pollBinding() {
  if (!state.classId || state.bindingPollInFlight) return;
  if (Date.now() >= state.bindingPollDeadline) { stopBindingPolling(); $("#bindingMessage").textContent = "自动等待扫码已超时；需要时可重新生成二维码。"; return; }
  state.bindingPollInFlight = true;
  try {
    const result = await api(`/classes/${state.classId}/agent-binding/wait`, { method: "POST", body: "{}" });
    if (!showBinding(result)) scheduleBindingPoll(1800);
  } catch (error) {
    $("#bindingMessage").textContent = `自动确认暂未完成：${error.message}。网页会继续重试。`;
    scheduleBindingPoll(3000);
  } finally { state.bindingPollInFlight = false; }
}
form.addEventListener("submit", (event) => event.preventDefault()); $("#connectOpenClaw").addEventListener("click", connect); $("#newSession").addEventListener("click", createSession); $("#reloadSession").addEventListener("click", () => loadSession().catch((error) => toast(error.message, true)));
form.elements.class_name.addEventListener("input", () => { state.classNameCheck = null; form.elements.class_name.setCustomValidity(""); clearTimeout(state.classNameCheckTimer); renderClassNameStatus("等待检查…", "checking"); state.classNameCheckTimer = setTimeout(() => checkClassName(), 450); });
form.elements.class_name.addEventListener("blur", () => checkClassName());
$("#saveDraft").addEventListener("click", () => saveDraft().catch((error) => toast(error.message, true))); $("#previous").addEventListener("click", () => setStep(state.step - 1)); $("#next").addEventListener("click", async () => { try { await saveDraft(); setStep(state.step + 1); } catch (error) { toast(error.message, true); } }); $("#preview").addEventListener("click", () => preview().catch((error) => toast(error.message, true))); $("#confirmCheck").addEventListener("change", updateCommit); $("#confirmName").addEventListener("input", updateCommit); $("#commit").addEventListener("click", commit); $("#startBinding").addEventListener("click", () => startBinding(true)); $("#skipBinding").addEventListener("click", skipBinding);
$$('[data-step]').forEach((button) => button.addEventListener("click", () => setStep(Number(button.dataset.step)))); $$(".add-row").forEach((button) => button.addEventListener("click", () => { appendRow(button.dataset.table); $(`#${button.dataset.table === "timetable" ? "timetable" : button.dataset.table === "students" ? "students" : "timetable"}Empty`)?.classList.add("hidden"); }));
$$('.dropzone').forEach((zone) => { const input = zone.querySelector('input[type="file"]'); zone.addEventListener("click", () => input.click()); zone.addEventListener("keydown", (event) => { if (["Enter", " "].includes(event.key)) { event.preventDefault(); input.click(); } }); input.addEventListener("change", () => uploadFiles(zone, input.files).catch(() => {})); zone.addEventListener("dragover", (event) => { event.preventDefault(); zone.classList.add("dragging"); }); zone.addEventListener("dragleave", () => zone.classList.remove("dragging")); zone.addEventListener("drop", (event) => { event.preventDefault(); zone.classList.remove("dragging"); uploadFiles(zone, event.dataTransfer.files).catch(() => {}); }); });

$("#apiToken").value = sessionStorage.getItem("classclaw-token") || ""; $("#operator").value = sessionStorage.getItem("classclaw-operator") || "班主任"; renderTables();
if (token()) connect(); else setConnected(false);
