// 统一 API 客户端：拼接 /api/v1、附带 Bearer Token 与 X-ClassClaw-Surface、解析统一响应。

const API_PREFIX = "/api/v1";
export let AI_REQUEST_TIMEOUT_MS = 150_000;

let authToken = null;
let unauthorizedHandler = null;

export function setToken(token) { authToken = token || null; }
export function getToken() { return authToken; }
export function onUnauthorized(handler) { unauthorizedHandler = handler; }
export function configureApi(config) {
  const seconds = Number(config?.ai_request_timeout_seconds);
  if (Number.isFinite(seconds) && seconds > 0) AI_REQUEST_TIMEOUT_MS = Math.round(seconds * 1000);
}

export function createAiTaskId() {
  if (globalThis.crypto?.randomUUID) return globalThis.crypto.randomUUID();
  return "xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx".replace(/[xy]/g, (char) => {
    const value = Math.floor(Math.random() * 16);
    return (char === "x" ? value : (value & 0x3) | 0x8).toString(16);
  });
}

export async function cancelAiTask(taskId, token = authToken) {
  if (!taskId) return;
  const headers = { "X-ClassClaw-Surface": "web" };
  if (token) headers.Authorization = `Bearer ${token}`;
  try {
    await fetch(`${API_PREFIX}/ai/tasks/${encodeURIComponent(taskId)}/cancel`, {
      method: "POST",
      headers,
      keepalive: true,
    });
  } catch { /* 原请求仍会在断开检测或超时后终止。 */ }
}

export class ApiError extends Error {
  constructor(message, { code = "UNKNOWN", status = 0, details = null, requestId = null } = {}) {
    super(message);
    this.name = "ApiError";
    this.code = code;
    this.status = status;
    this.details = details;
    this.requestId = requestId;
  }
}

// path 以 "/" 开头；body 传对象自动 JSON 化，传 FormData 走 multipart。
export async function api(path, { method = "GET", body, headers = {}, timeoutMs = 0, signal = null, aiTaskId = null, onDelta = null } = {}) {
  const requestToken = authToken;
  const finalHeaders = { "X-ClassClaw-Surface": "web", ...headers };
  if (aiTaskId) finalHeaders["X-ClassClaw-AI-Task-ID"] = aiTaskId;
  if (onDelta) finalHeaders.Accept = "text/event-stream";
  if (requestToken) finalHeaders.Authorization = `Bearer ${requestToken}`;
  let payload = body;
  if (body !== undefined && !(body instanceof FormData)) {
    finalHeaders["Content-Type"] = "application/json";
    payload = JSON.stringify(body);
  }
  let response;
  const controller = timeoutMs > 0 || signal ? new AbortController() : null;
  let cancellationSent = false;
  const cancelRequest = () => {
    if (aiTaskId && !cancellationSent) {
      cancellationSent = true;
      void cancelAiTask(aiTaskId, requestToken);
    }
    controller?.abort();
  };
  if (signal?.aborted) cancelRequest();
  else signal?.addEventListener("abort", cancelRequest, { once: true });
  const timeoutId = controller && timeoutMs > 0 ? window.setTimeout(cancelRequest, timeoutMs) : null;
  try {
    response = await fetch(`${API_PREFIX}${path}`, { method, headers: finalHeaders, body: payload, signal: controller?.signal });
    if (response.ok && onDelta && response.headers.get("Content-Type")?.includes("text/event-stream")) {
      return await readChatStream(response, onDelta);
    }
    let data = {};
    try { data = await response.json(); } catch (error) {
      if (controller?.signal.aborted) throw error;
      if (response.ok) throw new ApiError("服务返回了无法解析的响应", { code: "INVALID_RESPONSE" });
    }
    if (!response.ok || data.success === false) {
      const error = responseError(data, response);
      if (response.status === 401 && unauthorizedHandler && authToken === requestToken) unauthorizedHandler(error);
      throw error;
    }
    return data.data;
  } catch (error) {
    if (signal?.aborted) {
      throw new ApiError("操作已取消", { code: "REQUEST_CANCELLED" });
    }
    if (controller?.signal.aborted) {
      throw new ApiError("智能体处理超时，尚未收到结果；涉及写入时请先核对结果再重试", { code: "REQUEST_TIMEOUT" });
    }
    if (error instanceof ApiError) throw error;
    throw new ApiError("网络连接失败，请检查服务是否在线", { code: "NETWORK_ERROR" });
  } finally {
    if (timeoutId !== null) window.clearTimeout(timeoutId);
    signal?.removeEventListener("abort", cancelRequest);
  }
}

function responseError(data, response) {
  return new ApiError(data.error?.message || `请求失败（HTTP ${response.status}）`, {
    code: data.error?.code || `HTTP_${response.status}`, status: response.status,
    details: data.error?.details ?? null, requestId: data.request_id || response.headers.get("X-Request-ID"),
  });
}

export async function readChatStream(response, onDelta) {
  const reader = response.body?.getReader();
  if (!reader) throw new ApiError("浏览器未获得回复流", { code: "INVALID_RESPONSE" });
  const decoder = new TextDecoder();
  let buffer = "";
  try {
    while (true) {
      const { done, value } = await reader.read();
      buffer += decoder.decode(value, { stream: !done });
      buffer = buffer.replace(/\r\n/g, "\n");
      let boundary;
      while ((boundary = buffer.indexOf("\n\n")) !== -1) {
        const block = buffer.slice(0, boundary);
        buffer = buffer.slice(boundary + 2);
        if (block.length > 1_048_576) throw new ApiError("回复数据过大", { code: "INVALID_RESPONSE" });
        let event = "message";
        const lines = [];
        for (const line of block.split("\n")) {
          if (line.startsWith("event:")) event = line.slice(6).trim();
          if (line.startsWith("data:")) lines.push(line.slice(5).trimStart());
        }
        if (!lines.length) continue; // Heartbeats are not messages.
        let data;
        try { data = JSON.parse(lines.join("\n")); } catch {
          throw new ApiError("回复流格式错误", { code: "INVALID_RESPONSE" });
        }
        if (!data || typeof data !== "object") throw new ApiError("回复流格式错误", { code: "INVALID_RESPONSE" });
        if (event === "error" || data.success === false) throw responseError(data, response);
        if (event === "delta") {
          if (typeof data.data?.text !== "string") throw new ApiError("回复片段格式错误", { code: "INVALID_RESPONSE" });
          await onDelta(data.data.text);
        }
        if (event === "done") {
          if (typeof data.data?.reply !== "string") throw new ApiError("缺少完整回复", { code: "INVALID_RESPONSE" });
          return data.data;
        }
      }
      if (buffer.length > 1_048_576) throw new ApiError("回复数据过大", { code: "INVALID_RESPONSE" });
      if (done) throw new ApiError("回复连接提前结束；涉及写入时请先核对结果再重试", {
        code: "STREAM_INTERRUPTED", requestId: response.headers.get("X-Request-ID"),
      });
    }
  } finally {
    try { await reader.cancel(); } catch { /* Connection may already have closed. */ }
    reader.releaseLock();
  }
}
