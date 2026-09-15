import test from "node:test";
import assert from "node:assert/strict";
import { createWebLogin, LoginError, trustedWeixinUrl } from "./web-login.js";

function fixture(t, overrides = {}) {
  let time = 0, generated = 0;
  const polls = [], saved = [];
  const engine = createWebLogin({
    now: () => time,
    requestQr: async () => ({ qrcode: `SECRET-${++generated}`, qrcode_img_content: `weixin://qr-${generated}` }),
    pollQr: async (options) => { polls.push(options); return { status: "wait" }; },
    saveAccount: (value) => { saved.push(value); return "normalized-bot"; },
    ...overrides,
  });
  t.after(() => engine.dispose());
  return { engine, polls, saved, advance: (ms) => { time += ms; }, generated: () => generated };
}
const deferred = () => { let resolve; const promise = new Promise((r) => { resolve = r; }); return { promise, resolve }; };

test("short wait timeout preserves the same login and QR for the entire TTL", async (t) => {
  const f = fixture(t, { pollQr: async () => { throw new DOMException("timeout", "AbortError"); } });
  const start = await f.engine.start({ classId: "a" });
  for (let i = 0; i < 10; i++) {
    f.advance(15000);
    const result = await f.engine.wait({ classId: "a", loginId: start.loginId });
    assert.equal(result.loginId, start.loginId); assert.equal(result.qrDataUrl, start.qrDataUrl);
    assert.equal(result.restartRequired, false);
  }
  assert.equal(f.generated(), 1);
});

test("an expired QR is refreshed and returned in the very same wait response", async (t) => {
  const f = fixture(t, { pollQr: async () => ({ status: "expired" }) });
  const old = await f.engine.start({ classId: "a" });
  const next = await f.engine.wait({ classId: "a", loginId: old.loginId });
  assert.equal(next.loginId, old.loginId); assert.notEqual(next.qrDataUrl, old.qrDataUrl);
  assert.equal(next.restartRequired, false);
});

test("regeneration aborts an old wait; its late success cannot save credentials or delete the new session", async (t) => {
  const pending = deferred(); let signal;
  const f = fixture(t, { pollQr: (options) => { signal = options.signal; return pending.promise; } });
  const first = await f.engine.start({ classId: "a" });
  const waiting = f.engine.wait({ classId: "a", loginId: first.loginId });
  const rejected = assert.rejects(waiting, { code: "WECHAT_LOGIN_STALE" });
  const second = await f.engine.start({ classId: "a", force: true });
  assert.equal(signal.aborted, true);
  pending.resolve({ status: "confirmed", bot_token: "SECRET", ilink_bot_id: "bot" });
  await rejected;
  assert.equal(f.saved.length, 0);
  assert.equal((await f.engine.start({ classId: "a" })).loginId, second.loginId);
});

test("a late QR generation response cannot replace a newer attempt", async (t) => {
  const pending = deferred(); let count = 0;
  const f = fixture(t, { requestQr: () => ++count === 1 ? pending.promise : Promise.resolve({ qrcode: "b", qrcode_img_content: "weixin://b" }) });
  const first = f.engine.start({ classId: "a" });
  const rejected = assert.rejects(first, { code: "WECHAT_LOGIN_STALE" });
  const second = await f.engine.start({ classId: "a", force: true });
  pending.resolve({ qrcode: "a", qrcode_img_content: "weixin://a" });
  await rejected;
  assert.equal((await f.engine.start({ classId: "a" })).qrDataUrl, second.qrDataUrl);
});

test("parallel waits share one provider request and one credential save", async (t) => {
  const pending = deferred(); let count = 0;
  const f = fixture(t, { pollQr: () => { count++; return pending.promise; } });
  const start = await f.engine.start({ classId: "a" });
  const one = f.engine.wait({ classId: "a", loginId: start.loginId });
  const two = f.engine.wait({ classId: "a", loginId: start.loginId });
  assert.equal(count, 1);
  pending.resolve({ status: "confirmed", bot_token: "SECRET-TOKEN", ilink_bot_id: "raw-bot" });
  const result = await one; await two;
  assert.equal(f.saved.length, 1); assert.equal(result.accountId, "normalized-bot");
  assert.equal(result.connected, true); assert.equal(result.qrDataUrl, null);
  assert.equal(JSON.stringify(result).includes("SECRET"), false);
});

