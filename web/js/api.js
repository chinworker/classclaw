// 统一 API 客户端：拼接 /api/v1、附带 Bearer Token 与 X-ClassClaw-Surface、解析统一响应。

const API_PREFIX = "/api/v1";

let authToken = null;
let unauthorizedHandler = null;

export function setToken(token) { authToken = token || null; }
export function getToken() { return authToken; }
export function onUnauthorized(handler) { unauthorizedHandler = handler; }

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
export async function api(path, { method = "GET", body, headers = {} } = {}) {
  const finalHeaders = { "X-ClassClaw-Surface": "web", ...headers };
  if (authToken) finalHeaders.Authorization = `Bearer ${authToken}`;
  let payload = body;
  if (body !== undefined && !(body instanceof FormData)) {
    finalHeaders["Content-Type"] = "application/json";
    payload = JSON.stringify(body);
  }
  let response;
  try {
    response = await fetch(`${API_PREFIX}${path}`, { method, headers: finalHeaders, body: payload });
  } catch {
    throw new ApiError("网络连接失败，请检查服务是否在线", { code: "NETWORK_ERROR" });
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
