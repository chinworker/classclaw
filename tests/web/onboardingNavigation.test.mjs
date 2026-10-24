import assert from "node:assert/strict";
import { afterEach, test } from "node:test";
import { installDom, response, tick } from "./dom.mjs";

installDom();
const page = await import("../../web/js/pages/onboarding.js");
const { state, saveSession } = await import("../../web/js/state.js");
afterEach(() => page.dispose());
const button = (mount) => mount.querySelectorAll("button").find((node) => node.textContent === "保存并下一步");
async function setup(patch) {
  installDom(); state.user = { id: "teacher-a", username: "teacher", role: "head_teacher" }; saveSession("test-token");
  const session = { id: "draft-a", revision: 1, status: "draft", current_step: "class_info", draft_json: { class_info: {} } };
  globalThis.fetch = async (_path, options = {}) => options.method === "PATCH" ? patch(options, session) : response(session);
  const mount = document.createElement("main"); document.body.append(mount);
  await page.render(mount, { params: { id: "draft-a" } }, {});
  const inputs = mount.querySelectorAll("input"); inputs[0].value = "高一三班"; inputs[1].value = "高一";
  mount.querySelector(".ob-panel").dispatchEvent(new Event("input"));
  return mount;
}

test("one action saves the current draft and advances, even when the step has unsaved edits", async () => {
  let body;
  const mount = await setup((options, session) => {
    body = JSON.parse(options.body);
    return response({ ...session, revision: 2, current_step: body.current_step, draft_json: body.draft_patch });
  });
  assert.equal(button(mount).disabled, false);
  button(mount).click(); await tick();
  assert.equal(body.expected_revision, 1); assert.equal(body.current_step, "students");
  assert.equal(body.draft_patch.class_info.name, "高一三班");
  assert.ok(mount.textContent.includes("上传文件生成名单"));
  assert.equal(mount.querySelectorAll("button").some((node) => node.textContent === "保存草稿"), false);
});

test("a failed save stays on the same step and preserves the form for retry", async () => {
  const mount = await setup(() => new Response(JSON.stringify({ success: false, error: { code: "UNAVAILABLE", message: "暂时无法保存" } }), { status: 503 }));
  button(mount).click(); await tick();
  assert.equal(mount.querySelectorAll("input")[0].value, "高一三班");
  assert.equal(mount.textContent.includes("上传文件生成名单"), false);
  assert.equal(button(mount).disabled, false);
});

test("saving locks form and navigation, and a double click cannot submit twice", async () => {
  let finish, writes = 0;
  const mount = await setup((_options, session) => { writes++; return new Promise((resolve) => { finish = () => resolve(response({ ...session, revision: 2 })); }); });
  const next = button(mount); next.click(); next.click();
  assert.equal(writes, 1); assert.equal(next.disabled, true);
  assert.equal(mount.querySelectorAll("input")[0].disabled, true);
  finish(); await tick();
  assert.ok(mount.textContent.includes("上传文件生成名单"));
});
