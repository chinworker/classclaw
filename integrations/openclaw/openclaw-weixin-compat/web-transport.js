import { randomBytes } from "node:crypto";
import { LoginError, trustedWeixinUrl } from "./web-login.js";

// Same pinned Tencent 2.4.6 iLink protocol, with bounded whole-response
// deadlines and no URL/body logging (QR secrets and codes occur in both).
export function createWechatTransport({ fetchImpl = fetch, routeTag = () => undefined } = {}) {
  async function request(baseUrl, endpoint, { timeoutMs, signal, body }) {
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), timeoutMs);
    const headers = { "iLink-App-Id": "bot", "iLink-App-ClientVersion": String((2 << 16) | (4 << 8) | 6) };
    const tag = routeTag();
    if (tag) headers.SKRouteTag = tag;
    if (body !== undefined) Object.assign(headers, { "Content-Type": "application/json", AuthorizationType: "ilink_bot_token",
      "X-WECHAT-UIN": Buffer.from(String(randomBytes(4).readUInt32BE())).toString("base64") });
    try {
      const response = await fetchImpl(new URL(endpoint, `${trustedWeixinUrl(baseUrl)}/`), {
        method: body === undefined ? "GET" : "POST", headers, redirect: "error",
        signal: signal ? AbortSignal.any([signal, controller.signal]) : controller.signal,
        ...(body === undefined ? {} : { body: JSON.stringify(body) }),
      });
      if (!response.ok) throw new Error("Weixin unavailable");
      const chunks = []; let size = 0;
      for await (const chunk of response.body) {
        size += chunk.length;
        if (size > 1024 * 1024) { controller.abort(); throw new Error("Weixin response too large"); }
        chunks.push(chunk);
      }
      const value = JSON.parse(Buffer.concat(chunks).toString("utf8"));
      if (!value || typeof value !== "object" || Array.isArray(value) || (value.ret !== undefined && value.ret !== 0)) {
        throw new LoginError("WECHAT_RESPONSE_INVALID", "微信登录服务暂不可用，请稍后重试。", 502);
      }
      return value;
    } finally { clearTimeout(timer); }
  }
  return {
    requestQr: (options) => request("https://ilinkai.weixin.qq.com", "ilink/bot/get_bot_qrcode?bot_type=3", {
      ...options, body: { local_token_list: [] }, // Never send another class's credentials.
    }),
    pollQr: ({ qrToken, verifyCode, baseUrl, ...options }) => {
      const query = new URLSearchParams({ qrcode: qrToken });
      if (verifyCode) query.set("verify_code", verifyCode);
      return request(baseUrl, `ilink/bot/get_qrcode_status?${query}`, options);
    },
  };
}
