import test from "node:test";
import assert from "node:assert/strict";
import { api, setToken, onUnauthorized } from "../../web/js/api.js";

globalThis.window = { setTimeout, clearTimeout };

test("a late 401 from the old identity does not log out a newly signed-in account", async () => {
  let resolve;
  let loggedOut = 0;
  globalThis.fetch = () => new Promise((done) => { resolve = done; });
  onUnauthorized(() => { loggedOut += 1; });
  setToken("old-token");
  const oldRequest = api("/old-request");
  setToken("new-token");
  resolve(new Response("{}", { status: 401 }));
  await assert.rejects(oldRequest);
  assert.equal(loggedOut, 0);
  const currentRequest = api("/new-request");
  resolve(new Response("{}", { status: 401 }));
  await assert.rejects(currentRequest);
  assert.equal(loggedOut, 1);
});

test("a 401 from an unauthenticated request does not trigger the logout handler", async () => {
  let loggedOut = 0;
  onUnauthorized(() => { loggedOut += 1; });
  setToken(null);
  globalThis.fetch = () => Promise.resolve(new Response(
    JSON.stringify({ success: false, error: { code: "INVALID_PASSWORD", message: "密码错误，请检查密码" } }),
    { status: 401, headers: { "Content-Type": "application/json" } },
  ));
  await assert.rejects(api("/auth/login", { method: "POST", body: { username: "admin", password: "wrong" } }), (error) => error.code === "INVALID_PASSWORD");
  assert.equal(loggedOut, 0);
});

test("explicit cancellation uses the task's original credential even after identity changes", async () => {
  let cancellationToken;
  globalThis.fetch = (path, options) => {
    if (path.endsWith("/cancel")) {
      cancellationToken = options.headers.Authorization;
      return Promise.resolve(new Response("{}"));
    }
    return new Promise((_resolve, reject) => options.signal.addEventListener("abort", () => reject(new DOMException("stop", "AbortError"))));
  };
  const controller = new AbortController();
  setToken("task-owner-token");
  const running = api("/work", { signal: controller.signal, aiTaskId: "task-id" });
  setToken("new-owner-token");
  controller.abort();
  await assert.rejects(running, { code: "REQUEST_CANCELLED" });
  assert.equal(cancellationToken, "Bearer task-owner-token");
});
