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
      thinkingLevel: null, revision: 0,
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
      const scope = { ownerId, classId, conversations: [], selectedId: null, listeners: new Set(), disposed: false };
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
    conversation.thinkingLevel = value;
    conversation.revision += 1;
    notify(scope);
    return true;
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
    body.append("conversation_id", conversation.id);
    body.append("stream", "true");
    if (conversation.thinkingLevel !== null) body.append("thinking_level", conversation.thinkingLevel);
    if (text) body.append("text", text);
    uploads.forEach((file) => body.append("files", file));
    const taskId = newId();
    let partial = null;
    notify(scope);
    try {
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
      notify(scope);
      scope.listeners.clear();
    }
    scopes.clear();
  }

  return { getScope, createConversation, selectedConversation, selectConversation, setThinkingLevel, subscribe, sendMessage, stopMessage, clear };
}

export const agentChatStore = createChatStore();
