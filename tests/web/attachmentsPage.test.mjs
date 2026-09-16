import test, { beforeEach, afterEach } from "node:test";
import assert from "node:assert/strict";
import { installDom, response, tick } from "./dom.mjs";

installDom();
const page = await import("../../web/js/pages/attachments.js");
const { state } = await import("../../web/js/state.js");
let uploads;
let failSecond;
let listed;

beforeEach(() => {
  page.dispose();
  document.body.replaceChildren();
  state.classId = "class-a";
  state.user = { id: "teacher-a", role: "head_teacher" };
  uploads = [];
  failSecond = false;
  listed = [];
  globalThis.fetch = async (path, options) => {
    if (options.method === "POST") {
      const file = options.body.get("file");
      uploads.push({ name: file.name, classId: options.body.get("class_id") });
      if (file.name === "b.txt" && failSecond) return new Response(JSON.stringify({ success: false, error: { code: "UNAVAILABLE", message: "上传失败" } }), { status: 503 });
      return response({ original_name: file.name });
    }
    if (options.method === "DELETE") throw new Error("Should not delete after leaving the page");
    return response(listed);
  };
});
afterEach(() => page.dispose());

async function mountPage() {
  const mount = document.createElement("main");
  document.body.append(mount);
  await page.render(mount);
  return mount;
}
function selectFiles(mount) {
  const input = mount.querySelector("input");
  input.files = [new File(["a"], "a.txt"), new File(["b"], "b.txt")];
  input.dispatchEvent(new Event("change"));
}

test("all selected attachments upload with the captured class scope", async () => {
  const mount = await mountPage();
  selectFiles(mount);
  await tick();
  assert.deepEqual(uploads, [{ name: "a.txt", classId: "class-a" }, { name: "b.txt", classId: "class-a" }]);
});

test("partial upload failure retries only the failed file", async () => {
  failSecond = true;
  const mount = await mountPage();
  selectFiles(mount);
  await tick();
  assert.deepEqual(uploads.map((row) => row.name), ["a.txt", "b.txt"]);
  failSecond = false;
  const retry = mount.querySelectorAll("button").find((button) => button.textContent === "重试");
  retry.click();
  retry.click();
  await tick();
  assert.deepEqual(uploads.map((row) => row.name), ["a.txt", "b.txt", "b.txt"]);
});

test("leaving the attachments page cancels pending delete confirmation", async () => {
  listed = [{ id: "attachment-a", original_name: "a.txt", file_size: 1 }];
  const mount = await mountPage();
  mount.querySelectorAll("button").find((button) => button.textContent === "删除").click();
  assert.ok(document.body.querySelector(".modal"));
  page.dispose();
  await tick();
  assert.equal(document.body.querySelector(".modal"), null);
});
