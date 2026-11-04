import assert from "node:assert/strict";
import { test } from "node:test";
import { readFile } from "node:fs/promises";
import { createPageLoader } from "../../web/js/pageLoader.js";

const path = "https://classclaw.test/app/js/pages/exams.js?v=existing";

test("successful page modules retain their stable URL on subsequent navigation", async () => {
  const calls = [];
  const module = { render() {} };
  const load = createPageLoader(path, async (url) => { calls.push(url); return module; });
  assert.equal(await load(), module); assert.equal(await load(), module);
  assert.deepEqual(calls, [path, path]);
});

for (const message of ["Importing a module script failed.", "Failed to fetch dynamically imported module: example", "error loading dynamically imported module: example"]) {
  test(`a transient browser module failure retries once: ${message}`, async () => {
    const calls = []; const module = {};
    const load = createPageLoader(path, async (url) => {
      calls.push(url);
      if (calls.length === 1) throw new TypeError(message);
      return module;
    });
    assert.equal(await load(), module);
    assert.equal(calls.length, 2);
    const retried = new URL(calls[1]);
    assert.equal(retried.pathname, new URL(path).pathname);
    assert.equal(retried.searchParams.get("v"), "existing");
    assert.ok(retried.searchParams.get("_module_retry"));
    assert.equal(await load(), module);
    assert.equal(calls[2], calls[1]);
  });
}

test("persistent failure stops after one retry, and manual retry uses another fresh URL", async () => {
  const calls = []; let fail = true;
  const load = createPageLoader(path, async (url) => {
    calls.push(url);
    if (fail) throw new TypeError("Importing a module script failed.");
    return {};
  });
  await assert.rejects(load, /Importing/);
  assert.equal(calls.length, 2);
  fail = false; await load();
  assert.equal(calls.length, 3);
  assert.equal(new Set(calls).size, 3);
});

test("module execution errors are not automatically re-executed", async () => {
  let calls = 0;
  const load = createPageLoader(path, async () => { calls++; throw new ReferenceError("missingVariable is not defined"); });
  await assert.rejects(load, ReferenceError);
  assert.equal(calls, 1);
});

test("all lazy routes use the recoverable loader and the failure screen offers retry", async () => {
  const source = await readFile(new URL("../../web/app.js", import.meta.url), "utf8");
  assert.equal(source.includes("loader: () => import("), false);
  assert.ok(source.includes('pageLoader("./js/pages/exams.js")'));
  assert.ok(source.includes('pageLoader("./js/pages/attendance.js")'));
  assert.ok(source.includes('onclick: () => dispatch() }, "重试打开页面"'));
  assert.ok(source.includes('onclick: () => location.reload() }, "重新加载工作台"'));
});
