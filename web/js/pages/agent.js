// 班级 Agent 网页对话、语音输入、文件上传与微信绑定。

import { el, clear, fmtDateTime, toast } from "../util.js";
import { api, AI_REQUEST_TIMEOUT_MS, createAiTaskId } from "../api.js";
import { appConfig, featureEnabled } from "../config.js";
import { state, refreshOpenclaw } from "../state.js";
import { pageHeader, errorPanel, skeleton, statusBadge, qrBindingPanel, openclawBlocked, emptyState } from "../components.js";

const MAX_FILES = 8;
const ACCEPTED_FILES = ".xlsx,.xlsm,.docx,.pptx,.csv,.tsv,.pdf,.png,.jpg,.jpeg,.gif,.webp,.heic,.heif,.json,.xml,.rtf,.md,.markdown,.txt";
const chatRuntime = {
  scope: null,
  conversationId: null,
  messages: [],
  activeController: null,
  recognition: null,
};

function newId() {
  if (globalThis.crypto?.randomUUID) return globalThis.crypto.randomUUID();
  return "xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx".replace(/[xy]/g, (char) => {
    const value = Math.floor(Math.random() * 16);
    return (char === "x" ? value : (value & 0x3) | 0x8).toString(16);
  });
}

function resetRuntime(scope = chatRuntime.scope) {
  chatRuntime.activeController?.abort();
  try { chatRuntime.recognition?.stop(); } catch { /* 已停止 */ }
  chatRuntime.scope = scope;
  chatRuntime.conversationId = newId();
  chatRuntime.messages = [];
  chatRuntime.activeController = null;
  chatRuntime.recognition = null;
}

function fileSize(bytes) {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
}

function clock(value = new Date()) {
  return value.toLocaleTimeString("zh-CN", { hour: "2-digit", minute: "2-digit", hour12: false });
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
      el("div", { class: `agent-chat-text${message.error ? " is-error" : ""}` }, message.text)));
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

function bindingPanel(binding, mount, ctx, helpers) {
  const body = el("div", { class: "agent-binding-body" },
    el("p", {}, el("b", {}, binding.agent_name)),
    el("p", { class: "muted" }, binding.linked_at ? `微信绑定于 ${fmtDateTime(binding.linked_at)}` : "网页对话不要求绑定微信，需要时可在这里扫码。"),
    binding.last_error ? el("p", { class: "field-error" }, `最近错误：${binding.last_error}`) : null);
  const details = el("details", { class: "agent-binding-card" },
    el("summary", {},
      el("span", {}, "Agent 与微信"),
      statusBadge(binding.status),
      el("span", { class: "muted" }, binding.channel_account_id ? "微信已连接" : "微信未绑定")),
    body);
  if (!binding.channel_account_id) {
    body.append(qrBindingPanel(state.classId, { compact: true, onDone: () => render(mount, ctx, helpers) }).el);
  }
  return details;
}

function provisionPanel(mount, ctx, helpers, hint = "班级还没有可用于网页对话的专属 Agent。") {
  const button = el("button", { class: "primary", type: "button" }, "创建班级 Agent");
  button.addEventListener("click", async () => {
    button.disabled = true;
    button.textContent = "正在创建…";
    try {
      await api(`/classes/${state.classId}/agent-binding/provision`, { method: "POST", body: {} });
      await render(mount, ctx, helpers);
    } catch (error) {
      button.parentElement?.append(errorPanel(error));
      button.disabled = false;
      button.textContent = "重新创建";
    }
  });
  return el("div", { class: "card" }, emptyState("需要先创建班级 Agent", hint, button));
}

