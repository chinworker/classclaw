import test, { beforeEach } from "node:test";
import assert from "node:assert/strict";
import { api, setToken } from "../../web/js/api.js";

globalThis.window = { setTimeout, clearTimeout };
const encoder = new TextEncoder();
const frame = (event, data) => `event: ${event}\r\ndata: ${JSON.stringify(data)}\r\n\r\n`;
const tick = () => new Promise((resolve) => setImmediate(resolve));
beforeEach(() => setToken("original-token"));

function endpoint() {
  const calls = [];
  let controller;
  globalThis.fetch = async (url, options) => {
    calls.push({ url, options });
    if (url.endsWith("/cancel")) return new Response(JSON.stringify({ success: true, data: {} }));
    return new Response(new ReadableStream({
      start(value) {
        controller = value;
        options.signal?.addEventListener("abort", () => value.error(new DOMException("aborted", "AbortError")));
      },
    }), { headers: { "Content-Type": "text/event-stream", "X-Request-ID": "request-stream" } });
  };
  return { calls, send: (text) => controller.enqueue(encoder.encode(text)), bytes: (value) => controller.enqueue(value), close: () => controller.close() };
}

test("SSE handles fragmented UTF-8, CRLF, heartbeats and delivers text before completion", async () => {
  const server = endpoint();
  const deltas = [];
  let completed = false;
  const run = api("/chat", { onDelta: (text) => deltas.push(text), timeoutMs: 1000 }).then((result) => { completed = true; return result; });
  await tick();
  const encoded = encoder.encode(": keepalive\r\n\r\n" + frame("delta", { success: true, data: { text: "你好" } }));
  for (const byte of encoded) server.bytes(new Uint8Array([byte]));
  await tick();
  assert.deepEqual(deltas, ["你好"]);
  assert.equal(completed, false);
  server.send(frame("done", { success: true, data: { reply: "你好，完整回复" } }));
  assert.equal((await run).reply, "你好，完整回复");
});

test("EOF is not success and structured late errors retain request IDs", async () => {
  let server = endpoint();
  const deltas = [];
  let run = api("/chat", { onDelta: (text) => deltas.push(text) });
  const rejected = assert.rejects(run, (error) => error.code === "STREAM_INTERRUPTED" && error.requestId === "request-stream");
  await tick();
  server.send(frame("delta", { success: true, data: { text: "部分回复" } }));
  server.close();
  await rejected;
  assert.deepEqual(deltas, ["部分回复"]);
  server = endpoint();
  run = api("/chat", { onDelta() {} });
  const errorResult = assert.rejects(run, (error) => error.code === "OPENCLAW_TIMEOUT" && error.requestId === "late-error");
  await tick();
  server.send(frame("error", { success: false, error: { code: "OPENCLAW_TIMEOUT", message: "请核对结果" }, request_id: "late-error" }));
  await errorResult;
});

test("Stop after response headers still aborts the body and cancels under the original identity", async () => {
  const server = endpoint();
  const controller = new AbortController();
  const run = api("/chat", { onDelta() {}, signal: controller.signal, aiTaskId: "task-1" });
  const stopped = assert.rejects(run, (error) => error.code === "REQUEST_CANCELLED");
  await tick();
  setToken("new-token");
  controller.abort();
  await stopped;
  assert.equal(server.calls.length, 2);
  assert.equal(server.calls[1].options.headers.Authorization, "Bearer original-token");
});

test("the timeout remains active while waiting for streamed body data", async () => {
  const server = endpoint();
  const run = api("/chat", { onDelta() {}, timeoutMs: 20, aiTaskId: "timeout-task" });
  await assert.rejects(run, (error) => error.code === "REQUEST_TIMEOUT");
  assert.equal(server.calls.filter((call) => call.url.endsWith("/cancel")).length, 1);
});

test("a buffered JSON response remains compatible", async () => {
  globalThis.fetch = async () => new Response(JSON.stringify({ success: true, data: { reply: "完整回复" } }), { headers: { "Content-Type": "application/json" } });
  const value = await api("/chat", { onDelta() { assert.fail("JSON must not be a delta"); } });
  assert.equal(value.reply, "完整回复");
});
