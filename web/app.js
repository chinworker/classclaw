// ClassClaw 工作台入口：登录恢复、AppShell、路由守卫、页面挂载。

import { el, clear, toast, todayStr, installEnhancedControls } from "./js/util.js";
import { onUnauthorized } from "./js/api.js";
import { login, logout, restoreSession } from "./js/auth.js";
import { state, clearSession, refreshIdentity, refreshClassInfo, refreshOpenclaw, savePrefs } from "./js/state.js";
import { defineRoutes, setRouteResolver, startRouter, dispatch, navigate, parseHash } from "./js/router.js";
import { field, fieldError } from "./js/components.js";

const root = document.getElementById("root");
installEnhancedControls();

const TEACHER_NAV = [
  { group: "工作台", items: [
    { path: "/dashboard", label: "今日仪表盘", requiresClass: true },
    { path: "/briefing", label: "每日早报", requiresClass: true },
  ]},
  { group: "快捷查询", items: [
    { path: "/query", label: "班级问题查询", requiresClass: true },
  ]},
  { group: "学生中心", items: [
    { path: "/students", label: "学生档案", requiresClass: true },
    { path: "/seating", label: "座位表", requiresClass: true },
    { path: "/events", label: "日常表现", requiresClass: true },
  ]},
  { group: "教学管理", items: [
    { path: "/homework", label: "作业", requiresClass: true },
    { path: "/attendance", label: "考勤", requiresClass: true },
    { path: "/exams", label: "成绩与考试", requiresClass: true },
    { path: "/timetable", label: "课表与调课", requiresClass: true },
  ]},
  { group: "班级事务", items: [
    { path: "/duty", label: "值日管理", requiresClass: true },
    { path: "/arrangements", label: "日常安排", requiresClass: true },
    { path: "/attachments", label: "班级资料", requiresClass: true },
    { path: "/class-settings", label: "班级设置", requiresClass: true },
  ]},
  { group: "数据分析", items: [
    { path: "/analytics/students", label: "学生分析", requiresClass: true },
    { path: "/analytics/class", label: "班级分析", requiresClass: true },
    { path: "/analytics/attention", label: "重点关注", requiresClass: true },
  ]},
  { group: "工作流", items: [
    { path: "/workflow", label: "待确认记录" },
  ]},
  { group: "智能体", items: [
    { path: "/agent", label: "班级助手与微信" },
  ]},
  { group: "账户", items: [
    { path: "/account", label: "账户设置" },
  ]},
];

const ADMIN_NAV = [
  { group: "控制台", items: [
    { path: "/admin/overview", label: "运行概览" },
  ]},
  { group: "资源", items: [
    { path: "/admin/users", label: "用户" },
    { path: "/admin/classes", label: "班级" },
    { path: "/admin/openclaw", label: "OpenClaw" },
  ]},
  { group: "可观测性", items: [
    { path: "/admin/usage", label: "使用量与 Token" },
    { path: "/admin/logs", label: "运行日志" },
    { path: "/admin/database", label: "数据库" },
    { path: "/admin/audit", label: "审计日志" },
  ]},
  { group: "系统", items: [
    { path: "/admin/settings", label: "功能与常量" },
  ]},
];

const NAV = [...TEACHER_NAV, ...ADMIN_NAV];

const PAGE_META = {};
for (const group of NAV) for (const item of group.items) PAGE_META[item.path] = item;

