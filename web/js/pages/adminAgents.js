import { el, clear, toast } from "../util.js";
import { api } from "../api.js";
import { pageHeader, statusBadge, errorPanel, skeleton, emptyState, qrBindingPanel, openDrawer, openModal, field, confirmDanger } from "../components.js";
import { openAgentModelSettings } from "../agentModelSettings.js";
import { appConfig } from "../config.js";

export async function render(mount, ctx = {}) {
  const scope = ctx.path === "/admin/agents" ? "class" : ctx.path === "/admin/openclaw/system-agents" ? "system" : "gateway";
  const globalHost = el("div", { class: "card" });
  const agentsHost = el("div");
  const titles = { gateway: "Gateway 全局配置", system: "系统 Agent · Main / 数据提取", class: "各班级 Agent 配置" };
  const descriptions = {
    gateway: "作用于整个 OpenClaw Gateway；保存网页运行参数会请求重启 Gateway，所有班级 AI 服务会短暂受影响。",
    system: "Main 与数据提取 Agent 分别维护模型、运行参数和提示词；班级专属配置在各班级 Agent 页面管理。",
    class: "按班级维护主模型、图片理解、语音识别、运行参数、提示词与微信绑定。模型和文件设置独立于其他班级。",
  };
  mount.append(pageHeader(titles[scope], descriptions[scope],
    el("button", { class: "secondary", type: "button", onclick: load }, "刷新")), scope === "gateway" ? globalHost : agentsHost);

  async function load() {
    clear(globalHost); clear(agentsHost);
    const host = scope === "gateway" ? globalHost : agentsHost;
    host.append(skeleton(5));
    try {
      const data = await api(scope === "gateway" ? "/admin/openclaw/config" : "/admin/openclaw/agents/catalog");
      clear(host);
      if (scope === "gateway") renderGlobal(data);
      else renderAgents(data.filter((agent) => scope === "class" ? agent.kind === "class" : agent.kind !== "class"));
    } catch (error) { clear(host); host.append(errorPanel(error, { onRetry: load })); }
  }

  function renderGlobal(config) {
    const gateway = config.gateway; const runtime = config.runtime;
    const responses = el("input", { type: "checkbox", checked: runtime.responses_enabled });
    const plugin = el("input", { type: "checkbox", checked: runtime.classclaw_plugin_enabled });
    const dm = el("select", {}, ["main", "per-peer", "per-channel-peer", "per-account-channel-peer"].map((value) => el("option", { value, selected: runtime.dm_scope === value }, value)));
    globalHost.append(
      el("div", { class: "row-gap admin-section-title" }, el("h3", {}, "Gateway / 全局配置"), statusBadge(runtime.classclaw_plugin_enabled ? "active" : "inactive", runtime.classclaw_plugin_enabled ? "PLUGIN ENABLED" : "PLUGIN DISABLED")),
      el("div", { class: "admin-config-grid" },
        kv("Gateway URL", gateway.url), kv("Gateway Token", gateway.token_configured ? "已配置（不显示内容）" : "未配置"),
        kv("Default Agent", gateway.default_agent_id), kv("WeChat Channel", gateway.wechat_channel),
        kv("Workspace Root", gateway.workspace_root), kv("Timeout", `${gateway.timeout_seconds}s`),
        kv("Config Hash", runtime.config_hash || "—"), kv("WeChat Plugin", runtime.wechat_plugin_enabled ? "enabled" : "disabled")),
      el("p", {}, el("a", { href: "#/admin/openclaw/settings" }, "连接地址、超时、工作区与微信参数：查看启动配置")),
      el("h3", {}, "网页运行参数"),
      el("div", { class: "setting-row" }, el("div", {}, el("b", {}, "Responses API"), el("p", { class: "muted" }, "网页 Agent 对话、文件解析和自然语言分类依赖此端点。")), el("div", { class: "setting-control boolean" }, responses)),
      el("div", { class: "setting-row" }, el("div", {}, el("b", {}, "ClassClaw Plugin"), el("p", { class: "muted" }, "关闭后 OpenClaw 无法调用班级工具。")), el("div", { class: "setting-control boolean" }, plugin)),
      el("div", { class: "setting-row" }, el("div", {}, el("b", {}, "私聊会话隔离"), el("code", {}, "session.dmScope"), el("p", { class: "muted" }, "per-account-channel-peer 按账号、通道和联系人隔离；改为 main 会共享会话，应核对班级隐私边界。")), el("div", { class: "setting-control" }, dm)),
      el("div", { class: "row-gap", style: { justifyContent: "flex-end", marginTop: "12px" } }, el("button", { class: "primary", type: "button", onclick: async (event) => {
        const button = event.currentTarget;
        button.disabled = true;
        try {
          await api("/admin/openclaw/config", { method: "PATCH", body: { responses_enabled: responses.checked, classclaw_plugin_enabled: plugin.checked, dm_scope: dm.value } });
          toast("OpenClaw 全局配置已提交，Gateway 将重启", "success"); refreshAfterRestart(1600);
        } catch (error) { toast(error.message, "error"); } finally { button.disabled = false; }
      } }, "应用并重启 Gateway")),
      el("section", { class: "config-file-guide" }, el("h3", {}, "模型服务商与全局默认模型"),
        el("div", { class: "admin-config-grid compact" },
          kv("全局主模型", config.file_config?.default_model || "未配置"),
          kv("全局图片模型", config.file_config?.default_image_model || "未单独配置"),
          kv("已定义 Provider", config.file_config?.providers?.join(", ") || "无显式配置"),
          kv("Gateway 配置文件", config.file_config?.path || "请在 OpenClaw 服务端确认配置文件位置")),
        el("p", { class: "muted" }, "Provider 地址、模型目录与凭据通过 OpenClaw 自身配置维护，不写入 ClassClaw TOML。先停止 Gateway，修改其配置文件，按所装版本校验并重启；凭据不会通过此页面读取。"),
        el("p", { class: "muted" }, "默认模型路径：agents.defaults.model / agents.defaults.imageModel；Provider 配置：models.providers。各班级图片与语音模型通过班级 Agent 页面保存。"),
        el("div", { class: "row-gap" }, el("a", { href: "#/admin/openclaw/system-agents" }, "系统 Agent 配置"), el("a", { href: "#/admin/agents" }, "班级 Agent 配置"))),
    );
  }

  function refreshAfterRestart(delay = 1400) { window.setTimeout(() => { if (mount.isConnected) load(); }, delay); }

  function kv(label, value) { return el("div", { class: "admin-kv" }, el("span", {}, label), el("code", { title: String(value ?? "") }, value ?? "—")); }

  function renderAgents(rows) {
    if (!rows.length) { agentsHost.append(emptyState("尚无可管理智能体", "班级由班主任通过创建向导建立，系统 Agent 请检查 OpenClaw 配置。")); return; }
    if (scope === "system") {
      agentsHost.append(el("div", { class: "card" }, el("p", {}, "Main 用于系统任务和提取回退；数据提取 Agent 专用于结构化解析，不开放班级工具。"),
        el("a", { href: "#/admin/openclaw/settings" }, "设置提取 Agent 开关与 ID")), el("div", { class: "admin-agent-grid" }, rows.map(agentCard)));
      return;
    }
    const search = el("input", { type: "search", value: ctx.query?.class_id || "", placeholder: "搜索班级、负责人或 Agent ID", "aria-label": "搜索班级 Agent" });
    const status = el("select", { "aria-label": "Agent 状态" }, el("option", { value: "" }, "全部状态"),
      el("option", { value: "available" }, "Agent 已创建"), el("option", { value: "missing" }, "未创建 / 不可用"), el("option", { value: "linked" }, "微信已绑定"));
    const grid = el("div", { class: "admin-agent-grid" });
    const count = el("span", { class: "muted", role: "status" });
    agentsHost.append(el("div", { class: "card config-filter" }, field("查找班级", search), field("状态", status), count), grid);
    const filter = () => {
      const query = search.value.trim().toLowerCase();
      const selected = rows.filter((agent) =>
        `${agent.label} ${agent.identifier} ${agent.agent_id || ""} ${agent.owner?.username || ""} ${agent.owner?.display_name || ""}`.toLowerCase().includes(query)
        && (!status.value || (status.value === "available" ? agent.present : status.value === "missing" ? !agent.present : !!agent.binding?.channel_account_id)));
      clear(grid); count.textContent = `${selected.length} / ${rows.length} 个班级`;
      grid.append(...(selected.length ? selected.map(agentCard) : [emptyState("没有匹配的班级", "调整搜索内容或状态筛选。")]));
    };
    search.addEventListener("input", filter); status.addEventListener("change", filter); filter();
  }

  function agentCard(agent) {
    const binding = agent.binding; const cls = agent.class; const isClass = agent.kind === "class";
    const ownerLabel = isClass ? (agent.owner?.username || "未分配班主任") : agent.description;
    return el("div", { class: "card admin-agent-card" },
      el("div", { class: "row-gap", style: { justifyContent: "space-between" } }, el("div", {}, el("b", {}, agent.label), el("div", { class: "muted" }, ownerLabel)), statusBadge(agent.status)),
      el("div", { class: "admin-config-grid compact" },
        kv("类型", agent.kind.toUpperCase()), kv("Agent ID", agent.agent_id || "NOT CREATED"),
        kv("Workspace", agent.workspace_path || "NOT CONFIGURED"),
        isClass ? kv("微信账号", binding?.channel_account_id || "未绑定") : kv("启用状态", agent.enabled ? "已启用" : "已关闭"),
        kv("主模型", `${agent.model_summary?.main_model || "未配置"}${agent.model_summary?.inherits_main ? "（继承全局）" : ""}`),
        isClass ? kv("图片理解", agent.model_summary?.image_model || "跟随主模型 / 全局图片模型") : null,
        isClass ? kv("语音识别", agent.model_summary?.speech_model || "浏览器语音识别") : null),
      binding?.last_error ? el("pre", { class: "admin-error-log" }, binding.last_error) : null,
      el("div", { class: "row-gap" },
        el("button", { class: "secondary", type: "button", disabled: !agent.present, onclick: () => editAgent(agent) }, "运行参数"),
        el("button", { class: "secondary", type: "button", disabled: !agent.present, onclick: () => editAgent(agent, "files") }, "提示词与身份"),
        isClass ? el("button", { class: "secondary", type: "button", disabled: !agent.present, onclick: () => openAgentModelSettings(cls.id, {
          onSaved: (result) => refreshAfterRestart(result.restart_requested ? 1600 : 0),
        }) }, "主 / 图片 / 语音模型") : null,
        isClass ? el("button", { class: "secondary", type: "button", disabled: !appConfig.features.wechat_binding, onclick: () => openQr(cls.id) }, appConfig.features.wechat_binding ? "微信绑定" : "微信绑定已关闭") : null,
        el("button", { class: "text-button", type: "button", onclick: () => openDrawer({ title: `运行详情 · ${agent.label}`, body: el("div", {}, kv("Workspace", agent.workspace_path), kv("Identifier", agent.identifier), isClass ? kv("Binding ID", binding?.id) : null) }) }, "运行详情")));
  }

  async function editAgent(agent, initialTab = "runtime") {
    if (!agent.agent_id || !agent.present) { toast("OpenClaw 中尚未创建该智能体", "error"); return; }
    let settings;
    try { settings = await api(`/admin/openclaw/agents/${encodeURIComponent(agent.identifier)}/settings`); }
    catch (error) { toast(error.message, "error"); return; }
    const runtimePane = el("div", { class: "agent-config-pane" });
    const filesPane = el("div", { class: "agent-config-pane hidden" });
    const runtimeTab = el("button", { class: "active", type: "button" }, "运行参数");
    const filesTab = el("button", { type: "button" }, "提示词与身份文件");
    const setTab = (runtime) => {
      runtimePane.classList.toggle("hidden", !runtime); filesPane.classList.toggle("hidden", runtime);
      runtimeTab.classList.toggle("active", runtime); filesTab.classList.toggle("active", !runtime);
    };
    runtimeTab.onclick = () => setTab(true); filesTab.onclick = () => setTab(false);
    buildRuntimePane(runtimePane, settings, agent.identifier);
    buildFilesPane(filesPane, settings.workspace, agent.identifier, agent.kind);
    setTab(initialTab === "runtime");
    openModal({
      title: `Agent 配置 · ${agent.label}`,
      body: el("div", {},
        el("div", { class: "agent-config-summary" }, kv("Agent ID", agent.agent_id), kv("Workspace", settings.workspace.workspace || "NOT CONFIGURED"), kv("Config Hash", settings.config_hash || "—")),
        el("div", { class: "tabs agent-config-tabs" }, runtimeTab, filesTab), runtimePane, filesPane),
      wide: true, actions: [{ label: "关闭", kind: "secondary" }],
    });
  }

  function optionSelect(values, current, inheritLabel = "继承全局") {
    const select = el("select", {}, el("option", { value: "", selected: current === null || current === undefined || current === "" }, inheritLabel),
      values.map((value) => el("option", { value, selected: String(current) === String(value) }, String(value))));
    if (current !== null && current !== undefined && current !== "" && !values.some((value) => String(value) === String(current))) {
      select.append(el("option", { value: current, selected: true }, `${current}（当前值）`));
    }
    return select;
  }

  function buildRuntimePane(pane, settings, identifier) {
    const current = settings.runtime; const models = settings.models.filter((item) => item.available !== false);
    const name = el("input", { value: current.display_name || "", maxlength: "100" });
    const providerValues = [...new Set(models.map((item) => item.provider).filter(Boolean))].sort();
    const provider = optionSelect(providerValues, current.provider, "全部 Provider");
    const model = el("select", {}, el("option", { value: "", selected: !current.model }, "继承全局模型"));
    const fillModels = (selected) => {
      [...model.options].slice(1).forEach((item) => item.remove());
      for (const item of models.filter((entry) => !provider.value || entry.provider === provider.value)) {
        model.append(el("option", { value: item.id, selected: item.id === selected }, `${item.name || item.id} · ${item.provider}`));
      }
      if (selected && ![...model.options].some((item) => item.value === selected)) {
        model.append(el("option", { value: selected }, `${selected}（保留当前模型）`));
      }
      model.value = selected;
    };
    fillModels(current.model || ""); provider.addEventListener("change", () => fillModels(model.value));
    const fallbacks = el("input", { value: (current.model_fallbacks || []).join(", "), placeholder: "provider/model, provider/model" });
    const utility = el("select", {}, el("option", { value: "", selected: !current.utility_model }, "继承主模型"), models.map((item) => el("option", { value: item.id, selected: item.id === current.utility_model }, `${item.name || item.id} · ${item.provider}`)));
    if (current.utility_model && !models.some((item) => item.id === current.utility_model)) {
      utility.append(el("option", { value: current.utility_model, selected: true }, `${current.utility_model}（当前模型不可用）`));
    }
    const thinking = optionSelect(["off", "minimal", "low", "medium", "high", "xhigh", "adaptive", "max"], current.thinking_default);
    if (settings.thinking_configuration?.managed) {
      thinking.value = settings.thinking_configuration.default;
      thinking.disabled = true;
      thinking.title = "由 openclaw.class_agent_thinking 配置管理；会话覆盖请在对话页设置。";
    }
    const reasoning = optionSelect(["off", "on", "stream"], current.reasoning_default);
    const verbose = optionSelect(["off", "on", "full"], current.verbose_default);
    const fast = optionSelect(["auto", "true", "false"], current.fast_mode_default === true ? "true" : current.fast_mode_default === false ? "false" : current.fast_mode_default);
    const injection = optionSelect(["always", "continuation-skip", "never"], current.context_injection);
    const bootstrap = el("input", { type: "number", min: "1000", max: "100000", value: current.bootstrap_max_chars || "" });
    const bootstrapTotal = el("input", { type: "number", min: "1000", max: "500000", value: current.bootstrap_total_max_chars || "" });
    const skillBudget = el("input", { type: "number", min: "1000", max: "50000", value: current.max_skills_prompt_chars || "" });
    const memory = optionSelect(["true", "false"], current.memory_search_enabled === true ? "true" : current.memory_search_enabled === false ? "false" : null);
    const temperature = el("input", { type: "number", step: "0.1", value: current.model_params?.temperature ?? "", placeholder: "inherit" });
    const topP = el("input", { type: "number", step: "0.05", value: current.model_params?.topP ?? "", placeholder: "inherit" });
    const maxTokens = el("input", { type: "number", step: "1", value: current.model_params?.maxTokens ?? "", placeholder: "inherit" });
    const cacheRetention = el("input", { value: current.model_params?.cacheRetention ?? "", placeholder: "inherit / none" });
    const save = el("button", { class: "primary", type: "button" }, "保存运行参数");
    const readValues = () => {
      const numberOrNull = (input) => input.value === "" ? null : Number(input.value);
      const params = {};
      if (temperature.value !== "") params.temperature = Number(temperature.value);
      if (topP.value !== "") params.topP = Number(topP.value);
      if (maxTokens.value !== "") params.maxTokens = Number(maxTokens.value);
      if (cacheRetention.value.trim()) params.cacheRetention = cacheRetention.value.trim();
      return {
        display_name: name.value.trim() || current.display_name,
        model: model.value || null,
        model_fallbacks: fallbacks.value.split(",").map((item) => item.trim()).filter(Boolean),
        utility_model: utility.value || null,
        thinking_default: thinking.value || null, reasoning_default: reasoning.value || null,
        verbose_default: verbose.value || null,
        fast_mode_default: fast.value === "" ? null : fast.value === "auto" ? "auto" : fast.value === "true",
        context_injection: injection.value || null,
        bootstrap_max_chars: numberOrNull(bootstrap), bootstrap_total_max_chars: numberOrNull(bootstrapTotal),
        max_skills_prompt_chars: numberOrNull(skillBudget),
        memory_search_enabled: memory.value === "" ? null : memory.value === "true",
        model_params: Object.keys(params).length ? params : null,
      };
    };
    const initialValues = readValues();
    save.onclick = async () => {
      for (const input of pane.querySelectorAll("input")) { if (!input.reportValidity()) return; }
      const body = Object.fromEntries(Object.entries(readValues()).filter(([key, value]) => JSON.stringify(value) !== JSON.stringify(initialValues[key])));
      if (!Object.keys(body).length) { toast("配置未改变"); return; }
      save.disabled = true;
      try {
        const result = await api(`/admin/openclaw/agents/${encodeURIComponent(identifier)}`, { method: "PATCH", body });
        Object.assign(initialValues, body);
        toast(result.restart_requested ? "运行参数已提交，Gateway 将重启" : "运行参数已保存", "success");
        refreshAfterRestart(result.restart_requested ? 1400 : 0);
      }
      catch (error) { toast(error.message, "error"); } finally { save.disabled = false; }
    };
    const group = (title, ...fields) => el("fieldset", { class: "agent-runtime-section" }, el("legend", {}, title), el("div", { class: "form-grid agent-runtime-grid" }, fields));
    pane.append(
      el("p", { class: "muted" }, "留空表示继承默认值；修改运行参数会请求重启整个 Gateway，各班级 AI 服务会短暂受影响。提示词文件在后续对话轮次生效。"),
      el("div", { class: "admin-config-grid compact" }, kv("Skills", (current.skills || []).join(", ") || "inherit"), kv("Tool profile", current.tools?.profile || "inherit")),
      group("身份与模型", field("显示名", name), field("模型服务商筛选", provider), field("主模型", model), field("回退模型（逗号分隔）", fallbacks), field("辅助模型", utility)),
      group("思考与回复", field("思考强度", thinking), field("推理过程显示", reasoning), field("回复详细程度", verbose), field("快速模式", fast)),
      settings.thinking_configuration?.managed ? el("p", { class: "muted" },
        `班级默认思考强度由 openclaw.class_agent_thinking 配置管理（${settings.thinking_configuration.default}）。单独调整某个会话请前往班级对话页。`) : null,
      el("details", { class: "agent-runtime-advanced" }, el("summary", {}, "高级：上下文、记忆与模型参数"),
        group("上下文与记忆", field("上下文注入策略", injection), field("单文件提示词字符上限", bootstrap), field("提示词总字符上限", bootstrapTotal), field("Skill 提示词字符上限", skillBudget), field("记忆搜索", memory)),
        group("模型参数", field("Temperature", temperature), field("Top P", topP), field("最大输出 Token", maxTokens), field("缓存保留策略", cacheRetention))),
      el("div", { class: "row-gap", style: { justifyContent: "flex-end" } }, save));
  }

  function buildFilesPane(pane, workspace, identifier, kind) {
    const files = workspace.files || [];
    if (!workspace.available || !files.length) {
      pane.append(emptyState("工作区不可用", "请先在 OpenClaw 中为该智能体配置 workspace。"));
      return;
    }
    let current = files[0];
    const select = el("select", {}, files.map((item) => el("option", { value: item.name }, `${item.label} · ${item.name}`)));
    const meta = el("div", { class: "agent-file-meta" });
    const editor = el("textarea", { class: "agent-file-editor", spellcheck: "false" });
    const save = el("button", { class: "primary", type: "button" }, "保存文件");
    const reset = el("button", { class: "danger", type: "button" }, "恢复系统默认");
    const renderFile = () => {
      current = files.find((item) => item.name === select.value) || files[0];
      editor.value = current.content; clear(meta);
      meta.append(el("span", { class: `tag ${current.customized ? "tag-warn" : "tag-muted"}` }, current.customized ? "CUSTOMIZED" : "DEFAULT"), el("code", {}, `${current.characters} chars`), el("code", {}, current.sha256));
      reset.disabled = !current.resettable;
      reset.title = current.resettable ? "恢复 ClassClaw 默认内容" : "该文件没有 ClassClaw 管理的默认内容";
    };
    select.onchange = renderFile;
    save.onclick = async () => {
      save.disabled = true;
      try {
        const result = await api(`/admin/openclaw/agents/${encodeURIComponent(identifier)}/workspace/${encodeURIComponent(current.name)}`, { method: "PUT", body: { content: editor.value, expected_sha256: current.sha256 } });
        current.content = editor.value; current.sha256 = result.sha256; current.characters = result.characters; current.customized = true;
        renderFile(); toast(`${current.name} 已保存`, "success");
      } catch (error) { toast(error.message, "error"); } finally { save.disabled = false; }
    };
    reset.onclick = async () => {
      if (!current.resettable) return;
      const accepted = await confirmDanger({
        title: `恢复 ${current.name}`, lines: ["当前自定义内容会被系统默认内容覆盖。", "此操作会记录到审计日志。"], confirmLabel: "恢复默认",
      });
      if (!accepted) return;
      reset.disabled = true;
      try {
        const result = await api(`/admin/openclaw/agents/${encodeURIComponent(identifier)}/workspace/${encodeURIComponent(current.name)}/reset`, { method: "POST", body: {} });
        Object.assign(current, result); renderFile(); toast(`${current.name} 已恢复默认`, "success");
      } catch (error) { toast(error.message, "error"); } finally { reset.disabled = false; }
    };
    pane.append(el("div", { class: "agent-file-toolbar" }, field("工作区文件", select), meta),
      el("p", { class: "muted" }, kind === "extractor" ? "数据提取智能体的 AGENTS.md 决定结构化解析规则，修改后请验证文件解析结果。" : "AGENTS.md 是核心系统行为提示；SOUL.md 与 IDENTITY.md 控制人格和身份。保存后从后续 Agent turn 生效。"),
      editor, el("div", { class: "row-gap", style: { justifyContent: "space-between", marginTop: "10px" } }, reset, save));
    renderFile();
  }

  function openQr(classId) {
    const panel = qrBindingPanel(classId, { onDone: load });
    openDrawer({ title: "微信绑定", body: el("div", {}, el("p", { class: "muted" }, "重新校验智能体配置并生成微信登录二维码。"), panel.el) });
    panel.start(true);
  }
  await load();
}
