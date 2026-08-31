import { el, clear, toast, fmtDateTime } from "../util.js";
import { api } from "../api.js";
import { pageHeader, statusBadge, errorPanel, skeleton, emptyState, qrBindingPanel, openDrawer, openModal, field, confirmDanger } from "../components.js";

export async function render(mount) {
  const globalHost = el("div", { class: "card" });
  const agentsHost = el("div");
  mount.append(pageHeader("OpenClaw", "管理 Gateway 安全摘要、全局运行参数、班级智能体及微信路由。浏览器永远不会获得 Gateway Token 原文。",
    el("button", { class: "secondary", type: "button", onclick: load }, "刷新")), globalHost, agentsHost);

  async function load() {
    clear(globalHost); clear(agentsHost); globalHost.append(skeleton(4)); agentsHost.append(skeleton(5));
    const [configResult, rowsResult] = await Promise.allSettled([api("/admin/openclaw/config"), api("/admin/agents")]);
    clear(globalHost); clear(agentsHost);
    if (configResult.status === "fulfilled") renderGlobal(configResult.value);
    else globalHost.append(errorPanel(configResult.reason, { onRetry: load }));
    if (rowsResult.status === "fulfilled") renderAgents(rowsResult.value);
    else agentsHost.append(errorPanel(rowsResult.reason, { onRetry: load }));
  }

  function renderGlobal(config) {
    const gateway = config.gateway; const runtime = config.runtime;
    const responses = el("input", { type: "checkbox", checked: runtime.responses_enabled });
    const plugin = el("input", { type: "checkbox", checked: runtime.classclaw_plugin_enabled });
    const dm = el("select", {}, ["main", "per-peer", "per-channel-peer", "per-account-channel-peer"].map((value) => el("option", { value, selected: runtime.dm_scope === value }, value)));
    globalHost.append(
      el("div", { class: "row-gap admin-section-title" }, el("h3", {}, "Gateway / 全局配置"), statusBadge(runtime.classclaw_plugin_enabled ? "active" : "inactive", runtime.classclaw_plugin_enabled ? "PLUGIN ENABLED" : "PLUGIN DISABLED")),
      el("div", { class: "admin-config-grid" },
        kv("Gateway URL", gateway.url), kv("Gateway Token", gateway.token_configured ? gateway.token_masked : "NOT CONFIGURED"),
        kv("Default Agent", gateway.default_agent_id), kv("WeChat Channel", gateway.wechat_channel),
        kv("Workspace Root", gateway.workspace_root), kv("Timeout", `${gateway.timeout_seconds}s`),
        kv("Config Hash", runtime.config_hash || "—"), kv("WeChat Plugin", runtime.wechat_plugin_enabled ? "enabled" : "disabled")),
      el("div", { class: "setting-row" }, el("div", {}, el("b", {}, "Responses API"), el("p", { class: "muted" }, "网页文件解析和自然语言分类依赖此端点。")), el("div", { class: "setting-control boolean" }, responses)),
      el("div", { class: "setting-row" }, el("div", {}, el("b", {}, "ClassClaw Plugin"), el("p", { class: "muted" }, "关闭后 OpenClaw 无法调用班级工具。")), el("div", { class: "setting-control boolean" }, plugin)),
      el("div", { class: "setting-row" }, el("div", {}, el("b", {}, "DM Scope"), el("code", {}, "session.dmScope")), el("div", { class: "setting-control" }, dm)),
      el("div", { class: "row-gap", style: { justifyContent: "flex-end", marginTop: "12px" } }, el("button", { class: "primary", type: "button", onclick: async (event) => {
        event.currentTarget.disabled = true;
        try {
          await api("/admin/openclaw/config", { method: "PATCH", body: { responses_enabled: responses.checked, classclaw_plugin_enabled: plugin.checked, dm_scope: dm.value } });
          toast("OpenClaw 全局配置已提交，Gateway 将重启", "success"); setTimeout(load, 1600);
        } catch (error) { toast(error.message, "error"); } finally { event.currentTarget.disabled = false; }
      } }, "应用并重启 Gateway")),
    );
  }

  function kv(label, value) { return el("div", { class: "admin-kv" }, el("span", {}, label), el("code", {}, value ?? "—")); }

  function renderAgents(rows) {
    agentsHost.append(el("div", { class: "page-header", style: { marginTop: "8px" } }, el("div", {}, el("h2", {}, "班级智能体"), el("p", { class: "page-desc" }, "每个班级独立 workspace、模型参数和微信路由。"))));
    if (!rows.length) { agentsHost.append(emptyState("尚无班级智能体", "在班级管理中新建班级后会生成绑定记录。")); return; }
    agentsHost.append(el("div", { class: "admin-agent-grid" }, rows.map(agentCard)));
  }

  function agentCard({ binding, class: cls, owner }) {
    return el("div", { class: "card admin-agent-card" },
      el("div", { class: "row-gap", style: { justifyContent: "space-between" } }, el("div", {}, el("b", {}, cls?.name || binding.class_id), el("div", { class: "muted" }, owner?.username || "unassigned")), statusBadge(binding.status)),
      el("div", { class: "admin-config-grid compact" }, kv("Agent ID", binding.openclaw_agent_id || "NOT CREATED"), kv("Channel Account", binding.channel_account_id || "NOT BOUND"), kv("Resource Name", binding.agent_name), kv("Updated", fmtDateTime(binding.updated_at))),
      binding.last_error ? el("pre", { class: "admin-error-log" }, binding.last_error) : null,
      el("div", { class: "row-gap" },
        el("button", { class: "secondary", type: "button", onclick: () => editAgent(binding, cls) }, "配置"),
        el("button", { class: "secondary", type: "button", onclick: () => openQr(cls.id) }, "微信二维码"),
        el("button", { class: "text-button", type: "button", onclick: () => openDrawer({ title: `Runtime · ${cls.name}`, body: el("div", {}, kv("Workspace", binding.workspace_path), kv("Class ID", cls.id), kv("Binding ID", binding.id)) }) }, "运行详情")));
  }

  async function editAgent(binding, cls) {
    if (!binding.openclaw_agent_id) { toast("请先在班级管理中创建智能体", "error"); return; }
    let settings;
    try { settings = await api(`/admin/openclaw/agents/${cls.id}/settings`); }
    catch (error) { toast(error.message, "error"); return; }
    const runtimePane = el("div", { class: "agent-config-pane" });
    const filesPane = el("div", { class: "agent-config-pane hidden" });
    const runtimeTab = el("button", { class: "active", type: "button" }, "Runtime");
    const filesTab = el("button", { type: "button" }, "提示词与身份文件");
    const setTab = (runtime) => {
      runtimePane.classList.toggle("hidden", !runtime); filesPane.classList.toggle("hidden", runtime);
      runtimeTab.classList.toggle("active", runtime); filesTab.classList.toggle("active", !runtime);
    };
    runtimeTab.onclick = () => setTab(true); filesTab.onclick = () => setTab(false);
    buildRuntimePane(runtimePane, settings, cls);
    buildFilesPane(filesPane, settings.workspace.files, cls);
    openModal({
      title: `Agent Studio · ${cls.name}`,
      body: el("div", {},
        el("div", { class: "agent-config-summary" }, kv("Agent ID", binding.openclaw_agent_id), kv("Workspace", settings.workspace.workspace), kv("Config Hash", settings.config_hash || "—")),
        el("div", { class: "tabs agent-config-tabs" }, runtimeTab, filesTab), runtimePane, filesPane),
      wide: true, actions: [{ label: "关闭", kind: "secondary" }],
    });
  }

  function optionSelect(values, current, inheritLabel = "继承全局") {
    return el("select", {}, el("option", { value: "", selected: current === null || current === undefined || current === "" }, inheritLabel),
      values.map((value) => el("option", { value, selected: String(current) === String(value) }, String(value))));
  }

  function buildRuntimePane(pane, settings, cls) {
    const current = settings.runtime; const models = settings.models.filter((item) => item.available !== false);
    const name = el("input", { value: current.display_name || "", maxlength: "100" });
    const providerValues = [...new Set(models.map((item) => item.provider).filter(Boolean))].sort();
    const provider = optionSelect(providerValues, current.provider, "全部 Provider");
    const model = el("select", {}, el("option", { value: "", selected: !current.model }, "继承全局模型"));
    const fillModels = () => {
      const selected = model.value || current.model || "";
      [...model.options].slice(1).forEach((item) => item.remove());
      for (const item of models.filter((entry) => !provider.value || entry.provider === provider.value)) {
        model.append(el("option", { value: item.id, selected: item.id === selected }, `${item.name || item.id} · ${item.provider}`));
      }
      model.value = selected;
    };
    fillModels(); provider.addEventListener("change", fillModels);
    const fallbacks = el("input", { value: (current.model_fallbacks || []).join(", "), placeholder: "provider/model, provider/model" });
    const utility = el("select", {}, el("option", { value: "", selected: !current.utility_model }, "继承主模型"), models.map((item) => el("option", { value: item.id, selected: item.id === current.utility_model }, `${item.name || item.id} · ${item.provider}`)));
    const thinking = optionSelect(["off", "minimal", "low", "medium", "high", "xhigh", "adaptive", "max"], current.thinking_default);
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
    const save = el("button", { class: "primary", type: "button" }, "保存 Runtime 配置");
    save.onclick = async () => {
      const numberOrNull = (input) => input.value === "" ? null : Number(input.value);
      const params = {};
      if (temperature.value !== "") params.temperature = Number(temperature.value);
      if (topP.value !== "") params.topP = Number(topP.value);
      if (maxTokens.value !== "") params.maxTokens = Number(maxTokens.value);
      if (cacheRetention.value.trim()) params.cacheRetention = cacheRetention.value.trim();
      const body = {
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
      save.disabled = true;
      try { await api(`/admin/openclaw/agents/${cls.id}`, { method: "PATCH", body }); toast("Runtime 配置已提交，Gateway 将重启", "success"); setTimeout(load, 1400); }
      catch (error) { toast(error.message, "error"); } finally { save.disabled = false; }
    };
    pane.append(
      el("div", { class: "admin-config-grid compact" }, kv("Skills", (current.skills || []).join(", ") || "inherit"), kv("Tool profile", current.tools?.profile || "inherit")),
      el("div", { class: "form-grid agent-runtime-grid" },
        field("显示名", name), field("Provider", provider), field("主模型", model), field("Fallback models", fallbacks), field("Utility model", utility),
        field("Thinking effort", thinking), field("Reasoning visibility", reasoning), field("Verbose", verbose), field("Fast mode", fast),
        field("Context injection", injection), field("单文件 Prompt 上限", bootstrap), field("Prompt 总上限", bootstrapTotal), field("Skill Prompt 上限", skillBudget),
        field("Memory search", memory), field("Temperature", temperature), field("Top P", topP), field("Max tokens", maxTokens), field("Cache retention", cacheRetention)),
      el("div", { class: "row-gap", style: { justifyContent: "flex-end" } }, save));
  }

  function buildFilesPane(pane, files, cls) {
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
    };
    select.onchange = renderFile;
    save.onclick = async () => {
      save.disabled = true;
      try {
        const result = await api(`/admin/openclaw/agents/${cls.id}/workspace/${encodeURIComponent(current.name)}`, { method: "PUT", body: { content: editor.value, expected_sha256: current.sha256 } });
        current.content = editor.value; current.sha256 = result.sha256; current.characters = result.characters; current.customized = true;
        renderFile(); toast(`${current.name} 已保存`, "success");
      } catch (error) { toast(error.message, "error"); } finally { save.disabled = false; }
    };
    reset.onclick = async () => {
      const accepted = await confirmDanger({
        title: `恢复 ${current.name}`, lines: ["当前自定义内容会被系统默认内容覆盖。", "此操作会记录到审计日志。"], confirmLabel: "恢复默认",
      });
      if (!accepted) return;
      reset.disabled = true;
      try {
        const result = await api(`/admin/openclaw/agents/${cls.id}/workspace/${encodeURIComponent(current.name)}/reset`, { method: "POST", body: {} });
        Object.assign(current, result); renderFile(); toast(`${current.name} 已恢复默认`, "success");
      } catch (error) { toast(error.message, "error"); } finally { reset.disabled = false; }
    };
    pane.append(el("div", { class: "agent-file-toolbar" }, field("Workspace file", select), meta),
      el("p", { class: "muted" }, "AGENTS.md 是核心系统行为提示；SOUL.md 与 IDENTITY.md 控制人格和身份。保存后从后续 Agent turn 生效。"),
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
