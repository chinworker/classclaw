import assert from "node:assert/strict";
import test from "node:test";
import { installDom, tick } from "./dom.mjs";
import { fileDropzone } from "../../web/js/components.js";

const makeFile = (name, size = 10, lastModified = 1) => ({ name, size, lastModified });

function selectFiles(zone, files) {
  const input = zone.querySelector("input");
  Object.defineProperty(input, "files", { value: files, configurable: true });
  input.dispatchEvent(new Event("change"));
}

function listNames(zone) {
  return zone.querySelectorAll(".drop-file-name").map((node) => node.textContent);
}

function startButton(zone) {
  return zone.querySelectorAll("button").find((node) => node.textContent.includes("开始解析"));
}

test("manual dropzone shows selected files, keeps them after parsing and allows removal", async () => {
  installDom();
  const runs = [];
  const zone = fileDropzone({ manualStart: true, onFiles: async (files) => { runs.push(files.map((file) => file.name)); } });
  document.body.append(zone);

  selectFiles(zone, [makeFile("课表.md"), makeFile("名单.csv", 20, 2)]);
  assert.deepEqual(listNames(zone), ["课表.md", "名单.csv"]);
  assert.equal(startButton(zone).disabled, false);

  startButton(zone).click();
  await tick();
  assert.deepEqual(runs, [["课表.md", "名单.csv"]]);
  assert.deepEqual(listNames(zone), ["课表.md", "名单.csv"]);

  zone.querySelectorAll(".drop-file-remove")[0].click();
  assert.deepEqual(listNames(zone), ["名单.csv"]);
  assert.equal(startButton(zone).disabled, false);

  zone.querySelectorAll(".drop-file-remove")[0].click();
  assert.deepEqual(listNames(zone), []);
  assert.equal(startButton(zone).disabled, true);
});

test("dropzone rejects selections beyond maxFiles and never duplicates the same file", () => {
  installDom();
  const zone = fileDropzone({ manualStart: true, maxFiles: 2, onFiles: () => {} });
  document.body.append(zone);

  selectFiles(zone, [makeFile("a.txt"), makeFile("b.txt")]);
  selectFiles(zone, [makeFile("b.txt"), makeFile("c.txt")]);
  assert.deepEqual(listNames(zone), ["a.txt", "b.txt"]);

  selectFiles(zone, [makeFile("a.txt")]);
  assert.deepEqual(listNames(zone), ["a.txt", "b.txt"]);
});
