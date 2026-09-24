import assert from "node:assert/strict";
import { afterEach, test } from "node:test";
import { installDom, response, tick } from "./dom.mjs";
import { adminView } from "../../web/js/adminView.js";
import { settingsEditor, diffPreviewModal, needsRestart } from "../../web/js/settingsEditor.js";
import { applyTomlChanges, sourceEditor } from "../../web/js/sourceEditor.js";

let view;
afterEach(() => view?.dispose());
function fixture(locked = false) {
  installDom(); view = adminView();
  const snapshot = { toml_text: "[server]\nport = 8000\n", config_hash: "h1", active_config_hash: "h1", document_hash: "d1",
    env_locked_paths: locked ? ["server.port"] : [], values: { "server.port": 8000 },
    layout: { tables: { "": 0, server: 9 }, spans: [{ start: 9, end: 21, root: "server.port", scope: "server", values: { "server.port": 8000 } }] },
    catalog: { sections: [{ id: "server", label: "服务监听" }], credentials: [], items: [{ config_path: "server.port", section: "server", label: "监听端口", description: "HTTP 端口", env_var: "CLASSCLAW_SERVER_PORT", type: "integer", value: 8000, minimum: 1, maximum: 65535, value_source: locked ? "environment:CLASSCLAW_SERVER_PORT" : "file" }] },
  };
  const editor = settingsEditor({ snapshot, view }); document.body.append(editor.el);
  return { editor, snapshot, port: editor.el.querySelectorAll("input").find((node) => node.dataset.path === "server.port") };
}
const button = (text) => document.body.querySelectorAll("button").find((node) => node.textContent === text);

test("form edits track dirty rows and group badges, validate and discard", () => {
  const { editor, port } = fixture();
  assert.equal(editor.isDirty(), false);
  port.value = "8001"; port.dispatchEvent(new Event("input"));
  assert.equal(editor.isDirty(), true);
  assert.ok(editor.el.textContent.includes("已修改 1 项"));
  assert.equal(editor.el.querySelectorAll(".is-dirty").length, 1);
  assert.equal(editor.el.querySelector(".settings-tree").textContent, "全部设置服务监听1");
  port.value = "70000"; port.dispatchEvent(new Event("input"));
  assert.equal(port.getAttribute("aria-invalid"), "true");
  assert.equal(button("预览差异并保存").disabled, true);
  button("放弃全部").click();
  assert.equal(editor.isDirty(), false);
  assert.ok(editor.el.textContent.includes("所有修改已保存"));
});

test("environment overrides disable inputs with the exact variable in the tooltip", () => {
  const { port, editor } = fixture(true);
  assert.equal(port.disabled, true);
  assert.ok(port.title.includes("CLASSCLAW_SERVER_PORT"));
  assert.ok(editor.el.textContent.includes("ENV"));
  port.value = "9000"; port.dispatchEvent(new Event("input"));
  assert.equal(editor.isDirty(), false);
});

test("locked enums show the environment value while source retains the file value", () => {
  const { editor: previous, snapshot } = fixture(); previous.el.remove();
  const text = '[runtime]\nlog_level = "INFO"\n';
  const editor = settingsEditor({ view, snapshot: { ...snapshot, toml_text: text,
    env_locked_paths: ["runtime.log_level"], values: { "runtime.log_level": "INFO" },
    catalog: { sections: [{ id: "runtime", label: "运行与日志" }], credentials: [], items: [{
      config_path: "runtime.log_level", section: "runtime", label: "日志级别", type: "string",
      value: "ERROR", enum: ["INFO", "ERROR"], env_var: "CLASSCLAW_LOG_LEVEL", value_source: "environment:CLASSCLAW_LOG_LEVEL",
    }] },
  } });
  document.body.append(editor.el);
  const select = editor.el.querySelector("select");
  assert.equal(select.disabled, true);
  assert.deepEqual(select.querySelectorAll("option").filter((option) => option.getAttribute("selected") !== null).map((option) => option.value), ["ERROR"]);
  assert.equal(editor.isDirty(), false);
  button("源码").click();
  assert.equal(editor.el.querySelector("textarea").value, text);
});

