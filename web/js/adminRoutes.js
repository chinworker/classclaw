export const ADMIN_ALIASES = {
  "/admin/openclaw/settings": ["/admin/settings", { domain: "openclaw" }],
  "/admin/openclaw": ["/admin/settings", { domain: "openclaw", section: "gateway" }],
  "/admin/openclaw/system-agents": ["/admin/settings", { domain: "openclaw", section: "system-agents" }],
  "/admin/openclaw/maintenance": ["/admin/ops", { tab: "logs", source: "openclaw" }],
  "/admin/agents": ["/admin/access", { focus: "agents" }],
  "/admin/usage": ["/admin/ops", { tab: "usage" }],
  "/admin/logs": ["/admin/ops", { tab: "logs" }],
  "/admin/database": ["/admin/ops", { tab: "database" }],
  "/admin/audit": ["/admin/ops", { tab: "audit" }],
  "/admin/maintenance": ["/admin/ops", { tab: "maintenance" }],
  "/admin/users": ["/admin/access", {}],
  "/admin/classes": ["/admin/access", {}],
};

export const ADMIN_NAV = [{ group: "控制台", items: [
  { path: "/admin/overview", label: "概览" },
  { path: "/admin/settings", label: "设置中心" },
  { path: "/admin/access", label: "班级与账号" },
  { path: "/admin/ops", label: "运维中心" },
] }];
