import test from "node:test";
import assert from "node:assert/strict";
import { Readable } from "node:stream";
import { readFile } from "node:fs/promises";
import { validateLoginRequest, webLoginHandler } from "./web-route.js";
import { createWechatTransport } from "./web-transport.js";
import { createWebLogin } from "./web-login.js";

const classId = "11111111-1111-1111-1111-111111111111";
const loginId = "22222222-2222-2222-2222-222222222222";
const config = { plugins: { entries: { classclaw: { config: { agentClasses: { agent: classId } } } } } };
const body = { action: "start", classId, force: true, timeoutMs: 15000, ttlMs: 300000 };

test("private route accepts only bound class actions, opaque IDs, bounded timeouts and numeric codes", () => {
  assert.deepEqual(validateLoginRequest(body, config), body);
  assert.ok(validateLoginRequest({ action: "verify", classId, loginId, challengeId: loginId, code: "0012", timeoutMs: 15000 }, config));
  for (const input of [null, [], { ...body, action: "config.patch" }, { ...body, url: "https://evil.test" },
    { ...body, classId: loginId }, { ...body, timeoutMs: 9999999 }, { ...body, ttlMs: 1 },
    { action: "wait", classId, timeoutMs: 15000 }, { action: "verify", classId, loginId, challengeId: loginId, code: "password", timeoutMs: 15000 }]) {
    assert.throws(() => validateLoginRequest(input, config));
  }
});

async function invoke(handler, body, method = "POST") {
  const req = Readable.from([typeof body === "string" ? body : JSON.stringify(body)]); req.method = method;
  const headers = {};
  const res = { setHeader: (k, v) => { headers[k] = v; }, end(value) { this.body = JSON.parse(value); } };
  await handler(req, res);
  return { ...res, headers };
}

test("route rejects oversized / malformed input, disallows GET and sanitizes unexpected failures", async () => {
  let calls = 0;
  const handler = webLoginHandler({ start: async () => { calls++; throw new Error("SECRET provider QR and code"); } }, () => config);
  assert.equal((await invoke(handler, body, "GET")).statusCode, 405);
  assert.equal((await invoke(handler, "invalid-json")).statusCode, 400);
  assert.equal((await invoke(handler, "x".repeat(4097))).statusCode, 413);
  assert.equal(calls, 0);
  const result = await invoke(handler, body);
  assert.equal(result.statusCode, 503); assert.equal(JSON.stringify(result.body).includes("SECRET"), false);
  assert.equal(result.headers["Cache-Control"], "no-store");
});

test("route reloads class bindings on every request and is registered behind Gateway authentication", async () => {
  let cfg = config;
  const handler = webLoginHandler({ start: async () => ({ loginId }) }, () => cfg);
  assert.equal((await invoke(handler, body)).statusCode, 200);
  cfg = {};
  assert.equal((await invoke(handler, body)).statusCode, 400);
  const source = await readFile(new URL("./index.js", import.meta.url), "utf8");
  assert.match(source, /path: "\/api\/v1\/classclaw\/wechat-login", auth: "gateway", match: "exact"/);
  assert.match(source, /gatewayRuntimeScopeSurface: "trusted-operator"/);
  assert.doesNotMatch(source, /clearStaleAccountsForUserId/);
});

test("transport uses the pinned protocol without exporting other accounts' tokens or following redirects", async () => {
  const calls = [];
  const transport = createWechatTransport({ fetchImpl: async (url, options) => {
    calls.push({ url, options });
    return new Response(JSON.stringify(calls.length === 1 ? { qrcode: "secret", qrcode_img_content: "weixin://qr" } : { status: "wait" }));
  } });
  await transport.requestQr({ timeoutMs: 1000 });
  await transport.pollQr({ baseUrl: "https://ilinkai.weixin.qq.com", qrToken: "secret", verifyCode: "0012", timeoutMs: 1000 });
  assert.deepEqual(JSON.parse(calls[0].options.body), { local_token_list: [] });
  assert.equal(calls[0].options.headers["iLink-App-ClientVersion"], String(0x020406));
  assert.equal(calls[0].options.redirect, "error");
  assert.equal(calls[1].url.searchParams.get("verify_code"), "0012");
  assert.equal(calls[1].url.hostname, "ilinkai.weixin.qq.com");
});

