import assert from "node:assert/strict";
import { test } from "node:test";
import { installDom, response, tick } from "./dom.mjs";

installDom();
const page = await import("../../web/js/pages/attendance.js");
const { state, saveSession } = await import("../../web/js/state.js");

test("daily attendance records and queries have no time selector or time column", async () => {
  installDom(); state.classId = "class-a"; state.students = [{ id: "student-a", student_no: "1", name: "甲" }]; saveSession("test-token");
  const calls = [];
  globalThis.fetch = async (path, options = {}) => { calls.push({ path, options }); return response([]); };
  const mount = document.createElement("main"); document.body.append(mount); await page.render(mount); await tick();
  assert.equal(mount.querySelectorAll("select").length, 0);
  assert.equal(mount.textContent.includes("时段"), false);
  mount.querySelector(".status-late").click();
  const save = mount.querySelectorAll("button").find((node) => node.textContent === "保存考勤");
  await save.onclick();
  const body = JSON.parse(calls.find(({ options }) => options.method === "PUT").options.body);
  assert.equal(body.status, "late"); assert.ok(body.attendance_date); assert.equal("period" in body, false);
  assert.ok(calls.every(({ path }) => !path.includes("period=")));
  mount.querySelector(".tabs").querySelectorAll("button")[1].click(); await tick();
  assert.equal(mount.querySelectorAll("select").length, 1);
  assert.equal(mount.textContent.includes("时段"), false);
});