const routes = [
  { path: "/dashboard", title: "今日仪表盘", loader: () => import("./js/pages/dashboard.js"), requiresClass: true },
  { path: "/briefing", title: "每日早报", loader: () => import("./js/pages/briefing.js"), requiresClass: true },
  { path: "/query", title: "快捷查询", loader: () => import("./js/pages/query.js"), requiresClass: true },
  { path: "/students", title: "学生档案", loader: () => import("./js/pages/students.js"), requiresClass: true },
  { path: "/seating", title: "座位表", loader: () => import("./js/pages/seating.js"), requiresClass: true },
  { path: "/events", title: "日常表现", loader: () => import("./js/pages/events.js"), requiresClass: true },
  { path: "/homework", title: "作业", loader: () => import("./js/pages/homework.js"), requiresClass: true },
  { path: "/attendance", title: "考勤", loader: () => import("./js/pages/attendance.js"), requiresClass: true },
  { path: "/exams", title: "成绩与考试", loader: () => import("./js/pages/exams.js"), requiresClass: true },
  { path: "/timetable", title: "课表与调课", loader: () => import("./js/pages/timetablePage.js"), requiresClass: true },
  { path: "/duty", title: "值日管理", loader: () => import("./js/pages/duty.js"), requiresClass: true },
  { path: "/arrangements", title: "日常安排", loader: () => import("./js/pages/arrangements.js"), requiresClass: true },
  { path: "/attachments", title: "附件", loader: () => import("./js/pages/attachments.js"), requiresClass: true },
  { path: "/class-settings", title: "班级设置", loader: () => import("./js/pages/classSettings.js"), requiresClass: true },
  { path: "/analytics/students", title: "学生分析", loader: () => import("./js/pages/analytics.js"), requiresClass: true, section: "students" },
  { path: "/analytics/class", title: "班级分析", loader: () => import("./js/pages/analytics.js"), requiresClass: true, section: "class" },
  { path: "/analytics/attention", title: "重点关注", loader: () => import("./js/pages/analytics.js"), requiresClass: true, section: "attention" },
  { path: "/workflow", title: "待确认记录", loader: () => import("./js/pages/workflow.js") },
  { path: "/agent", title: "班级助手与微信", loader: () => import("./js/pages/agent.js") },
  { path: "/admin/overview", title: "运行概览", loader: () => import("./js/pages/adminOverview.js"), adminOnly: true },
  { path: "/admin/users", title: "用户管理", loader: () => import("./js/pages/adminUsers.js"), adminOnly: true },
  { path: "/admin/classes", title: "班级管理", loader: () => import("./js/pages/adminClasses.js"), adminOnly: true },
  { path: "/admin/openclaw", title: "OpenClaw", loader: () => import("./js/pages/adminAgents.js"), adminOnly: true },
  { path: "/admin/agents", title: "智能体管理", loader: () => import("./js/pages/adminAgents.js"), adminOnly: true },
  { path: "/admin/usage", title: "使用量与 Token", loader: () => import("./js/pages/adminUsage.js"), adminOnly: true },
  { path: "/admin/logs", title: "运行日志", loader: () => import("./js/pages/adminLogs.js"), adminOnly: true },
  { path: "/admin/database", title: "数据库调试", loader: () => import("./js/pages/adminDatabase.js"), adminOnly: true },
  { path: "/admin/audit", title: "审计日志", loader: () => import("./js/pages/adminAudit.js"), adminOnly: true },
  { path: "/admin/settings", title: "功能与常量", loader: () => import("./js/pages/adminSettings.js"), adminOnly: true },
  { path: "/account", title: "账户设置", loader: () => import("./js/pages/account.js") },
  { path: "/welcome", title: "创建班级", loader: () => import("./js/pages/welcome.js"), bare: true },
  { path: "/onboarding", title: "班级创建向导", loader: () => import("./js/pages/onboarding.js") },
  { path: "/onboarding/:id", title: "班级创建向导", loader: () => import("./js/pages/onboarding.js") },
];
defineRoutes(routes);

let shell = null; // { content, navMap, topTitle, topSub, classChip, statusDot, sidebar }
let loginNotice = null;

/* ---------------- 登录页 ---------------- */

function renderLogin(notice = null) {
  shell = null;
  const userInput = el("input", { type: "text", autocomplete: "username", required: true, maxlength: "100" });
  const passInput = el("input", { type: "password", autocomplete: "current-password", required: true, maxlength: "128" });
  const errorBox = el("div", { class: "error-panel hidden", role: "alert" });
  const submit = el("button", { class: "primary", type: "submit" }, "登录");
  const form = el("form", { novalidate: false },
    field("用户名", userInput),
    field("密码", passInput),
    errorBox,
    submit,
  );
  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    errorBox.classList.add("hidden");
    submit.disabled = true;
    submit.textContent = "登录中…";
    try {
      await login(userInput.value.trim(), passInput.value);
      toast("登录成功", "success");
      await renderApp();
    } catch (error) {
      clear(errorBox);
      errorBox.append(el("b", {}, error.message));
      if (error.requestId) errorBox.append(el("div", { class: "error-meta" }, el("span", { class: "tag tag-id" }, `问题编号：${error.requestId}`)));
      errorBox.classList.remove("hidden");
    } finally {
      submit.disabled = false;
      submit.textContent = "登录";
      passInput.value = "";
    }
  });
  clear(root);
  root.append(el("div", { class: "login-page" },
    el("div", { class: "login-card" },
      el("div", { class: "brand-line" },
        el("span", { class: "brand-mark" }, "C"),
        el("div", {}, el("h2", { style: { margin: "0" } }, "ClassClaw"), el("span", { class: "muted" }, "班主任工作台"))),
      el("p", { class: "muted" }, "班级管理、座位课表、值日作业考勤与班级专属智能体。"),
      notice ? el("p", { class: "login-hint" }, notice) : null,
      form,
      el("p", { class: "login-hint" }, "班主任账号由管理员创建，系统不提供自行注册。"))));
}

