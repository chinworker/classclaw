// ClassClaw's browser login state machine. No stdin, logging or disk storage of
// QR content / verification codes. The CLI login implementation is unchanged.
import { randomUUID } from "node:crypto";

export class LoginError extends Error {
  constructor(code, message, status = 409) { super(message); this.code = code; this.status = status; }
}
const stale = () => new LoginError("WECHAT_LOGIN_STALE", "二维码已被重新生成，请使用最新二维码。");
const terminal = new Set(["connected", "expired", "failed"]);

export function createWebLogin({ requestQr, pollQr, saveAccount, now = Date.now, uuid = randomUUID, maxSessions = 100 }) {
  const sessions = new Map();
  function clearSecrets(s) { s.qrToken = null; s.qrData = null; s.challengeId = null; }
  function finish(s, state, message) {
    s.state = state; s.message = message; clearSecrets(s);
    s.controller.abort(); clearTimeout(s.timer);
  }
  function current(s) {
    if (sessions.get(s.classId) !== s) throw stale();
    if (now() >= s.expiresAt && !terminal.has(s.state)) finish(s, "expired", "本次登录已过期，请重新生成二维码。");
    return !terminal.has(s.state);
  }
  function snapshot(s) {
    current(s);
    return {
      loginId: s.id, state: s.state, connected: s.state === "connected", accountId: s.accountId || null,
      qrDataUrl: s.qrData, verificationRequired: s.state === "need_verifycode", challengeId: s.challengeId,
      restartRequired: s.state === "expired" || s.state === "failed", message: s.message,
    };
  }
  function find(classId, loginId) {
    const s = sessions.get(classId);
    if (!s || s.id !== loginId) throw stale();
    current(s);
    return s;
  }
  async function refreshQr(s, timeoutMs) {
    const qr = await requestQr({ signal: s.controller.signal, timeoutMs });
    if (!current(s)) return;
    if (typeof qr.qrcode !== "string" || !qr.qrcode || qr.qrcode.length > 4096 || typeof qr.qrcode_img_content !== "string"
        || !qr.qrcode_img_content || qr.qrcode_img_content.length > 4096) {
      throw new LoginError("WECHAT_QR_UNAVAILABLE", "微信未返回有效二维码，请重新生成。", 502);
    }
    s.qrToken = qr.qrcode; s.qrData = qr.qrcode_img_content; s.challengeId = null;
    s.state = "waiting"; s.message = "请使用微信扫描二维码。";
  }
  async function runPoll(s, timeoutMs, verifyCode) {
    try {
      const result = await pollQr({ qrToken: s.qrToken, verifyCode, signal: s.controller.signal,
        timeoutMs: Math.min(timeoutMs, Math.max(1, s.expiresAt - now())), baseUrl: s.baseUrl });
      verifyCode = null;
      if (!current(s)) return snapshot(s);
      switch (result.status) {
        case "wait":
        case "scaned":
          s.state = "waiting"; s.message = result.status === "scaned" ? "已扫码，请在微信中继续确认。" : "正在等待扫码。";
          break;
        case "need_verifycode":
          s.state = "need_verifycode"; s.challengeId = uuid();
          s.message = "请输入手机微信显示的数字验证码；若刚提交过，请核对后重新输入。";
          break;
        case "expired":
        case "verify_code_blocked":
          s.refreshes += 1;
          if (s.refreshes > 3 || s.attempts >= 5) finish(s, "failed", "本次验证次数过多，请稍后重新生成二维码。");
          else await refreshQr(s, Math.min(10_000, Math.max(1, s.expiresAt - now())));
          break;
        case "scaned_but_redirect":
          s.baseUrl = trustedWeixinUrl(`https://${result.redirect_host}`);
          s.message = "已扫码，正在继续确认。";
          break;
        case "confirmed": {
          if (!result.bot_token || typeof result.bot_token !== "string" || !result.ilink_bot_id || typeof result.ilink_bot_id !== "string") {
            finish(s, "failed", "微信登录响应缺少凭据或账号，未完成绑定，请重新生成二维码。");
            break;
          }
          // Synchronous credential commit: no await between generation check,
          // saving the account and marking this attempt complete.
          s.accountId = saveAccount({ classId: s.classId, accountId: result.ilink_bot_id,
            token: result.bot_token, baseUrl: trustedWeixinUrl(result.baseurl || s.baseUrl), userId: result.ilink_user_id });
          finish(s, "connected", "微信登录成功，正在配置班级消息路由。");
          break;
        }
        case "binded_redirect":
          finish(s, "failed", "微信提示已有连接，但未返回可校验的账号，请重新生成二维码。");
          break;
        default:
          throw new LoginError("WECHAT_RESPONSE_INVALID", "微信返回了未知登录状态，请稍后重试。", 502);
      }
    } catch (error) {
      verifyCode = null;
      if (!current(s)) return snapshot(s);
      if (error instanceof LoginError) finish(s, "failed", error.message);
      else { s.state = "waiting"; s.message = "暂未收到微信确认，正在继续等待。"; }
    }
    return snapshot(s);
  }
  return {
    async start({ classId, force = false, timeoutMs = 30_000, ttlMs = 300_000 }) {
      for (const [key, item] of sessions) {
        if (now() >= item.expiresAt) { item.controller.abort(); clearSecrets(item); clearTimeout(item.timer); sessions.delete(key); }
      }
      const old = sessions.get(classId);
      if (old && !force && !terminal.has(old.state)) {
        if (old.startJob) await old.startJob;
        return snapshot(old);
      }
      if (!old && sessions.size >= maxSessions) throw new LoginError("WECHAT_LOGIN_BUSY", "扫码会话过多，请稍后重试。", 503);
      if (old) { old.controller.abort(); clearSecrets(old); clearTimeout(old.timer); }
      const s = { classId, id: uuid(), state: "starting", expiresAt: now() + ttlMs, controller: new AbortController(),
        qrToken: null, qrData: null, challengeId: null, refreshes: 0, attempts: 0, baseUrl: "https://ilinkai.weixin.qq.com" };
      sessions.set(classId, s);
      s.timer = setTimeout(() => {
        if (sessions.get(classId) === s) { finish(s, "expired", "本次登录已过期，请重新生成二维码。"); sessions.delete(classId); }
      }, ttlMs);
      s.timer.unref?.();
      s.startJob = refreshQr(s, Math.min(timeoutMs, ttlMs));
      try { await s.startJob; }
      catch (error) {
        if (sessions.get(classId) !== s) throw stale();
        finish(s, "failed", "二维码生成失败，请稍后重试。");
        throw error instanceof LoginError ? error : new LoginError("WECHAT_QR_UNAVAILABLE", s.message, 503);
      } finally { s.startJob = null; }
      return snapshot(s);
    },
    async wait({ classId, loginId, timeoutMs = 15_000 }) {
      const s = find(classId, loginId);
      if (terminal.has(s.state) || s.state === "need_verifycode") return snapshot(s);
      if (s.startJob) await s.startJob;
      if (!current(s)) return snapshot(s);
      if (!s.job) s.job = runPoll(s, timeoutMs).finally(() => { s.job = null; });
      return s.job;
    },
    async verify({ classId, loginId, challengeId, code, timeoutMs = 15_000 }) {
      const s = find(classId, loginId);
      if (!/^[0-9]{1,12}$/.test(code || "")) throw new LoginError("WECHAT_CODE_INVALID", "请输入手机显示的数字验证码。", 422);
      if (s.state !== "need_verifycode" || s.challengeId !== challengeId || s.job) throw new LoginError("WECHAT_CHALLENGE_STALE", "验证码提示已更新，请使用当前提示。", 409);
      if (++s.attempts > 5) { finish(s, "failed", "验证码尝试次数过多，请重新生成二维码。"); return snapshot(s); }
      s.state = "verifying"; s.challengeId = null;
      s.job = runPoll(s, timeoutMs, code).finally(() => { s.job = null; });
      return s.job;
    },
    dispose() { for (const s of sessions.values()) { s.controller.abort(); clearTimeout(s.timer); clearSecrets(s); } sessions.clear(); },
  };
}

export function trustedWeixinUrl(value) {
  let url;
  try { url = new URL(value); } catch { throw new LoginError("WECHAT_RESPONSE_INVALID", "微信返回了无效服务地址。", 502); }
  if (url.protocol !== "https:" || url.username || url.password || url.port || url.search || url.hash
      || !url.hostname.endsWith(".weixin.qq.com") || url.pathname !== "/") {
    throw new LoginError("WECHAT_RESPONSE_INVALID", "微信返回了不受信任的服务地址。", 502);
  }
  return url.origin;
}
