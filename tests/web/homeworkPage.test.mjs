import test from "node:test";
import assert from "node:assert/strict";
import { findButton, installDom, response, tick } from "./dom.mjs";

test("opening homework loads saved values and sends only changed rows and fields", async (t) => {
  const document = installDom();
  const { render } = await import("../../web/js/pages/homework.js");
  const { state } = await import("../../web/js/state.js");
  state.classId = "class-a";
  state.subjects = [{ name: "语文" }];
  state.students = [{ id: "s1", student_no: "1", name: "甲" }, { id: "s2", student_no: "2", name: "乙" }];
  let statuses = [
    { student_id: "s1", status: "submitted", score: 90, comment: "已核对" },
    { student_id: "s2", status: "missing", score: null, comment: "备注" },
  ];
  const writes = [];
  t.mock.method(globalThis, "fetch", async (path, options) => {
    const { pathname } = new URL(path, "https://example.test");
    if (pathname.endsWith("/summary")) return response({ status_counts: { submitted: 1, missing: 1 }, completion_rate: 0.5 });
    if (pathname.endsWith("/students")) {
      if (options.method === "PUT") {
        const { items } = JSON.parse(options.body);
        writes.push(items);
        statuses = statuses.map((row) => ({ ...row, ...items.find((item) => item.student_id === row.student_id) }));
      }
      return response(statuses);
    }
    assert.equal(pathname, "/api/v1/homework");
    return response([{ id: "hw", title: "练习", subject: "语文", status: "published", assigned_date: "2026-09-01" }]);
  });

  const mount = document.createElement("main");
  document.body.append(mount);
  await render(mount);
  findButton(document.body, "收缴明细").click();
  await tick();
  const drawer = document.body.querySelector(".drawer");
  const rows = drawer.querySelector("tbody").querySelectorAll("tr");
  assert.deepEqual(rows.map((row) => row.querySelector("select").value), ["submitted", "missing"]);
  assert.deepEqual(rows[0].querySelectorAll("input").map((input) => input.value), [90, "已核对"]);
  assert.equal(findButton(document.body, "保存修改").disabled, true);
  assert.deepEqual(writes, []);

  const status = rows[1].querySelector("select");
  status.value = "late";
  status.dispatchEvent(new Event("change"));
  const comment = rows[1].querySelectorAll("input")[1];
  comment.value = "";
  comment.dispatchEvent(new Event("input"));
  findButton(document.body, "保存修改").click();
  await tick();
  assert.deepEqual(writes, [[{ student_id: "s2", status: "late", comment: null }]]);
  assert.equal(findButton(document.body, "保存修改").disabled, true);
});
