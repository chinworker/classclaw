// 视图和在途请求保留在内存；历史从服务端恢复，原文不写入浏览器持久存储。
import { api, AI_REQUEST_TIMEOUT_MS, createAiTaskId } from "./api.js";
import { thinkingChoices } from "./agentThinking.js";
import { fmtDateTime } from "./util.js";

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
      thinkingLevel: null, thinkingNotice: "", revision: 0, deleted: false,
      historyLoaded: true, historyLoading: false, historyError: null, historyHasMore: false, historyOffset: 0,
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
      const scope = { ownerId, classId, conversations: [], selectedId: null, listeners: new Set(), disposed: false, thinkingOptions: null,
        historyLoaded: false, historyLoading: false, historyError: null, historyHasMore: false, historyOffset: 0,
        historyController: new AbortController(), removedIds: new Set() };
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

  function deleteConversation(scope, id) {
    if (scope.disposed) return false;
    const index = scope.conversations.findIndex((conversation) => conversation.id === id);
    if (index < 0) return false;
    const [conversation] = scope.conversations.splice(index, 1);
    scope.removedIds.add(id);
    // Remove ownership before aborting: late preflight/stream callbacks must not
    // restore a deleted conversation's draft or messages.
    conversation.deleted = true;
    stopMessage(conversation);
    conversation.messages = [];
    conversation.draft = "";
    conversation.files = [];
    conversation.unread = false;
    if (!scope.conversations.length) createConversation(scope);
    else {
      if (scope.selectedId === id) {
        scope.selectedId = scope.conversations[Math.min(index, scope.conversations.length - 1)].id;
        selectedConversation(scope).unread = false;
      }
      notify(scope);
    }
    return true;
  }

  function subscribe(scope, listener) {
    scope.listeners.add(listener);
    return () => scope.listeners.delete(listener);
  }

  async function loadHistory(scope, { more = false, force = false } = {}) {
    if (scope.disposed || scope.historyLoading || (scope.historyLoaded && !more && !force)) return;
    const initial = selectedConversation(scope);
    const initialWasOnly = scope.conversations.length === 1;
    scope.historyLoading = true; scope.historyError = null; notify(scope);
    try {
      const offset = more ? scope.historyOffset : 0;
      const result = await request(`/classes/${scope.classId}/agent-chat/conversations?offset=${offset}&limit=50`, {
        signal: scope.historyController.signal,
      });
      if (scope.disposed) return;
      if (!Array.isArray(result.items)) throw new Error("历史对话列表格式无效");
      for (const row of result.items) {
        if (scope.removedIds.has(row.id) || scope.conversations.some((item) => item.id === row.id)) continue;
        scope.conversations.push({ id: row.id, title: row.title || "历史对话", createdAt: row.created_at,
          messages: [], draft: "", files: [], status: "history", unread: false, activeController: null,
          thinkingLevel: null, thinkingNotice: "", revision: 0, deleted: false, remote: true,
          historyLoaded: false, historyLoading: false, historyError: null, historyHasMore: false, historyOffset: 0 });
      }
      if (initialWasOnly && result.items.length && initial && scope.selectedId === initial.id && !initial.remote && !initial.messages.length
          && !initial.draft && !initial.files.length && !initial.activeController && scope.conversations[0] === initial) {
        scope.conversations.splice(0, 1);
        scope.selectedId = scope.conversations[0]?.id;
        if (!scope.selectedId) createConversation(scope);
      }
      scope.historyLoaded = true; scope.historyHasMore = Boolean(result.has_more);
      scope.historyOffset = offset + result.items.length;
    } catch (error) {
      if (!scope.disposed) scope.historyError = error;
    } finally {
      if (!scope.disposed) { scope.historyLoading = false; notify(scope); }
    }
  }

  async function loadMessages(scope, conversation, { more = false } = {}) {
    if (scope.disposed || conversation.deleted || conversation.historyLoading || conversation.activeController
        || (conversation.historyLoaded && !more)) return;
    conversation.historyLoading = true; conversation.historyError = null; conversation.revision++; notify(scope);
    try {
      const offset = more ? conversation.historyOffset : 0;
      const result = await request(`/classes/${scope.classId}/agent-chat/conversations/${conversation.id}/messages?offset=${offset}&limit=100`, {
        signal: scope.historyController.signal,
      });
      if (scope.disposed || conversation.deleted) return;
      if (!Array.isArray(result.messages)) throw new Error("历史消息格式无效");
      const existing = new Set(conversation.messages.map((item) => item.id).filter(Boolean));
      const messages = result.messages.filter((item) => !item.id || !existing.has(item.id)).map((item) => ({
        id: item.id, role: item.role, text: item.text,
        time: item.timestamp ? fmtDateTime(item.timestamp) : "",
        incomplete: Boolean(item.incomplete),
      }));
      conversation.messages = more ? [...messages, ...conversation.messages] : messages;
      conversation.historyLoaded = true; conversation.historyHasMore = Boolean(result.has_more);
      conversation.historyOffset = offset + result.messages.length;
      const last = conversation.messages.at(-1);
      conversation.status = last?.role === "assistant" ? (last.incomplete ? "stopped" : "completed") : "history";
    } catch (error) {
      if (!scope.disposed && !conversation.deleted) conversation.historyError = error;
    } finally {
      if (!scope.disposed && !conversation.deleted) { conversation.historyLoading = false; conversation.revision++; notify(scope); }
    }
  }

  async function removeConversation(scope, conversation) {
    if (scope.disposed || conversation.deleted) return;
    stopMessage(conversation);
    await request(`/classes/${scope.classId}/agent-chat/conversations/${conversation.id}`, {
      method: "DELETE", signal: scope.historyController.signal,
    });
    if (!scope.disposed) deleteConversation(scope, conversation.id);
  }

  function setThinkingLevel(scope, conversation, value) {
    if (scope.disposed || !scope.conversations.includes(conversation) || conversation.activeController) return false;
    if (value !== null && !["off", "low", "medium", "high"].includes(value)) return false;
    if (value !== null && scope.thinkingOptions && !thinkingChoices(scope.thinkingOptions).some((item) => item.id === value)) return false;
    conversation.thinkingLevel = value;
    conversation.thinkingNotice = "";
    conversation.revision += 1;
    notify(scope);
    return true;
  }

  function reconcileThinking(scope, conversation) {
    if (!scope.thinkingOptions || conversation.activeController || conversation.thinkingLevel === null) return;
    if (thinkingChoices(scope.thinkingOptions).some((item) => item.id === conversation.thinkingLevel)) return;
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
    if (scope.disposed || !scope.conversations.includes(conversation) || conversation.activeController || !conversation.historyLoaded || conversation.historyLoading) return false;
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
    let effectiveThinking = null;
    const live = () => !scope.disposed && !conversation.deleted && !controller.signal.aborted && conversation.activeController === controller;
    function ensurePartial() {
      if (!partial) {
        partial = { role: "assistant", text: "", time: time(), streaming: true };
        conversation.messages.push(partial);
      }
      return partial;
    }
    function startThinking() {
      const message = ensurePartial();
      message.thinking ||= { text: "", elapsedMs: 0, startedAt: timestamp().getTime(), active: false, expanded: false };
      if (!message.thinking.active) {
        message.thinking.startedAt = timestamp().getTime();
        message.thinking.active = true;
      }
    }
    function finishThinking(interrupted = false) {
      const thinking = partial?.thinking;
      if (!thinking) return;
      if (thinking.active) thinking.elapsedMs += Math.max(0, timestamp().getTime() - thinking.startedAt);
      thinking.active = false;
      thinking.interrupted = interrupted;
    }
    notify(scope);
    try {
      // The preflight belongs to this conversation's request too: navigation
      // must not cancel a message after the user has pressed Send.
      const profile = await request(`/classes/${scope.classId}/agent-chat/thinking`, { signal: controller.signal, timeoutMs: 15_000 });
      if (scope.disposed || controller.signal.aborted) throw Object.assign(new Error("cancelled"), { code: "REQUEST_CANCELLED" });
      thinkingLoaded = true;
      setThinkingOptions(scope, profile);
      if (thinkingLevel !== null && !thinkingChoices(profile).some((item) => item.id === thinkingLevel)) {
        throw Object.assign(new Error("模型支持的思考选项已更新，请核对后再次发送。"), { code: "CHAT_THINKING_UNSUPPORTED" });
      }
      if (thinkingLevel === null && !profile.default_level) {
        throw Object.assign(new Error("请选择当前模型支持的思考强度后再发送。"), { code: "CHAT_THINKING_UNSUPPORTED" });
      }
      if (scope.disposed || controller.signal.aborted) throw Object.assign(new Error("cancelled"), { code: "REQUEST_CANCELLED" });
      messageStarted = true;
      effectiveThinking = thinkingLevel ?? profile.default_level;
      if (effectiveThinking !== "off") startThinking();
      conversation.revision += 1;
      notify(scope);
      const result = await request(`/classes/${scope.classId}/agent-chat/messages`, {
        method: "POST", body, timeoutMs: AI_REQUEST_TIMEOUT_MS, signal: controller.signal, aiTaskId: taskId,
        onThinking(event) {
          if (!live()) return;
          if (event.state === "started") {
            effectiveThinking = event.level;
            if (effectiveThinking !== "off") startThinking();
            else if (partial?.thinking) { finishThinking(); delete partial.thinking; }
          } else if (event.state === "delta" && effectiveThinking !== "off" && event.text) {
            startThinking();
            partial.thinking.text += event.text;
          }
          conversation.revision += 1;
          notify(scope);
        },
        onDelta(delta) {
          if (!live() || !delta) return;
          finishThinking();
          ensurePartial().text += delta;
          conversation.revision += 1;
          notify(scope);
        },
      });
      if (!scope.disposed && !conversation.deleted) {
        if (controller.signal.aborted) throw Object.assign(new Error("cancelled"), { code: "REQUEST_CANCELLED" });
        finishThinking();
        if (partial) Object.assign(partial, { text: result.reply, streaming: false });
        else conversation.messages.push({ role: "assistant", text: result.reply, time: time() });
        conversation.revision += 1;
        conversation.status = "completed";
      }
    } catch (error) {
      if (!scope.disposed && !conversation.deleted) {
        if (!thinkingLoaded) scope.thinkingOptions = null;
        if (!messageStarted) { conversation.draft = text; conversation.files = uploads; }
        finishThinking(true);
        if (partial) Object.assign(partial, { streaming: false, incomplete: true });
        const stopped = error.code === "REQUEST_CANCELLED";
        conversation.status = stopped ? "stopped" : "failed";
        conversation.messages.push({
          role: "assistant", time: time(), error: !stopped, requestId: error.requestId,
          text: stopped ? "已停止本次回答。你可以修改消息后重新发送。" : `暂时无法完成：${error.message}`,
        });
      }
    } finally {
      finishThinking(conversation.status !== "completed");
      conversation.activeController = null;
      if (!scope.disposed && !conversation.deleted) {
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
      scope.historyController.abort();
      for (const conversation of scope.conversations) stopMessage(conversation);
      scope.conversations = [];
      scope.thinkingOptions = null;
      notify(scope);
      scope.listeners.clear();
    }
    scopes.clear();
  }

  return { getScope, createConversation, selectedConversation, selectConversation, deleteConversation, setThinkingLevel, setThinkingOptions,
    loadHistory, loadMessages, removeConversation, subscribe, sendMessage, stopMessage, clear };
}

export const agentChatStore = createChatStore();