test("whole-body timeout still applies after HTTP headers arrive", async (t) => {
  t.mock.timers.enable({ apis: ["setTimeout"] });
  const transport = createWechatTransport({ fetchImpl: async (_url, options) => new Response(new ReadableStream({
    start(controller) { options.signal.addEventListener("abort", () => controller.error(new DOMException("aborted", "AbortError"))); },
  })) });
  const waiting = transport.requestQr({ timeoutMs: 15 });
  const rejected = assert.rejects(waiting, { name: "AbortError" });
  await new Promise(setImmediate);
  t.mock.timers.tick(15);
  await rejected;
});

test("transport rejects an oversized or unsuccessful provider response", async () => {
  for (const content of ["x".repeat(1024 * 1024 + 1), JSON.stringify({ ret: -1, errmsg: "provider error" })]) {
    const transport = createWechatTransport({ fetchImpl: async () => new Response(content) });
    await assert.rejects(transport.requestQr({ timeoutMs: 1000 }));
  }
});

test("browser route completes refreshed QR and phone verification through the real transport with mocked HTTP", async (t) => {
  let qrCount = 0; let pollCount = 0;
  const saved = [];
  const transport = createWechatTransport({ fetchImpl: async (url) => {
    if (url.pathname.endsWith("/get_bot_qrcode")) {
      qrCount++;
      return new Response(JSON.stringify({ qrcode: `token-${qrCount}`, qrcode_img_content: `weixin://qr-${qrCount}` }));
    }
    pollCount++;
    if (pollCount === 1) throw new DOMException("mock poll timeout", "AbortError");
    if (pollCount === 2) return new Response(JSON.stringify({ status: "expired" }));
    assert.equal(url.searchParams.get("qrcode"), "token-2");
    if (pollCount === 3) return new Response(JSON.stringify({ status: "need_verifycode" }));
    assert.equal(url.searchParams.get("verify_code"), "001234");
    return new Response(JSON.stringify({ status: "confirmed", bot_token: "credential-secret", ilink_bot_id: "bot-id" }));
  } });
  const engine = createWebLogin({ ...transport, saveAccount: (value) => { saved.push(value); return value.accountId; } });
  t.after(() => engine.dispose());
  const handler = webLoginHandler(engine, () => config);
  const started = await invoke(handler, body);
  assert.equal(started.statusCode, 200);
  const currentLogin = started.body.payload.loginId;
  const waitBody = { action: "wait", classId, loginId: currentLogin, timeoutMs: 15000 };
  const waiting = await invoke(handler, waitBody);
  assert.equal(waiting.body.payload.state, "waiting");
  assert.equal(waiting.body.payload.qrDataUrl, "weixin://qr-1");
  const refreshed = await invoke(handler, waitBody);
  assert.equal(refreshed.body.payload.qrDataUrl, "weixin://qr-2");
  assert.equal(refreshed.body.payload.loginId, currentLogin);
  const challenge = await invoke(handler, waitBody);
  assert.equal(challenge.body.payload.verificationRequired, true);
  const verified = await invoke(handler, { ...waitBody, action: "verify", challengeId: challenge.body.payload.challengeId, code: "001234" });
  assert.equal(verified.body.payload.connected, true);
  assert.equal(verified.body.payload.qrDataUrl, null);
  assert.equal(verified.body.payload.challengeId, null);
  assert.equal(saved.length, 1);
  assert.equal(saved[0].classId, classId);
  assert.equal(saved[0].token, "credential-secret");
  assert.doesNotMatch(JSON.stringify(verified.body), /001234|credential-secret|token-2/);
});