/* ---------------- AppShell ---------------- */

function buildShell() {
  const isAdmin = state.user?.role === "admin";
  const sidebar = el("aside", { class: "sidebar", id: "sidebar" });
  sidebar.append(el("div", { class: "brand-line" },
    el("span", { class: "brand-mark" }, "C"),
    el("div", {}, el("b", {}, isAdmin ? "ClassClaw Control" : "ClassClaw"), el("span", { class: "muted", style: { fontSize: "12px" } }, isAdmin ? "ADMIN CONSOLE" : "班主任工作台"))));
  const navMap = new Map();
  for (const group of (isAdmin ? ADMIN_NAV : TEACHER_NAV)) {
    const box = el("div", { class: "nav-group" }, el("div", { class: "nav-group-title" }, group.group));
    for (const item of group.items) {
      const btn = el("button", { class: "nav-item", type: "button" }, item.label);
      btn.addEventListener("click", () => { navigate(item.path); closeSidebar(); });
      navMap.set(item.path, btn);
      box.append(btn);
    }
    sidebar.append(box);
  }

  const hamburger = el("button", { class: "hamburger", type: "button", "aria-label": "打开导航" }, "菜单");
  hamburger.addEventListener("click", openSidebar);
  const topTitle = el("div", {}, el("div", { class: "page-title" }, "…"), el("div", { class: "page-sub" }));
  const classChip = el("span", { class: "class-chip hidden" });
  const statusDot = isAdmin ? el("span", { class: "status-dot", title: "智能服务状态" }) : null;
  const statusBtn = isAdmin ? el("button", { class: "text-button", type: "button", style: { display: "flex", alignItems: "center", gap: "6px" } }, statusDot, "智能服务") : null;
  statusBtn?.addEventListener("click", () => navigate("/admin/openclaw"));
  const today = el("span", { class: "today" }, todayStr());

  const menuBtn = el("button", { class: "user-menu-btn", type: "button", "aria-haspopup": "menu" },
    el("span", { class: "avatar" }, state.user?.role === "admin" ? "管" : (state.user?.display_name || state.user?.username || "?").slice(0, 1)),
    el("span", {}, state.user?.display_name || state.user?.username || ""),
    el("span", { class: "muted", style: { fontSize: "12px" } }, state.user?.role === "admin" ? "管理员" : "班主任"));
  const menuBox = el("div", { class: "user-menu" }, menuBtn);
  menuBtn.addEventListener("click", () => {
    if (menuBox.querySelector(".user-menu-pop")) { menuBox.querySelector(".user-menu-pop").remove(); return; }
    const pop = el("div", { class: "user-menu-pop", role: "menu" },
      el("button", { type: "button", onclick: () => { navigate("/account"); pop.remove(); } }, "账户设置"),
      el("button", { type: "button", onclick: async () => { pop.remove(); await logout(); renderLogin("已退出登录"); } }, "退出登录"));
    menuBox.append(pop);
    const dismiss = (e) => { if (!menuBox.contains(e.target)) { pop.remove(); document.removeEventListener("mousedown", dismiss); } };
    document.addEventListener("mousedown", dismiss);
  });

  const topbar = el("header", { class: "topbar" },
    hamburger, topTitle, classChip, el("span", { class: "spacer" }),
    statusBtn, today, menuBox);

  const content = el("main", { class: "content", id: "content" });
  const main = el("div", { class: "main" }, topbar, content);
  clear(root);
  root.append(el("div", { class: `shell${isAdmin ? " admin-shell" : ""}` }, sidebar, main));
  shell = { content, navMap, topTitle, topSub: topTitle.querySelector(".page-sub"), classChip, statusDot, sidebar };
  if (isAdmin) refreshOpenclawDot();
}

function openSidebar() {
  shell.sidebar.classList.add("open");
  const backdrop = el("div", { class: "sidebar-backdrop" });
  backdrop.addEventListener("click", closeSidebar);
  document.body.append(backdrop);
  shell.backdrop = backdrop;
  const onKey = (e) => { if (e.key === "Escape") { closeSidebar(); document.removeEventListener("keydown", onKey); } };
  document.addEventListener("keydown", onKey);
}
function closeSidebar() {
  shell?.sidebar.classList.remove("open");
  shell?.backdrop?.remove();
}

