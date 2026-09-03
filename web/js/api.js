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

export async function cancelAiTask(taskId) {
  if (!taskId) return;
  const headers = { "X-ClassClaw-Surface": "web" };
  if (authToken) headers.Authorization = `Bearer ${authToken}`;
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
export async function api(path, { method = "GET", body, headers = {}, timeoutMs = 0, signal = null, aiTaskId = null } = {}) {
  const finalHeaders = { "X-ClassClaw-Surface": "web", ...headers };
  if (aiTaskId) finalHeaders["X-ClassClaw-AI-Task-ID"] = aiTaskId;
  if (authToken) finalHeaders.Authorization = `Bearer ${authToken}`;
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
      void cancelAiTask(aiTaskId);
    }
    controller?.abort();
  };
  if (signal?.aborted) cancelRequest();
  else signal?.addEventListener("abort", cancelRequest, { once: true });
  const timeoutId = controller && timeoutMs > 0 ? window.setTimeout(cancelRequest, timeoutMs) : null;
  try {
    response = await fetch(`${API_PREFIX}${path}`, { method, headers: finalHeaders, body: payload, signal: controller?.signal });
  } catch {
    if (signal?.aborted) {
      throw new ApiError("操作已取消", { code: "REQUEST_CANCELLED" });
    }
    if (controller?.signal.aborted) {
      throw new ApiError("智能体处理超时，请稍后重试", { code: "REQUEST_TIMEOUT" });
    }
    throw new ApiError("网络连接失败，请检查服务是否在线", { code: "NETWORK_ERROR" });
  } finally {
    if (timeoutId !== null) window.clearTimeout(timeoutId);
    signal?.removeEventListener("abort", cancelRequest);
  }
  let data = {};
  try { data = await response.json(); } catch { /* 非 JSON 响应 */ }
  if (!response.ok || data.success === false) {
    const error = new ApiError(data.error?.message || `请求失败（HTTP ${response.status}）`, {
      code: data.error?.code || `HTTP_${response.status}`,
      status: response.status,
      details: data.error?.details ?? null,
      requestId: data.request_id || response.headers.get("X-Request-ID"),
    });
    if (response.status === 401 && unauthorizedHandler) unauthorizedHandler(error);
    throw error;
  }
  return data.data;
}
