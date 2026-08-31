const $ = (selector) => document.querySelector(selector);
const $$ = (selector) => [...document.querySelectorAll(selector)];
const API = "/api/v1";
const state = { token: sessionStorage.getItem("classclaw-user-token") || "", me: null, users: [], classes: [], agents: [], tables: [], agentPollTimer: null, agentPollInFlight: false, agentPollDeadline: 0, agentPollClassId: null };

const capabilities = [
  ["账户与权限", "管理员/班主任、唯一用户名、密码哈希、会话、停用和重置；班主任一账号一班级。", "/auth/* · /admin/users/*"],
  ["班级创建", "网页 onboarding 草稿、文件识别、名称冲突、最终预览、确认后原子创建。", "/class-onboarding/* · /write-proposals/*"],
  ["学生档案", "学生增删改查、同班学号唯一、同名候选、软删除和班级摘要。", "/students/* · /classes/*"],
  ["座位与值日", "座位快照/交换/恢复；值日规则、预览确认、替换、完成、评分与统计。", "/seating/* · /duty/*"],
  ["教学业务", "作业与未交状态、日常表现、考勤、考试成绩、排名和统计。", "/homework/* · /attendance/* · /exams/*"],
  ["课表与调课", "节次、基础/每日课表、临时覆盖、互换和批量长期调整。", "/classes/*/timetable · /lesson-*"],
  ["安排与留痕", "安排、提醒、附件和关键操作轻量审计。", "/arrangements/* · /attachments/* · /audit-logs"],
  ["分析与早报", "学生/班级综合分析、周期比较、关注学生、交叉分析、数据质量和每日早报。", "/analytics/* · /briefings/morning"],
  ["OpenClaw 集成", "插件健康、统一交互分析、班级隔离 Agent、微信二维码和可信 class_id 注入。", "/openclaw/status · /interaction-analyses · /agent-binding"],
  ["管理员调试", "用户、历史班级分配、智能体状态、二维码重试和脱敏只读数据库浏览。", "/admin/agents · /admin/database/*"],
];