async function refreshOpenclawDot() {
  if (!shell?.statusDot) return;
  try {
    const status = state.openclaw?.ready !== undefined && !state.openclawDirty ? state.openclaw : await refreshOpenclaw();
    shell.statusDot.className = `status-dot ${status.ready ? "ok" : "bad"}`;
    shell.statusDot.title = status.ready ? "OpenClaw 已连接" : `OpenClaw 未就绪：${status.error || ""}`;
  } catch {
    shell.statusDot.className = "status-dot bad";
    shell.statusDot.title = "OpenClaw 状态检查失败";
  }
}

function updateClassChip() {
  if (!shell) return;
  if (state.classInfo?.name) {
    shell.classChip.textContent = state.classInfo.name;
    shell.classChip.classList.remove("hidden");
  } else if (state.classId) {
    shell.classChip.textContent = "当前班级";
    shell.classChip.classList.remove("hidden");
  } else {
    shell.classChip.classList.add("hidden");
  }
}

/* ---------------- 路由守卫与页面挂载 ---------------- */

setRouteResolver(async (route, ctx) => {
  if (!state.user) { renderLogin("请先登录"); return; }
  const isAdmin = state.user.role === "admin";
  if (route.adminOnly && !isAdmin) {
    mountErrorState("权限不足", "该页面仅管理员可以访问。", "403");
    return;
  }
  if (isAdmin && !route.adminOnly && route.path !== "/account") {
    navigate("/admin/overview");
    return;
  }
  // 无班级班主任：只能停留在 welcome / onboarding / account / agent / workflow
  if (!isAdmin && !state.classId && !["/welcome", "/onboarding", "/account", "/agent", "/workflow"].some((p) => ctx.path === p || ctx.path.startsWith(`${p}/`))) {
    navigate("/welcome");
    return;
  }
  if (route.requiresClass && !state.classId && !isAdmin) { navigate("/welcome"); return; }
  // 无班级管理员访问需要班级的页面：给出班级选择提示
  if (route.requiresClass && !state.classId && isAdmin) {
    mountErrorState("管理员没有绑定班级", "请以班主任账号登录，或在用户管理中为账号分配历史班级后由班主任查看。", null);
    return;
  }

  const meta = PAGE_META[ctx.path] || {};
  shell.topTitle.firstChild.textContent = route.title;
  shell.topSub.textContent = meta.label ? `${meta.group || ""}` : "";
  for (const [path, btn] of shell.navMap) btn.classList.toggle("active", path === ctx.path);
  updateClassChip();

  clear(shell.content);
  const mount = el("div", { class: "page-mount" });
  shell.content.append(mount);
  try {
    const mod = await route.loader();
    await mod.render(mount, ctx, { refreshShell: renderApp, refreshOpenclawDot });
  } catch (error) {
    console.error(error);
    clear(mount);
    mount.append(el("div", { class: "error-panel" }, el("b", {}, `页面加载失败：${error.message}`)));
  }
});

function mountErrorState(title, hint, code) {
  clear(shell.content);
  shell.content.append(el("div", { class: "card" },
    el("h3", {}, title), el("p", { class: "muted" }, hint),
    code ? el("span", { class: "tag tag-warn" }, `HTTP ${code}`) : null));
}

async function renderApp() {
  buildShell();
  if (state.classId) {
    refreshClassInfo().then(updateClassChip).catch(() => {});
  }
  const { path } = parseHash();
  const known = routes.some((r) => r.path === path || (r.path.includes(":") && path.startsWith(r.path.split(":")[0])));
  if (!known || path === "/") {
    navigate(state.user.role === "admin" ? "/admin/overview" : state.classId ? "/dashboard" : "/welcome");
    return;
  }
  await dispatch();
}

/* ---------------- 启动 ---------------- */

onUnauthorized(() => {
  const wasLoggedIn = !!state.user;
  clearSession();
  renderLogin(wasLoggedIn ? "登录已过期，请重新登录。" : null);
});

startRouter();

(async function boot() {
  try {
    const me = await restoreSession();
    if (me) {
      await renderApp();
    } else {
      renderLogin();
    }
  } catch (error) {
    renderLogin(`服务暂不可用：${error.message}`);
  }
})();
