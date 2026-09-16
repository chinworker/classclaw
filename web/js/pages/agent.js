// 班级 Agent 网页对话、语音输入与文件上传；管理入口在账户设置。

import { el, clear, fmtDateTime, toast } from "../util.js";
import { api, AI_REQUEST_TIMEOUT_MS, createAiTaskId } from "../api.js";
import { appConfig, featureEnabled } from "../config.js";
import { state, refreshOpenclaw } from "../state.js";
import { pageHeader, errorPanel, skeleton, statusBadge, openclawBlocked, emptyState, confirmDanger } from "../components.js";
import { agentChatStore } from "../agentChatStore.js";

const MAX_FILES = 8;
const ACCEPTED_FILES = ".xlsx,.xlsm,.docx,.pptx,.csv,.tsv,.pdf,.png,.jpg,.jpeg,.gif,.webp,.heic,.heif,.json,.xml,.rtf,.md,.markdown,.txt";
let activeView = null;

// Page disposal only releases DOM subscriptions and microphone/input resources.
// Sent chat requests keep running in agentChatStore until completion or Stop.
export function dispose() {
  activeView?.cleanup();
  activeView = null;
}

function fileSize(bytes) {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
}

function messageNode(message) {
  const isUser = message.role === "user";
  const files = message.files || [];
  return el("article", { class: `agent-chat-message ${isUser ? "from-user" : "from-agent"}` },
    el("div", { class: "agent-chat-avatar", aria: { hidden: "true" } }, isUser ? "我" : "AI"),
    el("div", { class: "agent-chat-bubble" },
      el("div", { class: "agent-chat-meta" }, isUser ? "你" : "班级 Agent", el("span", {}, message.time || "")),
      files.length ? el("div", { class: "agent-chat-files" }, files.map((file) =>
        el("span", { class: "agent-chat-file" }, "文件 · ", file.name, el("small", {}, fileSize(file.size || file.file_size || 0))))) : null,
      el("div", { class: `agent-chat-text${message.error ? " is-error" : ""}` }, message.text),
      message.incomplete ? el("small", { class: "field-error" }, "以上仅为未完成的部分回复，不能据此确认操作结果。") : null,
      message.error && message.requestId ? el("small", { class: "muted" }, `问题编号：${message.requestId}`) : null));
}

function welcomeNode(onSuggestion) {
  const prompts = ["今天班里有哪些需要关注的情况？", "查询今天的课程和值日安排", "帮我整理并登记一条班级事项"];
  return el("div", { class: "agent-chat-welcome" },
    el("div", { class: "agent-chat-welcome-mark" }, "C"),
    el("h2", {}, "和本班 Agent 对话"),
    el("p", { class: "muted" }, "可以查询班级信息、处理自然语言指令，也可以附加课表、名单、图片或 PDF。涉及写入时，Agent 会先给出预览，等你确认后再保存。"),
    el("div", { class: "agent-chat-suggestions" }, prompts.map((prompt) =>
      el("button", { class: "agent-chat-suggestion", type: "button", onclick: () => onSuggestion(prompt) }, prompt))));
}

function provisionPanel() {
  return el("div", { class: "card" }, emptyState("需要先创建班级 Agent", "请到账户设置中创建本班专属 Agent；网页对话不要求绑定微信。",
    el("a", { href: "#/account" }, "前往账户设置")));
}