test("verification is a structured challenge; leading zero code is submitted once and never returned", async (t) => {
  const values = [];
  const f = fixture(t, { pollQr: async ({ verifyCode }) => {
    values.push(verifyCode);
    return verifyCode ? { status: "confirmed", bot_token: "token", ilink_bot_id: "bot" } : { status: "need_verifycode" };
  } });
  const start = await f.engine.start({ classId: "a" });
  const prompt = await f.engine.wait({ classId: "a", loginId: start.loginId });
  assert.equal(prompt.verificationRequired, true); assert.ok(prompt.challengeId);
  assert.equal((await f.engine.wait({ classId: "a", loginId: start.loginId })).challengeId, prompt.challengeId);
  assert.equal(values.length, 1);
  const result = await f.engine.verify({ classId: "a", loginId: start.loginId, challengeId: prompt.challengeId, code: "00123" });
  assert.equal(result.connected, true); assert.deepEqual(values, [undefined, "00123"]);
  assert.equal(JSON.stringify(result).includes("00123"), false);
});

test("incorrect code rotates the challenge; old challenges and non-digits are rejected", async (t) => {
  const f = fixture(t, { pollQr: async () => ({ status: "need_verifycode" }) });
  const start = await f.engine.start({ classId: "a" });
  const prompt = await f.engine.wait({ classId: "a", loginId: start.loginId });
  const params = { classId: "a", loginId: start.loginId, challengeId: prompt.challengeId };
  await assert.rejects(f.engine.verify({ ...params, code: "abc" }), { code: "WECHAT_CODE_INVALID" });
  const retry = await f.engine.verify({ ...params, code: "123" });
  assert.notEqual(retry.challengeId, prompt.challengeId);
  await assert.rejects(f.engine.verify({ ...params, code: "123" }), { code: "WECHAT_CHALLENGE_STALE" });
});

test("verification attempts and QR refreshes are bounded", async (t) => {
  const f = fixture(t, { pollQr: async () => ({ status: "need_verifycode" }) });
  const start = await f.engine.start({ classId: "a" });
  let prompt = await f.engine.wait({ classId: "a", loginId: start.loginId });
  for (let n = 0; n < 6; n++) prompt = await f.engine.verify({ classId: "a", loginId: start.loginId, challengeId: prompt.challengeId, code: "123" });
  assert.equal(prompt.restartRequired, true);
  const r = fixture(t, { pollQr: async () => ({ status: "expired" }) });
  const initial = await r.engine.start({ classId: "a" });
  let latest;
  for (let n = 0; n < 4; n++) latest = await r.engine.wait({ classId: "a", loginId: initial.loginId });
  assert.equal(latest.restartRequired, true);
});

test("class and login IDs are independently scoped", async (t) => {
  const f = fixture(t);
  const a = await f.engine.start({ classId: "a" });
  const b = await f.engine.start({ classId: "b" });
  await assert.rejects(f.engine.wait({ classId: "b", loginId: a.loginId }), { code: "WECHAT_LOGIN_STALE" });
  assert.equal((await f.engine.wait({ classId: "b", loginId: b.loginId })).loginId, b.loginId);
});

test("TTL expires even while waiting for verification", async (t) => {
  const f = fixture(t, { pollQr: async () => ({ status: "need_verifycode" }) });
  const start = await f.engine.start({ classId: "a" });
  await f.engine.wait({ classId: "a", loginId: start.loginId });
  f.advance(300000);
  const result = await f.engine.wait({ classId: "a", loginId: start.loginId });
  assert.equal(result.restartRequired, true); assert.equal(result.qrDataUrl, null); assert.equal(result.challengeId, null);
});

for (const status of [{ status: "confirmed", ilink_bot_id: "bot" }, { status: "binded_redirect" }, { status: "unexpected" }]) {
  test(`invalid or ambiguous confirmation is never successful: ${JSON.stringify(status)}`, async (t) => {
    const f = fixture(t, { pollQr: async () => status });
    const start = await f.engine.start({ classId: "a" });
    const result = await f.engine.wait({ classId: "a", loginId: start.loginId });
    assert.equal(result.connected, false); assert.equal(result.restartRequired, true); assert.equal(f.saved.length, 0);
  });
}

test("credential save failure cannot report connection success", async (t) => {
  const f = fixture(t, { pollQr: async () => ({ status: "confirmed", bot_token: "secret", ilink_bot_id: "bot" }),
    saveAccount: () => { throw new LoginError("WECHAT_CREDENTIAL_SAVE_FAILED", "保存失败", 503); } });
  const start = await f.engine.start({ classId: "a" });
  const result = await f.engine.wait({ classId: "a", loginId: start.loginId });
  assert.equal(result.connected, false); assert.equal(result.restartRequired, true);
});

test("provider-controlled redirects must stay on HTTPS Weixin hosts", () => {
  assert.equal(trustedWeixinUrl("https://ilinkai.weixin.qq.com"), "https://ilinkai.weixin.qq.com");
  for (const url of ["http://ilinkai.weixin.qq.com", "https://evil.test", "https://weixin.qq.com.evil.test", "https://u:p@ilinkai.weixin.qq.com", "https://ilinkai.weixin.qq.com/path"]) {
    assert.throws(() => trustedWeixinUrl(url), { code: "WECHAT_RESPONSE_INVALID" });
  }
});