test("invalid source cannot switch to form and highlights the backend error line", async () => {
  const { editor } = fixture();
  button("源码").click();
  const input = editor.el.querySelector("textarea");
  input.value = "[server]\nport = invalid"; input.dispatchEvent(new Event("input"));
  globalThis.fetch = async () => new Response(JSON.stringify({ success: false, error: {
    code: "CONFIG_TOML_INVALID", message: "TOML 语法错误", details: { errors: [{ line: 2, message: "非法值" }] },
  } }), { status: 422 });
  button("表单").click(); await tick();
  assert.equal(editor.el.querySelector("textarea"), input);
  assert.equal(editor.el.querySelector(".source-line-error").textContent, "2\n");
  assert.ok(editor.el.textContent.includes("TOML 语法错误"));
});

test("source uses line numbers and renders untrusted text without HTML", () => {
  installDom();
  const source = sourceEditor("<script>\na\n", { errors: [{ line: 2, message: "bad" }] });
  assert.equal(source.el.querySelector("pre").children.length, 3);
  assert.equal(source.input.value, "<script>\na\n");
  assert.equal(source.el.querySelector(".source-line-error").textContent, "2\n");
});

test("diff masks credential values and retains inline failures in its modal", async () => {
  installDom(); view = adminView();
  diffPreviewModal([{ path: "gateway.token", old: "private-old", new: "private-new" }], { view, onSave: async () => { throw new Error("Save failed"); } });
  assert.equal(document.body.textContent.includes("private-"), false);
  button("确认保存").click(); await tick();
  assert.ok(document.body.querySelector(".modal"));
  assert.ok(document.body.textContent.includes("Save failed"));
});

test("save previews exact draft and restart banner persists until active hash matches", async () => {
  const { port, snapshot } = fixture();
  let writes = 0;
  globalThis.fetch = async (url, options) => {
    if (url.endsWith("/check")) return response({ diff: [{ path: "server.port", old: 8000, new: 8001 }], values: { "server.port": 8001 }, layout: snapshot.layout });
    if (options.method === "PATCH") {
      const body = JSON.parse(options.body); assert.equal(body.toml_text, "[server]\nport = 8001\n");
      assert.equal(body.base_hash, "h1"); writes += 1;
      return response({ new_config_hash: "h2", document_hash: "d2" });
    }
    return response({ ...snapshot, toml_text: "[server]\nport = 8001\n", config_hash: "h2", values: { "server.port": 8001 } });
  };
  port.value = "8001"; port.dispatchEvent(new Event("input"));
  button("预览差异并保存").click(); await tick();
  assert.equal(writes, 0);
  button("确认保存").click(); await tick();
  assert.equal(writes, 1);
  assert.ok(document.body.querySelector(".settings-restart-banner"));
  assert.equal(needsRestart({ config_hash: "h2", active_config_hash: "h1" }), true);
  assert.equal(needsRestart({ config_hash: "h2", active_config_hash: "h2" }), false);
});

test("form replacements preserve unrelated comments, multiline siblings and Unicode offsets", () => {
  const text = '# 𝄞\nserver = { port = 8000, host = "localhost" }\n';
  const result = applyTomlChanges(text, { tables: { "": 0 }, spans: [{ start: 4, end: Array.from(text).length, root: "server", scope: "", values: { "server.port": 8000, "server.host": "localhost" } }] }, { "server.port": 8001 });
  assert.equal(result, '# 𝄞\nserver.port = 8001\nserver.host = "localhost"\n');
  const inserted = applyTomlChanges("[server]\n# note\n", { spans: [], tables: { "": 0, server: 9 } }, { "server.port": 8002 });
  assert.equal(inserted, "[server]\nport = 8002\n# note\n");
});

test("typing during validation cannot save a draft different from the preview", async () => {
  const { port, snapshot } = fixture(); let finish;
  globalThis.fetch = () => new Promise((resolve) => { finish = resolve; });
  port.value = "8001"; port.dispatchEvent(new Event("input"));
  button("预览差异并保存").click();
  port.value = "8002"; port.dispatchEvent(new Event("input"));
  finish(response({ diff: [{ path: "server.port", old: 8000, new: 8001 }], values: { "server.port": 8001 }, layout: snapshot.layout }));
  await tick();
  assert.equal(document.body.querySelector(".modal"), null);
  assert.equal(port.value, "8002");
});

test("ICE server arrays serialize to TOML inline tables", () => {
  const text = applyTomlChanges("[classroom]\n", { tables: { "": 0, classroom: 12 }, spans: [] },
    { "classroom.media_ice_servers": [{ urls: ["stun:example.test:3478"] }] });
  assert.match(text, /media_ice_servers = \[\{ "urls" = \["stun:example.test:3478"\] \}\]/);
});
