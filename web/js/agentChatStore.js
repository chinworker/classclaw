// 对话和请求属于当前登录会话，不属于页面 DOM；原文只保留在标签页内存。
import { api, AI_REQUEST_TIMEOUT_MS, createAiTaskId } from "./api.js";

export function createChatStore({ request = api, newId = createAiTaskId, timestamp = () => new Date() } = {}) {
  const scopes = new Map();

  function notify(scope) {
    for (const listener of [...scope.listeners]) listener();
  }

  function createConversation(scope) {
    if (scope.disposed) return null;
    const conversation = {
      id: newId(), title: "新对话", createdAt: timestamp(), messages: [],
      draft: "", files: [], status: "idle", unread: false, activeController: null,
      thinkingLevel: null, thinkingNotice: "", revision: 0,
    };
    scope.conversations.unshift(conversation);
    scope.selectedId = conversation.id;
    notify(scope);
    return conversation;
  }

  function getScope(ownerId, classId) {
    if (!ownerId || !classId) throw new Error("对话需要已登录的用户和班级");
    const key = JSON.stringify([ownerId, classId]);
    if (!scopes.has(key)) {
      const scope = { ownerId, classId, conversations: [], selectedId: null, listeners: new Set(), disposed: false, thinkingOptions: null };
      scopes.set(key, scope);
      createConversation(scope);
    }
    return scopes.get(key);
  }

  function selectedConversation(scope) {
    return scope.conversations.find((conversation) => conversation.id === scope.selectedId);
  }

  function selectConversation(scope, id) {
    if (scope.disposed || !scope.conversations.some((conversation) => conversation.id === id)) return;
    scope.selectedId = id;
    selectedConversation(scope).unread = false;
    notify(scope);
  }

  function subscribe(scope, listener) {
    scope.listeners.add(listener);
    return () => scope.listeners.delete(listener);
  }

  function setThinkingLevel(scope, conversation, value) {
    if (scope.disposed || !scope.conversations.includes(conversation) || conversation.activeController) return false;
    if (value !== null && !["off", "minimal", "low", "medium", "high", "xhigh", "adaptive", "max"].includes(value)) return false;
    if (value !== null && scope.thinkingOptions && !scope.thinkingOptions.levels.some((item) => item.id === value)) return false;
    conversation.thinkingLevel = value;
    conversation.thinkingNotice = "";
    conversation.revision += 1;
    notify(scope);
    return true;
  }

  function reconcileThinking(scope, conversation) {
    if (!scope.thinkingOptions || conversation.activeController || conversation.thinkingLevel === null) return;
    if (scope.thinkingOptions.levels.some((item) => item.id === conversation.thinkingLevel)) return;
    conversation.thinkingLevel = null;
    conversation.thinkingNotice = "原思考强度不适用于当前模型，已恢复为跟随默认。";
    conversation.revision += 1;
  }

  function setThinkingOptions(scope, profile) {
    if (scope.disposed) return;
    scope.thinkingOptions = profile;
    for (const conversation of scope.conversations) reconcileThinking(scope, conversation);
    notify(scope);
  }

  function time() {
    return timestamp().toLocaleTimeString("zh-CN", { hour: "2-digit", minute: "2-digit", hour12: false });
  }

  async function sendMessage(scope, conversation, text, files = []) {
    if (scope.disposed || !scope.conversations.includes(conversation) || conversation.activeController) return false;
    text = text.trim();
    if (!text && !files.length) return false;
    const uploads = [...files];
    if (!conversation.messages.length) conversation.title = (text || uploads[0].name).slice(0, 40);
    conversation.messages.push({
      role: "user", text: text || "请处理这些文件。", time: time(),
      files: uploads.map(({ name, size, type }) => ({ name, size, type })),
    });
    conversation.draft = "";
    conversation.files = [];
    const controller = new AbortController();
    conversation.activeController = controller;
    conversation.status = "running";
    conversation.unread = false;
    // Capture all ownership and request data before awaiting. Never look at a
    // global current class/conversation when a late response arrives.
    const body = new FormData();
    const thinkingLevel = conversation.thinkingLevel;
    body.append("conversation_id", conversation.id);
    body.append("stream", "true");
    if (thinkingLevel !== null) body.append("thinking_level", thinkingLevel);
    if (text) body.append("text", text);
    uploads.forEach((file) => body.append("files", file));
    const taskId = newId();
    let partial = null;
    let thinkingLoaded = false;
    let messageStarted = false;
    notify(scope);
    try {
      // The preflight belongs to this conversation's request too: navigation
      // must not cancel a message after the user has pressed Send.
      const profile = await request(`/classes/${scope.classId}/agent-chat/thinking`, { signal: controller.signal, timeoutMs: 15_000 });
      if (scope.disposed || controller.signal.aborted) throw Object.assign(new Error("cancelled"), { code: "REQUEST_CANCELLED" });
      thinkingLoaded = true;
      setThinkingOptions(scope, profile);
      if (thinkingLevel !== null && !profile.levels.some((item) => item.id === thinkingLevel)) {
        throw Object.assign(new Error("模型支持的思考选项已更新，请核对后再次发送。"), { code: "CHAT_THINKING_UNSUPPORTED" });
      }
      if (thinkingLevel === null && !profile.default_level) {
        throw Object.assign(new Error("请选择当前模型支持的思考强度后再发送。"), { code: "CHAT_THINKING_UNSUPPORTED" });
      }
      if (scope.disposed || controller.signal.aborted) throw Object.assign(new Error("cancelled"), { code: "REQUEST_CANCELLED" });
      messageStarted = true;
      const result = await request(`/classes/${scope.classId}/agent-chat/messages`, {
        method: "POST", body, timeoutMs: AI_REQUEST_TIMEOUT_MS, signal: controller.signal, aiTaskId: taskId,
        onDelta(delta) {
          if (scope.disposed || controller.signal.aborted || !delta) return;
          if (!partial) {
            partial = { role: "assistant", text: "", time: time(), streaming: true };
            conversation.messages.push(partial);
          }
          partial.text += delta;
          conversation.revision += 1;
          notify(scope);
        },
      });
      if (!scope.disposed) {
        if (controller.signal.aborted) throw Object.assign(new Error("cancelled"), { code: "REQUEST_CANCELLED" });
        if (partial) Object.assign(partial, { text: result.reply, streaming: false });
        else conversation.messages.push({ role: "assistant", text: result.reply, time: time() });
        conversation.revision += 1;
        conversation.status = "completed";
      }
    } catch (error) {
      if (!scope.disposed) {
        if (!thinkingLoaded) scope.thinkingOptions = null;
        if (!messageStarted) { conversation.draft = text; conversation.files = uploads; }
        if (partial) Object.assign(partial, { streaming: false, incomplete: true });
        const stopped = error.code === "REQUEST_CANCELLED";
        conversation.status = stopped ? "stopped" : "failed";
        conversation.messages.push({
          role: "assistant", time: time(), error: !stopped, requestId: error.requestId,
          text: stopped ? "已停止本次回答。你可以修改消息后重新发送。" : `暂时无法完成：${error.message}`,
        });
      }
    } finally {
      conversation.activeController = null;
      if (!scope.disposed) {
        reconcileThinking(scope, conversation);
        conversation.unread = true;
        notify(scope);
      }
    }
    return true;
  }

  function stopMessage(conversation) { conversation.activeController?.abort(); }

  function clear() {
    for (const scope of scopes.values()) {
      scope.disposed = true;
      for (const conversation of scope.conversations) stopMessage(conversation);
      scope.conversations = [];
      scope.thinkingOptions = null;
      notify(scope);
      scope.listeners.clear();
    }
    scopes.clear();
  }

  return { getScope, createConversation, selectedConversation, selectConversation, setThinkingLevel, setThinkingOptions,
    subscribe, sendMessage, stopMessage, clear };
}

export const agentChatStore = createChatStore();
