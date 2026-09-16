import test from "node:test";
import assert from "node:assert/strict";
import { compareStudentNumbers } from "../../web/js/studentOrder.js";
import { installDom, response } from "./dom.mjs";

const numbers = ["10", "2", "1", "02", "３", "A10", "A2", "100000000000000000002", "100000000000000000001"];
const expected = ["1", "2", "02", "３", "10", "100000000000000000001", "100000000000000000002", "A2", "A10"];

test("student numbers use numeric ordering including full-width and long identifiers", () => {
  assert.deepEqual([...numbers].sort(compareStudentNumbers), expected);
});

test("shared student cache supplies ascending order to forms and selectors", async () => {
  installDom();
  const { state, refreshStudents } = await import("../../web/js/state.js");
  state.classId = "class-a";
  globalThis.fetch = async () => response({ items: numbers.map((student_no) => ({ id: student_no, student_no })) });
  const students = await refreshStudents({ force: true });
  assert.deepEqual(students.map((student) => student.student_no), expected);
});

test("seating pool sorts the roster without rearranging assigned seats or the source array", async () => {
  installDom();
  const { seatMapEditor } = await import("../../web/js/seatmap.js");
  const students = numbers.map((student_no) => ({ id: student_no, student_no, name: student_no }));
  const editor = seatMapEditor({ students, rows: 1, cols: 2, layout: [["10", "2"]] });
  assert.deepEqual(students.map((s) => s.student_no), numbers);
  assert.deepEqual(editor.getLayout().layout, [["10", "2"]]);
  assert.deepEqual(editor.el.querySelectorAll(".pool-chip").map((node) => node.querySelector("small").textContent.trim()), expected.filter((no) => no !== "10" && no !== "2"));
});

test("student preview rows use ascending order without mutating the write proposal", async () => {
  installDom();
  const { proposalReview } = await import("../../web/js/components.js");
  const students = ["10", "2", "1"].map((student_no) => ({ student_no, name: `学生${student_no}` }));
  const view = proposalReview({ preview_json: { students } });
  assert.deepEqual(view.querySelector("tbody").querySelectorAll("tr").map((row) => row.querySelector("td").textContent), ["1", "2", "10"]);
  assert.deepEqual(students.map((student) => student.student_no), ["10", "2", "1"]);
});
