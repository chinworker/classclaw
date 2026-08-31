// 班级资料上传。

import { el, clear, toast } from "../util.js";
import { api } from "../api.js";
import { pageHeader, errorPanel, fileDropzone } from "../components.js";

const MAX_MB = 20;

export async function render(mount) {
  clear(mount);
  const resultHost = el("div");
  mount.append(
    pageHeader("班级资料", "上传需要留存或稍后使用的班级文件。"),
    el("div", { class: "card" },
      el("h3", {}, "上传附件"),
      fileDropzone({
        hint: "拖拽文件到这里，或点击选择",
        onFiles: (files, { setBusy }) => upload(files[0], setBusy),
      }),
      el("p", { class: "muted", style: { fontSize: "12px" } }, `单个文件上限 ${MAX_MB} MiB。`),
      resultHost));

  async function upload(file, setBusy) {
    if (file.size > MAX_MB * 1024 * 1024) { toast(`文件超过 ${MAX_MB} MiB 上限`, "error"); return; }
    setBusy(true, `正在上传 ${file.name}…`);
    const body = new FormData();
    body.append("file", file);
    try {
      const attachment = await api("/attachments", { method: "POST", body });
      clear(resultHost);
      resultHost.append(el("div", { class: "issue issue-ok" }, `已保存：${attachment.original_name}`),
        el("div", { class: "muted", style: { fontSize: "13px", margin: "6px 0" } },
          `大小：${(attachment.file_size / 1024).toFixed(1)} KB`));
      toast("附件已保存", "success");
    } catch (error) {
      clear(resultHost);
      resultHost.append(errorPanel(error));
    } finally { setBusy(false); }
  }

}