function chatPanel(binding, scope, conversation) {
  let selectedFiles = [...conversation.files];
  let busy = Boolean(conversation.activeController);
  let disposed = false;
  const chatRuntime = { recognition: null, mediaRecorder: null, mediaStream: null, voiceController: null, discardRecording: false };
  let listening = false;
  let transcribing = false;
  let renderedMessageCount = -1;
  let renderedController;
  let renderedRevision = -1;
  let renderedTail = null;
  let renderedThinkingOptions = null;
  let renderedPendingThinking = null;
  const fileAnalysisEnabled = featureEnabled("file_analysis");
  const maxBytes = Number(appConfig.storage.max_attachment_bytes) || 20 * 1024 * 1024;
  const messages = el("div", { class: "agent-chat-messages", role: "log", aria: { live: "polite", label: "班级 Agent 对话消息" } });
  const selected = el("div", { class: "agent-chat-selected hidden" });
  const textarea = el("textarea", {
    class: "agent-chat-input",
    rows: "1",
    maxlength: "20000",
    placeholder: "给班级 Agent 发消息…",
    aria: { label: "消息内容" },
    value: conversation.draft,
  });
  const fileInput = el("input", { type: "file", hidden: true, multiple: true, accept: ACCEPTED_FILES, disabled: !fileAnalysisEnabled });
  const attachButton = el("button", {
    class: "agent-composer-tool",
    type: "button",
    disabled: !fileAnalysisEnabled,
    title: fileAnalysisEnabled ? "上传文件" : "管理员已关闭文件智能解析",
    aria: { label: "上传文件" },
  }, "附件");
  const Recognition = globalThis.SpeechRecognition || globalThis.webkitSpeechRecognition;
  const modelVoiceSupported = Boolean(globalThis.MediaRecorder && navigator.mediaDevices?.getUserMedia);
  const voiceSupported = binding.speech_model ? modelVoiceSupported : Boolean(Recognition);
  const voiceButton = el("button", {
    class: "agent-composer-tool",
    type: "button",
    disabled: !voiceSupported,
    title: voiceSupported
      ? binding.speech_model ? `使用 ${binding.speech_model} 录音并转写` : "使用浏览器语音输入"
      : "当前浏览器不支持所选语音输入方式",
    aria: { label: "语音输入" },
  }, "语音");
  const sendButton = el("button", { class: "agent-chat-send", type: "button", aria: { label: "发送消息" } }, "发送");
  const stopButton = el("button", { class: "agent-chat-stop hidden", type: "button" }, "停止");
  const statusLine = el("span", { class: "agent-composer-status muted", role: "status" }, "Enter 发送 · Shift+Enter 换行");
  const thinkingSelect = el("select", { class: "agent-thinking-select", aria: { label: "本会话思考强度" },
    title: "仅显示当前模型支持的选项，设置只影响本会话的后续消息。", disabled: true });
  const thinkingNote = el("small", { class: "agent-thinking-note muted", role: "status" });
  thinkingSelect.addEventListener("change", () => {
    agentChatStore.setThinkingLevel(scope, conversation, thinkingSelect.value || null);
    thinkingSelect.value = conversation.thinkingLevel || "";
  });

  function renderThinking() {
    const profile = scope.thinkingOptions;
    const defaultLabel = profile?.levels.find((item) => item.id === profile.default_level)?.label;
    const pendingLevel = conversation.activeController && conversation.thinkingLevel
      && !profile?.levels.some((item) => item.id === conversation.thinkingLevel) ? conversation.thinkingLevel : null;
    if (profile !== renderedThinkingOptions || pendingLevel !== renderedPendingThinking || !thinkingSelect.children.length) {
      renderedThinkingOptions = profile;
      renderedPendingThinking = pendingLevel;
      clear(thinkingSelect);
      thinkingSelect.append(el("option", { value: "", disabled: Boolean(profile && !defaultLabel) },
        profile ? defaultLabel ? `跟随默认（${defaultLabel}）` : "默认不适用，请选择" : "正在读取模型支持的选项…"));
      for (const item of profile?.levels || []) thinkingSelect.append(el("option", { value: item.id }, item.label));
      if (pendingLevel) {
        thinkingSelect.append(el("option", { value: pendingLevel, disabled: true }, "本轮使用的原设置"));
      }
      thinkingSelect.title = profile?.model ? `当前模型：${profile.model}；仅影响本会话。` : "仅影响本会话的后续消息。";
    }
    const defaultNote = profile?.default_adjusted && conversation.thinkingLevel === null
      ? defaultLabel ? `配置默认不受当前模型支持，默认采用${defaultLabel}。` : "配置默认不受当前模型支持，请选择可用档位。" : "";
    thinkingNote.textContent = [conversation.thinkingNotice, defaultNote].filter(Boolean).join(" ");
    thinkingNote.classList.toggle("hidden", !thinkingNote.textContent);
    thinkingSelect.value = conversation.thinkingLevel || "";
    thinkingSelect.disabled = Boolean(conversation.activeController || !profile);
  }

  function scrollBottom() { requestAnimationFrame(() => { if (!disposed) messages.scrollTop = messages.scrollHeight; }); }

  function renderMessages() {
    clear(messages);
    renderedTail = null;
    if (!conversation.messages.length) {
      messages.append(welcomeNode((prompt) => { textarea.value = prompt; conversation.draft = prompt; resizeInput(); textarea.focus(); }));
    } else {
      const nodes = conversation.messages.map(messageNode);
      messages.append(...nodes);
      renderedTail = nodes.at(-1)?.querySelector(".agent-chat-text");
    }
    if (conversation.activeController && !conversation.messages.at(-1)?.streaming) messages.append(el("article", { class: "agent-chat-message from-agent agent-chat-thinking" },
      el("div", { class: "agent-chat-avatar" }, "AI"),
      el("div", { class: "agent-chat-bubble" }, el("span", {}, "正在思考"), el("i"), el("i"), el("i"))));
    scrollBottom();
  }

  function renderFiles() {
    conversation.files = selectedFiles;
    clear(selected);
    selected.classList.toggle("hidden", !selectedFiles.length);
    for (const [index, file] of selectedFiles.entries()) {
      selected.append(el("span", { class: "agent-selected-file" },
        el("span", {}, file.name), el("small", {}, fileSize(file.size)),
        el("button", { type: "button", aria: { label: `移除 ${file.name}` }, onclick: () => {
          selectedFiles.splice(index, 1);
          renderFiles();
        } }, "×")));
    }
  }

  function addFiles(fileList) {
    const incoming = [...fileList];
    if (selectedFiles.length + incoming.length > MAX_FILES) {
      toast(`每条消息最多上传 ${MAX_FILES} 个文件`, "error");
      return;
    }
    const oversized = incoming.find((file) => file.size > maxBytes);
    if (oversized) {
      toast(`${oversized.name} 超过 ${fileSize(maxBytes)} 上限`, "error");
      return;
    }
    selectedFiles.push(...incoming);
    renderFiles();
  }

  function resizeInput() {
    conversation.draft = textarea.value;
    textarea.style.height = "auto";
    textarea.style.height = `${Math.min(textarea.scrollHeight, 180)}px`;
  }

  function setBusy(value) {
    busy = value;
    textarea.disabled = value;
    fileInput.disabled = value || !fileAnalysisEnabled;
    attachButton.disabled = value || !fileAnalysisEnabled;
    voiceButton.disabled = value || transcribing || !voiceSupported;
    sendButton.classList.toggle("hidden", value);
    stopButton.classList.toggle("hidden", !value);
    statusLine.textContent = value ? "班级 Agent 正在处理，可随时停止" : "Enter 发送 · Shift+Enter 换行";
  }

  function stopVoice(discard = false) {
    if (!listening) return;
    chatRuntime.discardRecording = discard;
    try { chatRuntime.recognition?.stop(); } catch { /* 已停止 */ }
    try { if (chatRuntime.mediaRecorder?.state === "recording") chatRuntime.mediaRecorder.stop(); } catch { /* 已停止 */ }
  }

  function resetVoiceButton(message = "语音已转成文字，可修改后发送") {
    if (disposed) return;
    listening = false;
    voiceButton.classList.remove("active");
    voiceButton.textContent = "语音";
    statusLine.textContent = message;
    textarea.focus();
  }

  async function startModelVoice() {
    if (!modelVoiceSupported) return;
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      if (disposed) { stream.getTracks().forEach((track) => track.stop()); return; }
      const candidates = ["audio/webm;codecs=opus", "audio/mp4", "audio/ogg;codecs=opus"];
      const mimeType = candidates.find((item) => globalThis.MediaRecorder.isTypeSupported?.(item)) || "";
      const recorder = new globalThis.MediaRecorder(stream, mimeType ? { mimeType } : undefined);
      const chunks = [];
      chatRuntime.mediaStream = stream;
      chatRuntime.mediaRecorder = recorder;
      chatRuntime.discardRecording = false;
      recorder.addEventListener("dataavailable", (event) => { if (event.data.size) chunks.push(event.data); });
      recorder.addEventListener("start", () => {
        if (disposed) return;
        listening = true;
        voiceButton.classList.add("active");
        voiceButton.textContent = "停止录音";
        statusLine.textContent = `正在录音，停止后由 ${binding.speech_model} 转写…`;
      });
      recorder.addEventListener("stop", async () => {
        stream.getTracks().forEach((track) => track.stop());
        chatRuntime.mediaStream = null;
        chatRuntime.mediaRecorder = null;
        const discard = chatRuntime.discardRecording;
        chatRuntime.discardRecording = false;
        if (disposed || discard || !chunks.length) { resetVoiceButton("录音已取消"); return; }
        listening = false;
        transcribing = true;
        voiceButton.classList.remove("active");
        voiceButton.textContent = "识别中";
        voiceButton.disabled = true;
        statusLine.textContent = `正在使用 ${binding.speech_model} 识别语音…`;
        const type = recorder.mimeType || chunks[0].type || "audio/webm";
        const extension = type.includes("mp4") ? "m4a" : type.includes("ogg") ? "ogg" : "webm";
        const form = new FormData();
        form.append("audio", new Blob(chunks, { type }), `voice-${Date.now()}.${extension}`);
        const controller = new AbortController();
        const taskId = createAiTaskId();
        chatRuntime.voiceController = controller;
        try {
          const result = await api(`/classes/${scope.classId}/agent-chat/transcriptions`, {
            method: "POST", body: form, timeoutMs: AI_REQUEST_TIMEOUT_MS, signal: controller.signal, aiTaskId: taskId,
          });
          if (disposed) return;
          textarea.value = [textarea.value.trim(), result.text].filter(Boolean).join(" ");
          resizeInput();
          resetVoiceButton(`已由 ${result.model} 转成文字，可修改后发送`);
        } catch (error) {
          if (disposed) return;
          resetVoiceButton(error.code === "REQUEST_CANCELLED" ? "语音识别已取消" : "语音识别失败，请重试");
          if (error.code !== "REQUEST_CANCELLED") toast(error.message, "error");
        } finally {
          transcribing = false;
          if (chatRuntime.voiceController === controller) chatRuntime.voiceController = null;
          voiceButton.disabled = busy || !voiceSupported;
        }
      });
      recorder.start(250);
    } catch (error) {
      if (disposed) return;
      resetVoiceButton("无法使用麦克风");
      toast(error?.name === "NotAllowedError" ? "请允许浏览器使用麦克风" : "录音无法启动，请重试", "error");
    }
  }

  function startBrowserVoice() {
    if (!Recognition) return;
    if (listening) { stopVoice(); return; }
    const recognition = new Recognition();
    const base = textarea.value.trim();
    chatRuntime.recognition = recognition;
    recognition.lang = "zh-CN";
    recognition.continuous = true;
    recognition.interimResults = true;
    recognition.onstart = () => {
      if (disposed) return;
      listening = true;
      voiceButton.classList.add("active");
      voiceButton.textContent = "停止录音";
      statusLine.textContent = "正在听，请直接说话…";
    };
    recognition.onresult = (event) => {
      if (disposed) return;
      let finalText = "";
      let interimText = "";
      for (const result of event.results) {
        if (result.isFinal) finalText += result[0].transcript;
        else interimText += result[0].transcript;
      }
      textarea.value = [base, finalText, interimText].filter(Boolean).join(base ? " " : "");
      resizeInput();
    };
    recognition.onerror = (event) => {
      if (disposed) return;
      if (event.error !== "aborted") toast(event.error === "not-allowed" ? "请允许浏览器使用麦克风" : "语音识别失败，请重试", "error");
    };
    recognition.onend = () => {
      chatRuntime.recognition = null;
      resetVoiceButton();
    };
    try { recognition.start(); } catch { toast("语音输入暂时无法启动", "error"); }
  }

  function startVoice() {
    if (disposed || busy || transcribing) return;
    if (listening) { stopVoice(); return; }
    if (binding.speech_model) void startModelVoice();
    else startBrowserVoice();
  }

  function send() {
    if (disposed || busy) return;
    if (listening) { stopVoice(); return; }
    if (transcribing) return;
    const text = textarea.value.trim();
    const files = [...selectedFiles];
    if (!text && !files.length) { textarea.focus(); return; }

    textarea.value = "";
    selectedFiles = [];
    renderFiles();
    resizeInput();
    void agentChatStore.sendMessage(scope, conversation, text, files);
  }

  fileInput.addEventListener("change", () => { addFiles(fileInput.files); fileInput.value = ""; });
  attachButton.addEventListener("click", () => fileInput.click());
  voiceButton.addEventListener("click", startVoice);
  sendButton.addEventListener("click", send);
  stopButton.addEventListener("click", () => agentChatStore.stopMessage(conversation));
  textarea.addEventListener("input", resizeInput);
  textarea.addEventListener("keydown", (event) => {
    if (event.key === "Enter" && !event.shiftKey && !event.isComposing) {
      event.preventDefault();
      void send();
    }
  });

  const composer = el("div", { class: "agent-chat-composer" }, selected,
    el("div", { class: "agent-chat-compose-row" }, fileInput, attachButton, voiceButton, textarea, sendButton, stopButton),
    el("div", { class: "agent-chat-compose-foot" }, statusLine,
      el("span", { class: "muted" }, `最多 ${MAX_FILES} 个文件 · AI 可能出错，写入前请核对预览`)));
  const panel = el("section", { class: "agent-chat-panel", aria: { label: "与本班专属 Agent 对话" } },
    el("div", { class: "agent-chat-bar" },
      el("div", {}, el("b", {}, "本班专属 Agent"), el("span", { class: "agent-online" }, "在线")),
      el("div", { class: "agent-thinking-settings" },
        el("label", { class: "agent-thinking-control" }, "本会话思考强度 ", thinkingSelect), thinkingNote)),
    messages,
    composer);
  function refresh() {
    if (disposed) return;
    renderThinking();
    const sameLayout = renderedMessageCount === conversation.messages.length && renderedController === conversation.activeController;
    if (sameLayout && renderedRevision === conversation.revision) return;
    renderedRevision = conversation.revision;
    if (sameLayout && renderedTail && conversation.messages.at(-1)?.streaming) {
      renderedTail.textContent = conversation.messages.at(-1).text;
      scrollBottom();
      return;
    }
    renderedMessageCount = conversation.messages.length;
    renderedController = conversation.activeController;
    if (busy && !conversation.activeController && conversation.draft) {
      textarea.value = conversation.draft;
      selectedFiles = [...conversation.files];
      renderFiles();
      resizeInput();
    }
    setBusy(Boolean(conversation.activeController));
    renderMessages();
  }
  renderFiles();
  resizeInput();
  refresh();
  return {
    el: panel, conversationId: conversation.id, refresh,
    thinkingError(error) {
      if (disposed) return;
      thinkingNote.textContent = `${error.message}；发送前会重新检查。`;
      thinkingNote.classList.remove("hidden");
    },
    dispose() {
      if (disposed) return;
      if (!conversation.deleted) {
        conversation.draft = textarea.value;
        conversation.files = selectedFiles;
      }
      disposed = true;
      chatRuntime.voiceController?.abort();
      chatRuntime.discardRecording = true;
      try { chatRuntime.recognition?.stop(); } catch { /* 已停止 */ }
      try { if (chatRuntime.mediaRecorder?.state === "recording") chatRuntime.mediaRecorder.stop(); } catch { /* 已停止 */ }
      chatRuntime.mediaStream?.getTracks().forEach((track) => track.stop());
    },
  };
}

