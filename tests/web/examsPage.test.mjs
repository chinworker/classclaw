import assert from "node:assert/strict";
import { afterEach, beforeEach, test } from "node:test";
import { installDom, response, tick } from "./dom.mjs";

installDom();
const page = await import("../../web/js/pages/exams.js");
const { state, saveSession } = await import("../../web/js/state.js");
const exam = { id: "exam-a", name: "期中考试", exam_date: "2026-11-01", status: "draft",
  subjects: [{ subject: "数学", full_score: 100 }] };
const button = (host, text) => host.querySelectorAll("button").find((node) => node.textContent === text);

beforeEach(() => {
  page.dispose(); installDom(); state.classId = "class-a"; saveSession("test-token");
});
afterEach(() => page.dispose());

function mountPage() {
  const mount = document.createElement("main"); document.body.append(mount);
  return { mount, rendered: page.render(mount) };
}

test("exam list renders while the independent subjects request is pending", async () => {
  let finishSubjects;
  globalThis.fetch = async (path) => path.endsWith("/subjects")
    ? new Promise((resolve) => { finishSubjects = resolve; }) : response([exam]);
  const { mount, rendered } = mountPage(); await tick();
  assert.ok(mount.textContent.includes("成绩与考试"));
  assert.ok(mount.textContent.includes(exam.name));
  assert.equal(button(mount, "创建考试").disabled, true);
  assert.ok(button(mount, "录入/查看成绩"));
  finishSubjects(response([{ name: "数学" }])); await rendered;
  assert.equal(button(mount, "创建考试").disabled, false);
  button(mount, "创建考试").click();
  assert.ok(document.body.querySelector(".modal").textContent.includes("数学"));
});

test("subject failure is visible and retryable without hiding the exam list", async () => {
  let fail = true;
  globalThis.fetch = async (path) => path.endsWith("/subjects")
    ? fail ? response(null, 503) : response([{ name: "数学" }]) : response([exam]);
  const { mount, rendered } = mountPage(); await rendered;
  assert.ok(mount.textContent.includes(exam.name));
  assert.ok(mount.textContent.includes("创建考试前请重试加载科目"));
  assert.equal(button(mount, "创建考试").disabled, true);
  fail = false; button(mount, "重试").click(); await tick();
  assert.equal(button(mount, "创建考试").disabled, false);
  assert.equal(mount.querySelector(".error-panel"), null);
});

test("exam list failure can be retried independently of subjects", async () => {
  let fail = true;
  globalThis.fetch = async (path) => path.endsWith("/subjects") ? response([{ name: "数学" }])
    : fail ? response(null, 503) : response([]);
  const { mount, rendered } = mountPage(); await rendered;
  assert.equal(button(mount, "创建考试").disabled, false);
  assert.ok(mount.querySelector(".error-panel"));
  fail = false; button(mount, "重试").click(); await tick();
  assert.ok(mount.textContent.includes("暂无考试"));
  assert.equal(mount.querySelector(".error-panel"), null);
});

test("stalled reads time out with a useful retry action", async (t) => {
  t.mock.timers.enable({ apis: ["setTimeout"] });
  window.setTimeout = setTimeout; window.clearTimeout = clearTimeout;
  globalThis.fetch = async (_path, { signal }) => new Promise((_resolve, reject) => {
    signal.addEventListener("abort", () => reject(new DOMException("Aborted", "AbortError")), { once: true });
  });
  const { mount, rendered } = mountPage();
  assert.ok(mount.querySelector(".skeleton-box"));
  t.mock.timers.tick(15_000); await rendered;
  assert.ok(mount.textContent.includes("加载超时，请重试"));
  assert.equal(mount.querySelectorAll(".error-panel").length, 2);
  assert.equal(mount.querySelector(".skeleton-box"), null);
});

test("leaving cancels page reads and ignores late responses from a previous class", async () => {
  const pending = [];
  globalThis.fetch = async (path, { signal }) => new Promise((resolve) => { pending.push({ path, signal, resolve }); });
  const { mount, rendered } = mountPage(); page.dispose(); state.classId = "class-b";
  assert.ok(pending.every(({ signal }) => signal.aborted));
  for (const request of pending) request.resolve(response(request.path.endsWith("/subjects") ? [{ name: "数学" }] : [exam]));
  await rendered;
  assert.equal(mount.textContent.includes(exam.name), false);
  assert.equal(button(mount, "创建考试").disabled, true);
});

test("deleting an exam only requires a confirmation click", async (t) => {
  t.mock.timers.enable({ apis: ["setTimeout"] });
  const deleted = [];
  globalThis.fetch = async (path, options) => {
    if (options.method === "DELETE") {
      deleted.push(path);
      return response({});
    }
    return response(path.endsWith("/subjects") ? [{ name: "数学" }] : [exam]);
  };
  const { mount, rendered } = mountPage(); await rendered;
  button(mount, "删除").click();
  const modal = document.body.querySelector(".modal");
  assert.ok(modal.textContent.includes(exam.name));
  assert.equal(modal.querySelector("input"), null);
  assert.deepEqual(deleted, []);
  button(modal, "确认删除").click(); await tick();
  assert.deepEqual(deleted, [`/api/v1/exams/${exam.id}`]);
});
