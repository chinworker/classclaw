import assert from "node:assert/strict";
import { afterEach, beforeEach, test } from "node:test";
import { installDom, response, tick } from "./dom.mjs";
import { openExamImport } from "../../web/js/examImport.js";

let close;
const candidate = {
  id: 1, name: "期中", exam_date: "2026-11-01", source: "成绩.xlsx / 本班", header_row: 1,
  students: [{ id: "student-a", student_no: "1", name: "甲" }],
  subjects: [{ subject: "数学", full_score: 100, score_header: "数" }],
  scores: [{ student_id: "student-a", subject: "数学", score: 98, class_rank: 2, grade_rank: 16 }],
  totals: [], warnings: ["有 1 人未匹配"], errors: [], unmapped_columns: [], excluded_rows: 44,
};

beforeEach(() => installDom());
afterEach(() => close?.());

function startUpload() {
  const input = document.body.querySelector(".dropzone").querySelector("input");
  input.files = [new File(["sample"], "grades.xlsx")];
  input.dispatchEvent(new Event("change"));
  document.body.querySelector(".ai-button").click();
}

test("file preview displays original ranks and commits only the chosen data group", async (t) => {
  t.mock.timers.enable({ apis: ["setTimeout"] });
  const calls = [];
  const second = { ...candidate, id: 2, name: "月考" };
  const blocked = { ...candidate, id: 3, name: "有冲突", errors: ["第 3 行学生重复"] };
  globalThis.fetch = async (path, options) => {
    calls.push({ path, options });
    return path.endsWith("import-preview") ? response({ preview_id: "preview", datasets: [candidate, second, blocked], warnings: [] })
      : response({ exams: [{ id: "exam-a" }] }, 201);
  };
  let imported = 0;
  close = openExamImport("class-a", () => imported++);
  startUpload(); await tick();
  assert.equal(calls.length, 1);
  assert.equal(calls[0].options.body.get("files").name, "grades.xlsx");
  assert.ok(document.body.textContent.includes("年级第 16"));
  assert.ok(document.body.textContent.includes("第 3 行学生重复"));
  const cards = document.body.querySelectorAll(".import-panel");
  assert.equal(cards[2].querySelector("input").disabled, true);
  cards[1].querySelector("input").checked = true;
  document.body.querySelector("form").dispatchEvent(new Event("submit", { cancelable: true }));
  await tick();
  assert.equal(imported, 1);
  assert.deepEqual(JSON.parse(calls[1].options.body), {
    preview_id: "preview", datasets: [{ dataset_id: 2, name: "月考", exam_date: "2026-11-01", full_scores: { 数学: 100 } }],
  });
});

test("closing the import cancels the pending extraction and never commits", async () => {
  let signal;
  const calls = [];
  globalThis.fetch = async (path, options) => {
    calls.push(path);
    if (path.endsWith("import-preview")) {
      signal = options.signal;
      return new Promise((_resolve, reject) => {
        signal.addEventListener("abort", () => reject(new DOMException("Aborted", "AbortError")), { once: true });
      });
    }
    return response({});
  };
  close = openExamImport("class-a", () => assert.fail("must not save"));
  startUpload(); await tick();
  close(); await tick();
  assert.equal(signal.aborted, true);
  assert.equal(document.body.querySelector(".modal"), null);
  assert.equal(calls.filter((path) => path.endsWith("import-apply")).length, 0);
});

test("a names-only preview requires a subject but no extra roster checkbox", async (t) => {
  t.mock.timers.enable({ apis: ["setTimeout"] });
  const calls = [];
  const subject = "第 2 列成绩";
  const missing = {
    ...candidate, name_only: true,
    subjects: [{ subject, requires_subject: true, full_score: 100, score_header: "小测成绩" }],
    scores: [{ ...candidate.scores[0], subject }],
  };
  globalThis.fetch = async (path, options) => {
    calls.push({ path, options });
    return path.endsWith("import-preview") ? response({ preview_id: "preview", datasets: [missing], warnings: [] })
      : response({ exams: [{ id: "exam-a" }] }, 201);
  };
  close = openExamImport("class-a", () => {}, () => [{ name: "数学" }]);
  startUpload(); await tick();
  const form = document.body.querySelector("form");
  const submit = () => form.dispatchEvent(new Event("submit", { cancelable: true }));
  assert.ok(form.querySelector("select").textContent.includes("请选择科目"));
  assert.equal(form.querySelectorAll("input").filter((input) => input.getAttribute("type") === "checkbox").length, 1);
  submit(); await tick();
  assert.equal(calls.length, 1);
  form.querySelector("select").value = "数学";
  submit(); await tick();
  assert.deepEqual(JSON.parse(calls[1].options.body).datasets[0], {
    dataset_id: 1, name: "期中", exam_date: "2026-11-01", full_scores: { [subject]: 100 },
    subject_names: { [subject]: "数学" },
  });
});