export async function render(mount, ctx, helpers) {
  if (!mount.isConnected) return;
  dispose();
  const view = {
    disposed: false, panel: null, unsubscribe: null, onFocus: null, deletePending: false, confirmController: new AbortController(),
    cleanup() {
      if (this.disposed) return;
      this.disposed = true;
      this.confirmController.abort();
      this.unsubscribe?.();
      if (this.onFocus) window.removeEventListener("focus", this.onFocus);
      this.panel?.dispose();
    },
  };
  activeView = view;
  const live = () => activeView === view && !view.disposed && mount.isConnected;
  const scope = state.user?.role === "head_teacher" && state.classId
    ? agentChatStore.getScope(state.user.id, state.classId) : null;
  const newChatButton = el("button", { class: "secondary", type: "button", disabled: !scope }, "新对话");
  newChatButton.addEventListener("click", () => {
    if (scope && live()) agentChatStore.createConversation(scope);
  });
  clear(mount);
  mount.append(pageHeader("班级 Agent", "文字、语音和文件都可以直接交给本班专属 Agent。", newChatButton));
  const host = el("div", { class: "agent-chat-main" }, skeleton(4));
  const list = el("div", { class: "agent-conversation-list", role: "region", aria: { label: "对话列表" } });
  const sidebar = el("aside", { class: "agent-chat-sidebar" }, list,
    el("p", { class: "muted" }, el("a", { href: "#/account" }, "绑定与设置请前往账户设置")));
  mount.append(scope ? el("div", { class: "agent-chat-layout" }, sidebar, host) : host);
  let binding = null;
  let renderedListSignature = null;
  let thinkingLoad = null;

  async function deleteConversation(conversation) {
    if (!live() || view.deletePending || !scope.conversations.includes(conversation)) return;
    view.deletePending = true;
    try {
      const confirmed = await confirmDanger({
        title: "删除对话", confirmLabel: "删除对话", signal: view.confirmController.signal,
        lines: [
          `删除“${conversation.title}”及其在本页的消息、草稿和待发送附件？删除后无法恢复。`,
          "如果此对话正在回答，将立即停止回答并删除。其他对话不受影响。",
          "已保存的班级数据和已上传的附件不会被删除。",
        ],
      });
      if (confirmed && live()) agentChatStore.deleteConversation(scope, conversation.id);
    } finally { view.deletePending = false; }
  }

  async function reloadThinking() {
    if (!thinkingLoad) thinkingLoad = api(`/classes/${scope.classId}/agent-chat/thinking`, { timeoutMs: 15_000 });
    const pending = thinkingLoad;
    try {
      const profile = await pending;
      if (live() && !scope.disposed) agentChatStore.setThinkingOptions(scope, profile);
      return profile;
    } catch (error) {
      if (live() && !scope.disposed) agentChatStore.setThinkingOptions(scope, null);
      throw error;
    } finally {
      if (thinkingLoad === pending) thinkingLoad = null;
    }
  }

  function refreshConversations() {
    if (!live()) return;
    if (scope.disposed) { dispose(); clear(mount); return; }
    const current = agentChatStore.selectedConversation(scope);
    if (binding) {
      if (view.panel?.conversationId !== current.id) {
        view.panel?.dispose();
        view.panel = chatPanel(binding, scope, current);
        clear(host);
        host.append(view.panel.el);
      }
      current.unread = false;
      view.panel.refresh();
    }
    const signature = JSON.stringify([scope.selectedId, ...scope.conversations.map(({ id, title, status, unread }) => [id, title, status, unread])]);
    if (signature === renderedListSignature) return;
    renderedListSignature = signature;
    clear(list);
    list.append(el("h3", {}, `对话列表 · ${scope.conversations.length}`));
    const labels = { idle: "未发送", running: "处理中", completed: "已回复", stopped: "已停止", failed: "请求失败" };
    list.append(el("div", { class: "agent-conversation-items" }, scope.conversations.map((conversation) =>
      el("div", { class: "agent-conversation-row" }, el("button", {
        class: `agent-conversation-item${conversation.id === scope.selectedId ? " active" : ""}`,
        type: "button", aria: { pressed: String(conversation.id === scope.selectedId) },
        onclick: () => { if (live()) agentChatStore.selectConversation(scope, conversation.id); },
      },
      el("b", { class: "agent-conversation-title" }, conversation.title),
      el("span", { class: `agent-conversation-status ${conversation.status}` },
        `${labels[conversation.status]}${conversation.unread ? " · 新回复" : ""}`),
      el("small", { class: "muted" }, fmtDateTime(conversation.createdAt))),
      el("button", {
        class: "agent-conversation-delete", type: "button", title: "删除对话",
        aria: { label: `删除对话：${conversation.title}` }, onclick: () => { void deleteConversation(conversation); },
      }, "删除")))));
    list.append(el("p", { class: "muted agent-conversation-hint" }, "新建、切换对话或前往站内其他页面不会停止回答。记录仅保留在本标签页内存，刷新或关闭后不保留。"));
  }
  if (scope) {
    view.unsubscribe = agentChatStore.subscribe(scope, refreshConversations);
    refreshConversations();
  }

  let status;
  try {
    status = await refreshOpenclaw({ force: true });
    if (!live()) return;
    helpers.refreshOpenclawDot();
  } catch (error) {
    if (!live()) return;
    clear(host);
    host.append(errorPanel(error, { onRetry: () => render(mount, ctx, helpers) }));
    return;
  }

  clear(host);
  if (state.user.role === "admin") {
    host.append(el("div", { class: "card" },
      el("h3", {}, "智能服务状态"),
      status.ready
        ? el("p", {}, statusBadge("active", "已连接"), el("span", { class: "muted", style: { marginLeft: "8px" } }, `Gateway：${status.gateway_url}`))
        : openclawBlocked(status, () => render(mount, ctx, helpers)),
      el("p", { class: "muted" }, "班级对话入口仅供绑定了班级的班主任账号使用。")));
    return;
  }
  if (!state.classId) {
    host.append(el("div", { class: "card" }, emptyState("尚未创建班级", "创建班级后会自动生成专属 Agent。")));
    return;
  }
  if (!status.gateway_live || !status.plugin_ready) {
    host.append(openclawBlocked(status, () => render(mount, ctx, helpers)));
    return;
  }

  try {
    const loadedBinding = await api(`/classes/${scope.classId}/agent-binding`);
    if (!live()) return;
    if (!loadedBinding.openclaw_agent_id) {
      host.append(provisionPanel());
      return;
    }
    binding = loadedBinding;
    refreshConversations();
    view.onFocus = () => { void reloadThinking().catch((error) => { if (live()) view.panel?.thinkingError(error); }); };
    window.addEventListener("focus", view.onFocus);
    try { await reloadThinking(); }
    catch (error) { if (live()) view.panel?.thinkingError(error); }
  } catch (error) {
    if (!live()) return;
    if (error.code === "NOT_FOUND") host.append(provisionPanel());
    else host.append(errorPanel(error, { onRetry: () => render(mount, ctx, helpers) }));
  }
}
