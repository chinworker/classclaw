import { el, clear } from "../util.js";
import { api } from "../api.js";
import { pageHeader, errorPanel, emptyState, skeleton, statusBadge, field } from "../components.js";

export async function render(mount, ctx = {}) {
  const category = ctx.path?.startsWith("/admin/openclaw") ? "openclaw" : "classclaw";
  const isOpenClaw = category === "openclaw";
  const host = el("div");
  mount.append(pageHeader(isOpenClaw ? "OpenClaw 连接与微信配置" : "ClassClaw 系统配置",
    "显示当前进程实际生效的启动配置。停止服务、修改配置文件或环境变量、校验后重启 ClassClaw。",
    el("button", { class: "secondary", type: "button", onclick: load }, "刷新生效值")), host);

  async function load() {
    clear(host); host.append(skeleton(6));
    try {
      const catalog = await api("/admin/settings/catalog");
      clear(host);
      const sections = catalog.sections.filter((item) => item.category === category);
      const rows = catalog.items.filter((item) => item.category === category);
      const credentials = catalog.credentials.filter((item) => item.category === category);
      const search = el("input", { type: "search", placeholder: "搜索名称、TOML 字段或环境变量", "aria-label": "搜索配置" });
      const section = el("select", { "aria-label": "配置分组" }, el("option", { value: "" }, "全部分组"),
        sections.map((item) => el("option", { value: item.id }, item.label)), el("option", { value: "credentials" }, "凭据与安全"));
      const count = el("span", { class: "muted", role: "status" });
      const results = el("div", { class: "config-sections" });
      host.append(
        el("div", { class: "card config-file-guide" },
          el("div", { class: "row-gap admin-section-title" }, el("h3", {}, "配置文件与生效方式"), statusBadge("pending", "修改后重启 ClassClaw")),
          el("p", {}, catalog.precedence),
          el("p", {}, "当前 TOML：", el("code", {}, catalog.config_file || "未加载配置文件（使用环境变量 / 程序默认值）")),
          el("p", { class: "muted" }, "默认文件：", el("code", {}, catalog.default_config_file), "；外置配置通过 ", el("code", {}, catalog.config_file_env_var), " 指定。"),
          el("details", {}, el("summary", {}, "查看修改步骤与版本"),
            el("ol", {},
              el("li", {}, "停止 ClassClaw；涉及数据路径时先备份，路径修改不会搬迁数据。"),
              el("li", {}, "未有配置文件时复制 config/classclaw.example.toml 为 config/classclaw.toml；密钥放在服务器 .env 或环境变量。"),
              el("li", {}, "修改对应 TOML 字段；如有同名环境变量覆盖，也需同步修改或移除覆盖。"),
              el("li", {}, "在项目目录执行 ", el("code", {}, "python scripts/check_config.py"), "；外置文件应保持 CLASSCLAW_CONFIG_FILE 与服务一致。"),
              el("li", {}, "校验通过后重启 ClassClaw，再刷新页面核对生效值。")),
            el("p", { class: "muted" }, "TOML 相对路径以配置文件目录为基准；环境变量相对路径以项目根目录为基准。页面展示解析后的路径。"),
            el("p", {}, "启动配置版本：", el("code", {}, catalog.config_hash))),
          el("div", { class: "row-gap" },
            el("a", { href: isOpenClaw ? "#/admin/openclaw" : "#/admin/openclaw/settings" }, isOpenClaw ? "Gateway 网页配置" : "OpenClaw 连接配置"),
            el("a", { href: "#/admin/agents" }, "各班级 Agent 配置"),
            el("a", { href: isOpenClaw ? "#/admin/openclaw/system-agents" : "#/admin/maintenance" }, isOpenClaw ? "Main / 提取 Agent" : "备份与系统维护"))),
        el("div", { class: "card config-filter" }, field("搜索配置", search), field("配置分组", section), count), results);

      function filter() {
        clear(results);
        const query = search.value.trim().toLowerCase();
        const matches = (item) => `${item.label} ${item.description} ${item.config_path || ""} ${item.env_var}`.toLowerCase().includes(query);
        let total = 0;
        for (const group of sections) {
          if (section.value && section.value !== group.id) continue;
          const items = rows.filter((item) => item.section === group.id && matches(item));
          if (!items.length) continue;
          total += items.length;
          results.append(el("section", { class: "card" },
            el("h3", {}, group.label), el("p", { class: "muted" }, group.description),
            el("div", { class: "settings-list" }, items.map(settingRow))));
        }
        const secrets = !section.value || section.value === "credentials" ? credentials.filter(matches) : [];
        if (secrets.length) {
          total += secrets.length;
          results.append(el("section", { class: "card" }, el("h3", {}, "凭据与安全"),
            secrets.map((item) => el("div", { class: "setting-row config-setting-row" },
              el("div", {}, el("b", {}, item.label), el("p", { class: "muted" }, item.description), el("code", {}, item.env_var)),
              statusBadge(item.configured ? "active" : "pending", item.configured ? "已设置" : "未设置 / 内置默认")))));
        }
        count.textContent = `${total} / ${rows.length + credentials.length} 项`;
        if (!total) results.append(emptyState("没有匹配的配置", "调整分组或搜索 TOML 字段、环境变量名称。"));
      }
      search.addEventListener("input", filter); section.addEventListener("change", filter); filter();
    } catch (error) { clear(host); host.append(errorPanel(error, { onRetry: load })); }
  }
  await load();
}

function settingRow(item) {
  const source = item.value_source.startsWith("environment:")
    ? `环境变量 ${item.value_source.slice("environment:".length)}（覆盖 TOML）`
    : ({ file: "TOML 配置文件", default: "程序默认值", unknown: "启动配置" }[item.value_source] || "启动配置");
  return el("div", { class: "setting-row config-setting-row" },
    el("div", {}, el("div", { class: "row-gap" }, el("b", {}, item.label), el("code", {}, item.config_path)),
      el("p", { class: "muted" }, item.description),
      el("div", { class: "config-field-meta" }, el("code", {}, item.env_var), el("small", {}, `当前来源：${source}`))),
    el("div", { class: "config-setting-value" }, item.type === "boolean"
      ? statusBadge(item.value ? "active" : "inactive", item.value ? "已开启" : "已关闭")
      : el("code", {}, String(item.value))));
}