function chatPanel(binding) {
  let selectedFiles = [];
  let busy = false;
  let listening = false;
  let thinkingNode = null;
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
  const voiceButton = el("button", {
    class: "agent-composer-tool",
    type: "button",
    disabled: !Recognition,
    title: Recognition ? "语音输入" : "当前浏览器不支持语音转文字",
    aria: { label: "语音输入" },
  }, "语音");
  const sendButton = el("button", { class: "agent-chat-send", type: "button", aria: { label: "发送消息" } }, "发送");
  const stopButton = el("button", { class: "agent-chat-stop hidden", type: "button" }, "停止");
  const statusLine = el("span", { class: "agent-composer-status muted", role: "status" }, "Enter 发送 · Shift+Enter 换行");

  function scrollBottom() { requestAnimationFrame(() => { messages.scrollTop = messages.scrollHeight; }); }

  function renderMessages() {
    clear(messages);
    if (!chatRuntime.messages.length) {
      messages.append(welcomeNode((prompt) => { textarea.value = prompt; resizeInput(); textarea.focus(); }));
    } else {
      messages.append(...chatRuntime.messages.map(messageNode));
    }
    if (thinkingNode) messages.append(thinkingNode);
    scrollBottom();
  }

  function renderFiles() {
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
    textarea.style.height = "auto";
    textarea.style.height = `${Math.min(textarea.scrollHeight, 180)}px`;
  }

  function setBusy(value) {
    busy = value;
    textarea.disabled = value;
    fileInput.disabled = value || !fileAnalysisEnabled;
    attachButton.disabled = value || !fileAnalysisEnabled;
    voiceButton.disabled = value || !Recognition;
    sendButton.classList.toggle("hidden", value);
    stopButton.classList.toggle("hidden", !value);
    statusLine.textContent = value ? "班级 Agent 正在处理，可随时停止" : "Enter 发送 · Shift+Enter 换行";
  }

  function stopVoice() {
    if (!listening) return;
    try { chatRuntime.recognition?.stop(); } catch { /* 已停止 */ }
  }

  function startVoice() {
    if (!Recognition || busy) return;
    if (listening) { stopVoice(); return; }
    const recognition = new Recognition();
    const base = textarea.value.trim();
    chatRuntime.recognition = recognition;
    recognition.lang = "zh-CN";
    recognition.continuous = true;
    recognition.interimResults = true;
    recognition.onstart = () => {
      listening = true;
      voiceButton.classList.add("active");
      voiceButton.textContent = "停止录音";
      statusLine.textContent = "正在听，请直接说话…";
    };
    recognition.onresult = (event) => {
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
      if (event.error !== "aborted") toast(event.error === "not-allowed" ? "请允许浏览器使用麦克风" : "语音识别失败，请重试", "error");
    };
    recognition.onend = () => {
      listening = false;
      voiceButton.classList.remove("active");
      voiceButton.textContent = "语音";
      statusLine.textContent = "语音已转成文字，可修改后发送";
      chatRuntime.recognition = null;
      textarea.focus();
    };
    try { recognition.start(); } catch { toast("语音输入暂时无法启动", "error"); }
  }

  async function send() {
    if (busy) return;
    stopVoice();
    const text = textarea.value.trim();
    const files = [...selectedFiles];
    if (!text && !files.length) { textarea.focus(); return; }

    chatRuntime.messages.push({ role: "user", text: text || "请处理这些文件。", files, time: clock() });
    textarea.value = "";
    selectedFiles = [];
    renderFiles();
    resizeInput();
    thinkingNode = el("article", { class: "agent-chat-message from-agent agent-chat-thinking" },
      el("div", { class: "agent-chat-avatar" }, "AI"),
      el("div", { class: "agent-chat-bubble" }, el("span", {}, "正在思考"), el("i"), el("i"), el("i")));
    renderMessages();
    setBusy(true);

    const controller = new AbortController();
    const taskId = createAiTaskId();
    chatRuntime.activeController = controller;
    const body = new FormData();
    body.append("conversation_id", chatRuntime.conversationId);
    if (text) body.append("text", text);
    files.forEach((file) => body.append("files", file));
    try {
      const result = await api(`/classes/${state.classId}/agent-chat/messages`, {
        method: "POST",
        body,
        timeoutMs: AI_REQUEST_TIMEOUT_MS,
        signal: controller.signal,
        aiTaskId: taskId,
      });
      chatRuntime.messages.push({ role: "assistant", text: result.reply, time: clock() });
    } catch (error) {
      const stopped = error.code === "REQUEST_CANCELLED";
      chatRuntime.messages.push({
        role: "assistant",
        text: stopped ? "已停止本次回答。你可以修改消息后重新发送。" : `暂时无法完成：${error.message}`,
        error: !stopped,
        time: clock(),
      });
    } finally {
      thinkingNode = null;
      if (chatRuntime.activeController === controller) chatRuntime.activeController = null;
      setBusy(false);
      renderMessages();
      textarea.focus();
    }
  }

  fileInput.addEventListener("change", () => { addFiles(fileInput.files); fileInput.value = ""; });
  attachButton.addEventListener("click", () => fileInput.click());
  voiceButton.addEventListener("click", startVoice);
  sendButton.addEventListener("click", send);
  stopButton.addEventListener("click", () => chatRuntime.activeController?.abort());
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
  const panel = el("section", { class: "agent-chat-panel", aria: { label: `与 ${binding.agent_name} 对话` } },
    el("div", { class: "agent-chat-bar" },
      el("div", {}, el("b", {}, binding.agent_name), el("span", { class: "agent-online" }, "在线")),
      el("span", { class: "muted" }, "网页专属会话")),
    messages,
    composer);
  renderMessages();
  return panel;
}

export async function render(mount, ctx, helpers) {
  const scope = `${state.user?.id || state.user?.username || "user"}:${state.classId || "none"}`;
  if (chatRuntime.scope !== scope || !chatRuntime.conversationId) resetRuntime(scope);
  const newChatButton = el("button", { class: "secondary", type: "button" }, "新对话");
  newChatButton.addEventListener("click", () => {
    resetRuntime(scope);
    void render(mount, ctx, helpers);
  });
  clear(mount);
  mount.append(pageHeader("班级 Agent 对话", "文字、语音和文件都可以直接交给本班专属 Agent。", newChatButton));
  const host = el("div", {}, skeleton(4));
  mount.append(host);

  let status;
  try {
    status = await refreshOpenclaw({ force: true });
    helpers.refreshOpenclawDot();
  } catch (error) {
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
    const binding = await api(`/classes/${state.classId}/agent-binding`);
    if (!binding.openclaw_agent_id) {
      host.append(provisionPanel(mount, ctx, helpers));
      return;
    }
    host.append(el("div", { class: "agent-chat-layout" }, chatPanel(binding), bindingPanel(binding, mount, ctx, helpers)));
  } catch (error) {
    if (error.code === "NOT_FOUND") host.append(provisionPanel(mount, ctx, helpers));
    else host.append(errorPanel(error, { onRetry: () => render(mount, ctx, helpers) }));
  }
}

window.addEventListener("hashchange", () => {
  if (!location.hash.startsWith("#/agent")) {
    chatRuntime.activeController?.abort();
    try { chatRuntime.recognition?.stop(); } catch { /* 已停止 */ }
  }
});
