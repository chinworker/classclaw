import { el, clear, toast as notify } from "./util.js";
import { statusBadge, emptyState, qrBindingPanel, openDrawer as drawer, openModal as modal, field, confirmDanger as danger, errorPanel } from "./components.js";
import { openAgentModelSettings, selectModel } from "./agentModelSettings.js";
import { appConfig } from "./config.js";
import { sourceEditor } from "./sourceEditor.js";
import { diffPreviewModal } from "./settingsEditor.js";
import { confirmDiscard } from "./adminView.js";

export function agentPanel(agent, view, { onRefresh = () => {} } = {}) {
  const api = view.api;
  const toast = (...args) => { if (view.active) notify(...args); };
  const confirmDanger = (options) => danger({ ...options, signal: view.signal });
  const openModal = (options) => view.overlay(modal(options));
  const openDrawer = (options) => view.overlay(drawer(options));
  function refreshAfterRestart(delay = 1600) { view.delay(() => { if (!view.isDirty()) onRefresh(); }, delay); }
  function kv(label, value) { return el("div", { class: "admin-kv" }, el("span", {}, label), el("code", {}, value ?? "—")); }
  async function loadModels(agent, host) {
    try {
      const isClass = agent.kind === "class";
      const endpoint = isClass ? `/classes/${agent.class.id}/agent-chat/models` : `/admin/openclaw/agents/${encodeURIComponent(agent.identifier)}`;
      const data = await api(isClass ? endpoint : `${endpoint}/settings`);
      const current = isClass ? data.configured : { model: data.runtime.model };
      const controls = isClass ? {
        main_model: selectModel(data.models, current.main_model, "继承全局主模型"),
        image_model: selectModel(data.image_models, current.image_model, "跟随主模型 / 全局图片模型"),
        speech_model: selectModel(data.speech_models, current.speech_model, "浏览器语音识别", { speech: true }),
      } : { model: selectModel(data.models, current.model, "继承全局主模型") };
      const labels = { main_model: "主模型", model: "主模型", image_model: "图片理解", speech_model: "语音识别" };
      const changes = () => Object.fromEntries(Object.entries(controls).map(([key, control]) => [key, control.value || null])
        .filter(([key, value]) => value !== (current[key] || null)));
      const save = el("button", { class: "primary", type: "button", disabled: true }, "保存模型");
      const errors = el("div");
      view.trackDirty(() => host.isConnected && Object.keys(changes()).length > 0);
      for (const [key, control] of Object.entries(controls)) {
        control.value = current[key] || "";
        control.addEventListener("change", () => { save.disabled = !Object.keys(changes()).length; });
        host.append(field(labels[key], control));
      }
      if (!isClass) host.append(el("p", { class: "muted" }, "系统 Agent 图片能力跟随 OpenClaw 模型配置；语音识别按班级配置。"));
      host.append(save, errors);
      save.addEventListener("click", async () => {
        const body = changes(); if (!Object.keys(body).length) return;
        diffPreviewModal(Object.entries(body).map(([key, value]) => ({ path: `${agent.label}.${key}`, old: current[key], new: value })), {
          view, title: `预览模型差异 · ${agent.label}`, onSave: async () => {
            const result = await api(endpoint, { method: "PATCH", body });
            Object.assign(current, body); save.disabled = true; clear(errors);
            toast(result.restart_requested ? "模型已保存，Gateway 正在重启" : "模型已保存", "success");
            refreshAfterRestart(result.restart_requested ? 1600 : 0);
          },
        });
      });
    } catch (error) { if (view.active) host.append(errorPanel(error, { onRetry: () => { clear(host); void loadModels(agent, host); } })); }
  }
  function agentCard(agent) {
    const binding = agent.binding; const cls = agent.class; const isClass = agent.kind === "class";
    const ownerLabel = isClass ? (agent.owner?.username || "未分配班主任") : agent.description;
    const models = el("div", { class: "agent-inline-models" });
    if (agent.present) void loadModels(agent, models);
    return el("div", { class: "card admin-agent-card" },
      el("div", { class: "row-gap", style: { justifyContent: "space-between" } }, el("div", {}, el("b", {}, agent.label), el("div", { class: "muted" }, ownerLabel)), statusBadge(agent.status)),
      el("div", { class: "admin-config-grid compact" },
        kv("类型", agent.kind.toUpperCase()), kv("Agent ID", agent.agent_id || "NOT CREATED"),
        kv("Workspace", agent.workspace_path || "NOT CONFIGURED"),
        isClass ? kv("微信绑定", binding?.status === "linked" ? "已绑定" : "尚未绑定完成") : kv("启用状态", agent.enabled ? "已启用" : "已关闭"),
        kv("主模型", `${agent.model_summary?.main_model || "未配置"}${agent.model_summary?.inherits_main ? "（继承全局）" : ""}`),
        isClass ? kv("图片理解", agent.model_summary?.image_model || "跟随主模型 / 全局图片模型") : null,
        isClass ? kv("语音识别", agent.model_summary?.speech_model || "浏览器语音识别") : null),
      models,
      binding?.last_error ? el("pre", { class: "admin-error-log" }, binding.last_error) : null,
      el("div", { class: "row-gap" },
        el("button", { class: "secondary", type: "button", disabled: !agent.present, onclick: () => editAgent(agent) }, "运行参数"),
        el("button", { class: "secondary", type: "button", disabled: !agent.present, onclick: () => editAgent(agent, "files") }, "提示词与身份"),
        isClass ? el("button", { class: "secondary", type: "button", disabled: !agent.present, onclick: async () => view.overlay(await openAgentModelSettings(cls.id, {
          isActive: () => view.active,
          signal: view.signal,
          onSaved: (result) => refreshAfterRestart(result.restart_requested ? 1600 : 0),
        })) }, "主 / 图片 / 语音模型") : null,
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
    (initialTab === "files" ? openDrawer : openModal)({
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
    view.trackDirty(() => pane.isConnected && JSON.stringify(readValues()) !== JSON.stringify(initialValues));
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
    const source = sourceEditor("", { label: "工作区文件源码" });
    const editor = source.input;
    view.trackDirty(() => editor.isConnected && editor.value !== current.content);
    const save = el("button", { class: "primary", type: "button" }, "保存文件");
    const reset = el("button", { class: "danger", type: "button" }, "恢复系统默认");
    const renderFile = () => {
      current = files.find((item) => item.name === select.value) || files[0];
      source.setText(current.content); clear(meta);
      meta.append(el("span", { class: `tag ${current.customized ? "tag-warn" : "tag-muted"}` }, current.customized ? "CUSTOMIZED" : "DEFAULT"), el("code", {}, `${current.characters} chars`), el("code", {}, current.sha256));
      reset.disabled = !current.resettable;
      reset.title = current.resettable ? "恢复 ClassClaw 默认内容" : "该文件没有 ClassClaw 管理的默认内容";
    };
    select.onchange = async () => {
      const targetName = select.value;
      if (editor.value !== current.content) {
        select.value = current.name;
        if (!await confirmDiscard(view)) return;
        if (!view.active) return;
        select.value = targetName;
      }
      renderFile();
    };
    save.onclick = async () => {
      save.disabled = select.disabled = editor.disabled = reset.disabled = true;
      const target = current;
      const content = editor.value;
      try {
        const result = await api(`/admin/openclaw/agents/${encodeURIComponent(identifier)}/workspace/${encodeURIComponent(target.name)}`, { method: "PUT", body: { content, expected_sha256: target.sha256 } });
        target.content = content; target.sha256 = result.sha256; target.characters = result.characters; target.customized = true;
        renderFile(); toast(`${current.name} 已保存`, "success");
      } catch (error) { toast(error.message, "error"); }
      finally { save.disabled = select.disabled = editor.disabled = false; reset.disabled = !current.resettable; }
    };
    reset.onclick = async () => {
      if (!current.resettable) return;
      const accepted = await confirmDanger({
        title: `恢复 ${current.name}`, lines: ["当前自定义内容会被系统默认内容覆盖。", "此操作会记录到审计日志。"], confirmLabel: "恢复默认",
      });
      if (!accepted) return;
      reset.disabled = select.disabled = editor.disabled = save.disabled = true;
      try {
        const result = await api(`/admin/openclaw/agents/${encodeURIComponent(identifier)}/workspace/${encodeURIComponent(current.name)}/reset`, { method: "POST", body: {} });
        Object.assign(current, result); renderFile(); toast(`${current.name} 已恢复默认`, "success");
      } catch (error) { toast(error.message, "error"); }
      finally { select.disabled = editor.disabled = save.disabled = false; reset.disabled = !current.resettable; }
    };
    pane.append(el("div", { class: "agent-file-toolbar" }, field("工作区文件", select), meta),
      el("p", { class: "muted" }, kind === "extractor" ? "数据提取智能体的 AGENTS.md 决定结构化解析规则，修改后请验证文件解析结果。" : "AGENTS.md 是核心系统行为提示；SOUL.md 与 IDENTITY.md 控制人格和身份。保存后从后续 Agent turn 生效。"),
      source.el, el("div", { class: "row-gap", style: { justifyContent: "space-between", marginTop: "10px" } }, reset, save));
    renderFile();
  }

  function openQr(classId) {
    const panel = qrBindingPanel(classId, { onDone: onRefresh });
    view.own(() => panel.dispose());
    openDrawer({ title: "微信绑定", onClose: () => panel.dispose(), body: el("div", {}, el("p", { class: "muted" }, "重新校验智能体配置并生成微信登录二维码。"), panel.el) });
    panel.start(true);
  }
  return agentCard(agent);
}
