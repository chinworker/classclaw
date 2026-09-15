import { LoginError } from "./web-login.js";

const uuid = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/;
export function validateLoginRequest(body, config) {
  if (!body || typeof body !== "object" || Array.isArray(body)) throw new Error("Invalid request");
  const allowed = {
    start: ["action", "classId", "force", "timeoutMs", "ttlMs"],
    wait: ["action", "classId", "loginId", "timeoutMs"],
    verify: ["action", "classId", "loginId", "challengeId", "code", "timeoutMs"],
  }[body.action];
  if (!allowed || Object.keys(body).some((key) => !allowed.includes(key))) throw new Error("Invalid fields");
  const classes = config?.plugins?.entries?.classclaw?.config?.agentClasses || {};
  if (typeof body.classId !== "string" || !uuid.test(body.classId) || !Object.values(classes).includes(body.classId)) throw new Error("Unbound class");
  if (!Number.isInteger(body.timeoutMs) || body.timeoutMs < 1 || body.timeoutMs > 120_000) throw new Error("Invalid timeout");
  if (body.action === "start") {
    if (typeof body.force !== "boolean" || !Number.isInteger(body.ttlMs) || body.ttlMs < 30_000 || body.ttlMs > 1_800_000) throw new Error("Invalid start");
  } else {
    if (typeof body.loginId !== "string" || !uuid.test(body.loginId)) throw new Error("Invalid login");
    if (body.action === "verify" && (typeof body.challengeId !== "string" || !uuid.test(body.challengeId)
        || typeof body.code !== "string" || !/^[0-9]{1,12}$/.test(body.code))) throw new Error("Invalid code");
  }
  return body;
}

export function webLoginHandler(engine, getConfig) {
  return async (req, res) => {
    const send = (status, value) => {
      res.statusCode = status; res.setHeader("Content-Type", "application/json; charset=utf-8");
      res.setHeader("Cache-Control", "no-store"); res.end(JSON.stringify(value));
    };
    if (req.method !== "POST") { res.setHeader("Allow", "POST"); send(405, { ok: false }); return true; }
    let body;
    try {
      const chunks = []; let size = 0;
      for await (const raw of req) {
        const chunk = Buffer.from(raw); size += chunk.length;
        if (size > 4096) { send(413, { ok: false }); return true; }
        chunks.push(chunk);
      }
      body = validateLoginRequest(JSON.parse(Buffer.concat(chunks).toString("utf8")), getConfig());
    } catch { send(400, { ok: false, error: { code: "WECHAT_REQUEST_INVALID", message: "无效的班级微信登录请求。" } }); return true; }
    try {
      const payload = await engine[body.action](body);
      send(200, { ok: true, payload });
    } catch (error) {
      send(error instanceof LoginError ? error.status : 503, { ok: false, error: {
        code: error instanceof LoginError ? error.code : "WECHAT_LOGIN_UNAVAILABLE",
        message: error instanceof LoginError ? error.message : "微信登录暂不可用，请稍后重试。",
      } });
    }
    return true;
  };
}
