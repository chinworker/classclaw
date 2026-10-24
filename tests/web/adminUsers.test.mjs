import assert from "node:assert/strict";
import { afterEach, test } from "node:test";
import { installDom, response, tick } from "./dom.mjs";
import * as page from "../../web/js/pages/adminUsers.js";

afterEach(() => page.dispose());
test("custom initial password is submitted unchanged and success never claims it is 32767", async () => {
  installDom(); let posted;
  globalThis.fetch = async (path, options = {}) => {
    if (options.method === "POST") { posted = JSON.parse(options.body); return response({ id: "new-user" }); }
    return response(path.includes("/classes") ? { items: [] } : []);
  };
  const mount = document.createElement("main"); document.body.append(mount); await page.render(mount);
  mount.querySelectorAll("button").find((node) => node.textContent === "添加班主任").click();
  const modal = document.body.querySelector(".modal");
  const inputs = modal.querySelectorAll("input"); inputs[0].value = "new-teacher"; inputs[2].value = "custom-initial-password";
  modal.querySelectorAll("button").find((node) => node.textContent === "创建").click(); await tick();
  assert.equal(posted.password, "custom-initial-password");
  assert.ok(document.body.textContent.includes("所填写的初始密码"));
  assert.equal(document.body.textContent.includes("32767"), false);
  assert.equal(document.body.textContent.includes("custom-initial-password"), false);
});
