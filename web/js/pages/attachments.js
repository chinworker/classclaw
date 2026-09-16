// 班级资料：上传、列出与删除已保存的附件。

import { el, clear, toast, fmtDateTime, fileSize } from "../util.js";
import { api } from "../api.js";
import { pageHeader, errorPanel, emptyState, fileDropzone, confirmDanger } from "../components.js";
import { state } from "../state.js";
import { appConfig } from "../config.js";

let activeView = null;

export function dispose() {
  activeView?.abort();
  activeView = null;
}

export async function render(mount) {
  dispose();
  const controller = new AbortController();
  activeView = controller;
  const classId = state.classId;
  const ownerId = state.user?.id;
  const live = () => !controller.signal.aborted && mount.isConnected && state.classId === classId && state.user?.id === ownerId;
  const maxBytes = Number(appConfig.storage.max_attachment_bytes) || 20 * 1024 * 1024;
  const maxMb = maxBytes / 1024 / 1024;
  let uploading = false;
  clear(mount);
  if (!state.classId) {
    mount.append(pageHeader("班级资料", "上传需要留存或稍后使用的班级文件。"), emptyState("当前账号没有绑定班级", "请先由管理员分配班级。"));
    return;
  }
  const feedback = el("div");
  const listHost = el("div", { class: "card" });
  mount.append(
    pageHeader("班级资料", "上传需要留存或稍后使用的班级文件。"),
    el("div", { class: "card" },
      el("h3", {}, "上传附件"),
      fileDropzone({
        hint: "拖拽文件到这里，或点击选择",
        multiple: true,
        onFiles: (files, { setBusy }) => upload(files, setBusy),
      }),
      el("p", { class: "muted", style: { fontSize: "12px" } }, `单个文件上限 ${maxMb} MiB。上传失败不会清空已选文件。`),
      feedback),
    listHost);

  async function loadList() {
    if (!live()) return;
    clear(listHost);
    listHost.append(el("h3", {}, "已上传文件"));
    try {
      const rows = await api(`/classes/${classId}/attachments`, { signal: controller.signal });
      if (!live()) return;
      if (!rows.length) {
        listHost.append(el("p", { class: "muted" }, "还没有上传任何文件。"));
        return;
      }
      listHost.append(el("div", { class: "table-wrap" },
        el("table", { class: "data-table" },
          el("thead", {}, el("tr", {},
            el("th", {}, "文件名"), el("th", {}, "大小"), el("th", {}, "上传时间"), el("th", {}, ""))),
          el("tbody", {}, rows.map((row) => el("tr", {},
            el("td", {}, row.original_name),
            el("td", {}, fileSize(row.file_size)),
            el("td", {}, fmtDateTime(row.created_at)),
            el("td", {}, el("button", { class: "text-button", type: "button", onclick: () => remove(row) }, "删除"))))))));
    } catch (error) {
      if (live()) listHost.append(errorPanel(error, { onRetry: loadList }));
    }
  }

  async function upload(files, setBusy) {
    if (!files.length || !live() || uploading) return;
    if (files.some((file) => file.size > maxBytes)) { toast(`文件超过 ${maxMb} MiB 上限`, "error"); return; }
    const remaining = [...files];
    uploading = true;
    setBusy(true);
    clear(feedback);
    try {
      while (remaining.length && live()) {
        const file = remaining[0];
        setBusy(true, `正在上传 ${file.name}…`);
        const body = new FormData();
        body.append("file", file);
        body.append("class_id", classId);
        await api("/attachments", { method: "POST", body, signal: controller.signal });
        remaining.shift();
      }
      if (live()) toast("附件已保存", "success");
    } catch (error) {
      // Retry only files that have not yet succeeded, including a failed tail.
      if (live()) feedback.append(errorPanel(error, { onRetry: () => upload(remaining, setBusy) }));
    } finally {
      uploading = false;
      setBusy(false);
      if (live()) await loadList();
    }
  }

  async function remove(row) {
    if (!live()) return;
    const accepted = await confirmDanger({
      title: `删除附件：${row.original_name}`,
      lines: ["将删除服务器上的文件和附件记录。", "已被事件、作业等记录引用的附件不能删除。", "此操作不可恢复。"],
      confirmLabel: "删除附件",
      signal: controller.signal,
    });
    if (!accepted || !live()) return;
    try {
      await api(`/attachments/${row.id}`, { method: "DELETE" });
      if (!live()) return;
      toast("附件已删除", "success");
      await loadList();
    } catch (error) {
      if (live()) toast(error.message, "error");
    }
  }

  await loadList();
}