function toast(message, bad = false) { const node = $("#toast"); node.textContent = message; node.className = `toast show${bad ? " bad" : ""}`; clearTimeout(toast.timer); toast.timer = setTimeout(() => node.className = "toast", 2600); }
function pretty(value) { return JSON.stringify(value, null, 2); }
function roleName(role) { return role === "admin" ? "管理员" : "班主任"; }
function statusTag(active) { return `<span class="tag ${active ? "" : "off"}">${active ? "启用" : "停用"}</span>`; }
function escapeHtml(value) { return String(value ?? "").replace(/[&<>'"]/g, (char) => ({"&":"&amp;","<":"&lt;",">":"&gt;","'":"&#39;",'"':"&quot;"}[char])); }

async function api(path, options = {}) {
  const headers = { "X-ClassClaw-Surface": "web", ...(options.headers || {}) };
  if (state.token) headers.Authorization = `Bearer ${state.token}`;
  if (options.body !== undefined && !(options.body instanceof FormData)) headers["Content-Type"] = "application/json";
  const response = await fetch(path.startsWith("/") && !path.startsWith(API) ? `${API}${path}` : path, { ...options, headers });
  let payload; try { payload = await response.json(); } catch { payload = { success: false, error: { message: `HTTP ${response.status}` } }; }
  if (!response.ok || payload.success === false) { const error = new Error(payload.error?.message || `HTTP ${response.status}`); error.status = response.status; error.payload = payload; throw error; }
  return payload.data;
}

function setTab(tab) {
  $$('[data-tab]').forEach((button) => button.classList.toggle("active", button.dataset.tab === tab));
  $$('[data-panel]').forEach((panel) => panel.classList.toggle("active", panel.dataset.panel === tab));
  if (tab === "users") loadUsers(); if (tab === "classes") loadClasses(); if (tab === "agents") loadAgents(); if (tab === "database") loadDatabase();
}

function showConsole(me) {
  state.me = me; $("#loginCard").classList.add("hidden"); $("#console").classList.remove("hidden"); $("#logout").classList.remove("hidden");
  $("#identityName").textContent = me.display_name || me.username; $("#identityRole").textContent = roleName(me.role); $("#avatar").textContent = me.role === "admin" ? "管" : "师";
  $("#metricUser").textContent = me.username; $("#metricUserSub").textContent = `${roleName(me.role)}${me.must_change_password ? " · 建议修改初始密码" : ""}`;
  $$('[data-admin]').forEach((node) => node.classList.toggle("hidden", me.role !== "admin"));
  $("#capabilityGrid").innerHTML = capabilities.map(([title, text, routes]) => `<article class="capability"><h3>${escapeHtml(title)}</h3><p>${escapeHtml(text)}</p><p><code>${escapeHtml(routes)}</code></p></article>`).join("");
}

async function login(event) {
  event.preventDefault(); $("#loginError").classList.add("hidden");
  try {
    const data = await api("/auth/login", { method: "POST", body: JSON.stringify({ username: $("#username").value.trim(), password: $("#password").value }) });
    state.token = data.access_token; sessionStorage.setItem("classclaw-user-token", state.token); showConsole(data.user); toast("登录成功"); await runChecks();
  } catch (error) { $("#loginError").textContent = error.message; $("#loginError").classList.remove("hidden"); }
}

async function restoreSession() { if (!state.token) return; try { showConsole(await api("/auth/me")); await runChecks(); } catch { state.token = ""; sessionStorage.removeItem("classclaw-user-token"); } }
async function logout() { try { await api("/auth/logout", { method: "POST", body: "{}" }); } catch {} state.token = ""; state.me = null; sessionStorage.removeItem("classclaw-user-token"); location.reload(); }

async function runChecks() {
  const checks = [["账户会话", "/auth/me"], ["OpenClaw 连接", "/openclaw/status"], ["班级读取", "/classes"]];
  if (state.me?.role === "admin") checks.push(["用户管理", "/admin/users"], ["智能体管理", "/admin/agents"], ["数据库调试", "/admin/database/overview"]);
  const box = $("#checkResults"); box.innerHTML = ""; let openclaw; let classes; let db;
  for (const [label, path] of checks) {
    const node = document.createElement("div"); node.className = "check"; node.innerHTML = `<b>…</b><div><strong>${escapeHtml(label)}</strong><div class="muted">${escapeHtml(path)}</div></div>`; box.append(node);
    try { const data = await api(path); node.classList.add("ok"); node.querySelector("b").textContent = "通过"; if (path === "/openclaw/status") openclaw = data; if (path === "/classes") classes = data; if (path.endsWith("overview")) db = data; }
    catch (error) { node.classList.add("fail"); node.querySelector("b").textContent = "×"; node.querySelector(".muted").textContent = `${path} — ${error.message}`; }
  }
  $("#metricOpenClaw").textContent = openclaw?.ready ? "已连接" : "未就绪"; $("#metricOpenClawSub").textContent = openclaw?.error || openclaw?.gateway_url || "查看巡检结果";
  $("#metricClasses").textContent = classes?.total ?? "—"; $("#metricTables").textContent = db?.tables?.length ?? "—";
}

async function loadUsers() {
  if (state.me?.role !== "admin") return; try { state.users = await api("/admin/users"); } catch (error) { toast(error.message, true); return; }
  const rows = $("#userRows"); rows.innerHTML = state.users.map((user) => `<tr><td><b>${escapeHtml(user.username)}</b>${user.must_change_password ? '<br><span class="tag warn">初始密码</span>' : ""}</td><td>${escapeHtml(roleName(user.role))}</td><td>${escapeHtml(user.display_name || "—")}</td><td>${escapeHtml(user.class_id || "未分配")}</td><td>${statusTag(user.is_active)}</td><td><div class="row-actions">${user.role === "head_teacher" ? `<button class="secondary" data-user-action="reset" data-id="${user.id}">重置密码</button><button class="secondary" data-user-action="toggle" data-active="${user.is_active}" data-id="${user.id}">${user.is_active ? "停用" : "启用"}</button><button class="secondary" data-user-action="assign" data-id="${user.id}">分配班级</button>` : "唯一管理员"}</div></td></tr>`).join("");
}

async function createUser(event) { event.preventDefault(); const form = new FormData(event.currentTarget); try { await api("/admin/users", { method: "POST", body: JSON.stringify(Object.fromEntries(form)) }); toast("班主任账号已创建"); event.currentTarget.reset(); event.currentTarget.elements.password.value = "32767"; await loadUsers(); } catch (error) { toast(error.message, true); } }
async function userAction(button) {
  const id = button.dataset.id; const action = button.dataset.userAction;
  try {
    if (action === "reset") await api(`/admin/users/${id}/reset-password`, { method: "POST", body: "{}" });
    if (action === "toggle") await api(`/admin/users/${id}`, { method: "PATCH", body: JSON.stringify({ is_active: button.dataset.active !== "true" }) });
    if (action === "assign") { await loadClasses(); const available = state.classes.filter((item) => !item.owner_user_id); if (!available.length) throw new Error("没有未分配的历史班级"); const choices = available.map((item, index) => `${index + 1}. ${item.name} (${item.id})`).join("\n"); const selected = prompt(`输入要分配的序号：\n${choices}`, "1"); if (!selected) return; const cls = available[Number(selected) - 1]; if (!cls) throw new Error("班级序号无效"); await api(`/admin/users/${id}/assign-class`, { method: "POST", body: JSON.stringify({ class_id: cls.id }) }); }
    toast("操作成功"); await loadUsers();
  } catch (error) { toast(error.message, true); }
}

async function loadClasses() {
  try { const data = await api("/classes"); state.classes = data.items || []; $("#classCards").innerHTML = state.classes.length ? state.classes.map((cls) => `<article class="class-card"><span class="tag">${escapeHtml(cls.status)}</span><h3>${escapeHtml(cls.name)}</h3><p>${escapeHtml(cls.grade)} · ${escapeHtml(cls.room || "未设置教室")}<br>班主任：${escapeHtml(cls.head_teacher || "未设置")}<br><code>${escapeHtml(cls.id)}</code></p></article>`).join("") : '<div class="empty">当前没有班级，可打开创建向导建立第一个班级。</div>'; }
  catch (error) { $("#classCards").innerHTML = `<div class="empty error">${escapeHtml(error.message)}</div>`; toast(error.message, true); }
}

async function loadAgents() {
  if (state.me?.role !== "admin") return; try { state.agents = await api("/admin/agents"); $("#agentCards").innerHTML = state.agents.length ? state.agents.map(({binding, class: cls, owner}) => `<article class="agent-card"><span class="tag ${binding.status === "linked" ? "" : "warn"}">${escapeHtml(binding.status)}</span><h3>${escapeHtml(binding.agent_name)}</h3><p>班级：${escapeHtml(cls.name)}<br>账号：${escapeHtml(owner?.username || "未分配")}<br>OpenClaw ID：${escapeHtml(binding.openclaw_agent_id || "尚未创建")}<br>微信：${escapeHtml(binding.channel_account_id || "尚未绑定")}${binding.last_error ? `<br><span class="error">${escapeHtml(binding.last_error)}</span>` : ""}</p><button class="secondary" data-agent-start="${cls.id}">刷新配置并生成二维码</button></article>`).join("") : '<div class="empty">尚无班级智能体。创建班级后会自动生成绑定记录。</div>'; } catch (error) { toast(error.message, true); }
}
async function startAgent(classId) {
  stopAgentPolling(); state.agentPollClassId = classId; state.agentPollDeadline = Date.now() + 5 * 60 * 1000;
  try {
    const result = await api(`/admin/agents/${classId}/wechat/start`, { method: "POST", body: JSON.stringify({ force: true }) });
    if (result.connected && result.route_ready !== false) { toast("微信与消息路由已经连接"); await loadAgents(); return; }
    $("#agentQr").src = result.qr_data_url; $("#qrMessage").textContent = `${result.message || "请使用微信扫码。"} 正在自动等待扫码结果。`; $("#qrBox").classList.remove("hidden"); scheduleAgentPoll();
  } catch (error) { toast(error.message, true); }
}
function stopAgentPolling() { clearTimeout(state.agentPollTimer); state.agentPollTimer = null; state.agentPollInFlight = false; }
function scheduleAgentPoll(delay = 1200) { clearTimeout(state.agentPollTimer); state.agentPollTimer = setTimeout(pollAgentBinding, delay); }
async function pollAgentBinding() {
  if (!state.agentPollClassId || state.agentPollInFlight) return;
  if (Date.now() >= state.agentPollDeadline) { stopAgentPolling(); $("#qrMessage").textContent = "自动等待扫码已超时，请重新生成二维码。"; return; }
  state.agentPollInFlight = true;
  try {
    const result = await api(`/classes/${state.agentPollClassId}/agent-binding/wait`, { method: "POST", body: "{}" });
    if (result.connected && result.route_ready !== false) { stopAgentPolling(); $("#qrBox").classList.add("hidden"); toast("微信绑定完成，消息路由已就绪"); await loadAgents(); }
    else { if (result.qr_data_url) $("#agentQr").src = result.qr_data_url; $("#qrMessage").textContent = `${result.message || "尚未检测到扫码。"} 正在自动等待。`; scheduleAgentPoll(1800); }
  } catch (error) { $("#qrMessage").textContent = `自动确认暂未完成：${error.message}。网页会继续重试。`; scheduleAgentPoll(3000); }
  finally { state.agentPollInFlight = false; }
}

async function loadDatabase() { if (state.me?.role !== "admin") return; try { const data = await api("/admin/database/overview"); state.tables = data.tables; $("#tableList").innerHTML = data.tables.map((table) => `<button data-table="${escapeHtml(table.name)}"><span>${escapeHtml(table.name)}</span><b>${table.row_count}</b></button>`).join(""); } catch (error) { toast(error.message, true); } }
async function loadTable(name) { try { const data = await api(`/admin/database/tables/${encodeURIComponent(name)}?limit=100`); $("#tableMeta").textContent = `${name} · ${data.total} 行 · 显示 ${data.items.length} 行`; $("#databaseRows").textContent = pretty(data.items); $$('[data-table]').forEach((button) => button.classList.toggle("active", button.dataset.table === name)); } catch (error) { toast(error.message, true); } }

async function sendApi(event) {
  event.preventDefault(); const method = $("#apiMethod").value; let body; if (!["GET", "DELETE"].includes(method) && $("#apiBody").value.trim()) { try { body = JSON.stringify(JSON.parse($("#apiBody").value)); } catch { toast("请求体不是有效 JSON", true); return; } }
  const started = performance.now(); try { const data = await api($("#apiPath").value.trim(), { method, ...(body !== undefined ? {body} : {}) }); $("#responseStatus").textContent = `成功 · ${Math.round(performance.now() - started)} ms`; $("#apiResponse").textContent = pretty(data); } catch (error) { $("#responseStatus").textContent = `失败 · HTTP ${error.status || "?"}`; $("#apiResponse").textContent = pretty(error.payload || {error: error.message}); }
}

$("#loginForm").addEventListener("submit", login); $("#logout").addEventListener("click", logout); $("#runChecks").addEventListener("click", runChecks); $("#reloadUsers").addEventListener("click", loadUsers); $("#createUser").addEventListener("submit", createUser); $("#reloadClasses").addEventListener("click", loadClasses); $("#reloadAgents").addEventListener("click", loadAgents); $("#reloadDatabase").addEventListener("click", loadDatabase); $("#apiForm").addEventListener("submit", sendApi); $("#closeQr").addEventListener("click", () => $("#qrBox").classList.add("hidden"));
$("#tabs").addEventListener("click", (event) => { const button = event.target.closest("[data-tab]"); if (button) setTab(button.dataset.tab); });
$("#userRows").addEventListener("click", (event) => { const button = event.target.closest("[data-user-action]"); if (button) userAction(button); });
$("#agentCards").addEventListener("click", (event) => { const button = event.target.closest("[data-agent-start]"); if (button) startAgent(button.dataset.agentStart); });
$("#tableList").addEventListener("click", (event) => { const button = event.target.closest("[data-table]"); if (button) loadTable(button.dataset.table); });
restoreSession();
