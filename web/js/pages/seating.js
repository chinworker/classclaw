// 座位表：建班后按需创建；支持命名版本、选择查看、重命名、删除和恢复。

import { el, clear, toast, fmtDateTime } from "../util.js";
import { api, AI_REQUEST_TIMEOUT_MS } from "../api.js";
import { featureEnabled } from "../config.js";
import { state, refreshStudents } from "../state.js";
import { pageHeader, errorPanel, skeleton, emptyState, field, fieldError, confirmDanger, openModal, fileDropzone, showAiRejection } from "../components.js";
import { seatMapEditor } from "../seatmap.js";

export async function render(mount) {
  clear(mount);
  mount.append(pageHeader("座位表", "座位表可在建班后创建，并保留多个命名版本。"));
  const host = el("div");
  mount.append(host);

  async function reload(selectedId = null) {
    host.replaceChildren(skeleton(5));
    try {
      const [students, current, history] = await Promise.all([
        refreshStudents({ force: true }),
        api(`/classes/${state.classId}/seating/current`),
        api(`/classes/${state.classId}/seating/history?page_size=100`),
      ]);
      const versions = history?.items || [];
      const selected = selectedId === "__new__" ? null : versions.find((item) => item.id === selectedId) || current || null;
      renderWorkspace(students, selected, current, versions);
    } catch (error) {
      host.replaceChildren(errorPanel(error, { onRetry: reload }));
    }
  }

  function renderWorkspace(students, selected, current, versions) {
    clear(host);
    const selector = el("select", {},
      el("option", { value: "__new__" }, "新建座位表"),
      ...versions.map((item) => el("option", { value: item.id }, `${item.name}${item.id === current?.id ? "（当前）" : ""}`)));
    selector.value = selected?.id || "__new__";
    selector.addEventListener("change", () => reload(selector.value));
    host.append(el("div", { class: "card seating-version-bar" },
      el("div", {}, el("b", {}, "座位表版本"), el("p", { class: "muted" }, versions.length ? `共 ${versions.length} 个版本，可选择查看或继续调整。` : "还没有座位表，可直接创建。")),
      field("选择版本", selector)));

    renderEditor(students, selected, current, reload);
    renderVersions(versions, current, reload);
  }

  function renderEditor(students, snapshot, current, reloadFn) {
    let editor;
    const editorBox = el("div");
    const importBox = el("div", { class: "import-panel hidden" });
    const analysisBox = el("div");
    const fileAnalysisEnabled = featureEnabled("file_analysis");
    const importBtn = el("button", { class: "secondary", type: "button", disabled: !fileAnalysisEnabled }, fileAnalysisEnabled ? "从文件生成" : "文件解析已关闭");
    function showEditor({ rows, cols, layout }) {
      editor = seatMapEditor({ students, rows, cols, layout, editable: true });
      editorBox.replaceChildren(editor.el);
    }
    showEditor({ rows: snapshot?.rows || 5, cols: snapshot?.cols || 6, layout: snapshot?.layout_json || null });

    const upload = fileDropzone({
      hint: "上传座位表图片、PDF、Word、Excel 或文本文件",
      accept: ".xlsx,.xlsm,.docx,.pptx,.csv,.pdf,.png,.jpg,.jpeg,.webp,.txt,.md,.json",
      multiple: true,
      manualStart: true,
      busyText: "正在识别座位，请稍候…",
      disabled: !fileAnalysisEnabled,
      onFiles: async (files, { signal, taskId }) => {
        clear(analysisBox);
        const form = new FormData();
        files.slice(0, 4).forEach((file) => form.append("files", file));
        try {
          const result = await api(`/classes/${state.classId}/seating/import-preview`, {
            method: "POST",
            body: form,
            timeoutMs: AI_REQUEST_TIMEOUT_MS,
            signal,
            aiTaskId: taskId,
          });
          if (!result.analysis?.accepted) {
            showAiRejection(result.analysis, "座位表数据未采用");
            analysisBox.append(...(result.analysis?.reasons || []).map((text) => el("div", { class: "issue issue-error" }, text)));
            return;
          }
          showEditor(result);
          analysisBox.append(el("div", { class: "issue issue-ok" }, `已识别 ${result.seated_count} 名学生，${result.unseated_count} 名暂未排座。`),
            ...(result.analysis?.warnings || []).map((text) => el("div", { class: "issue issue-warn" }, text)));
        } catch (error) {
          if (error.code === "REQUEST_CANCELLED") toast("已取消座位表识别", "info");
          else analysisBox.append(errorPanel(error));
        }
      },
    });
    importBox.append(el("p", { class: "muted" }, "文件只生成预览，确认保存后才创建新版本。"), upload, analysisBox);
    importBtn.addEventListener("click", () => {
      importBox.classList.toggle("hidden");
      importBtn.textContent = importBox.classList.contains("hidden") ? "从文件生成" : "收起文件导入";
    });

    const nameInput = el("input", { type: "text", placeholder: "留空则按保存时间命名" });
    const noteInput = el("input", { type: "text", placeholder: "调整说明（可选）" });
    const saveBtn = el("button", { class: "primary", type: "button" }, snapshot ? "另存为新版本" : "创建座位表");
    saveBtn.addEventListener("click", async () => {
      const errors = editor.validate();
      if (errors.length) { toast(errors[0], "error"); return; }
      const { rows, cols, layout } = editor.getLayout();
      saveBtn.disabled = true;
      try {
        const result = await api(`/classes/${state.classId}/seating`, {
          method: "POST",
          body: { name: nameInput.value.trim() || null, rows, cols, layout, change_note: noteInput.value.trim() || null },
        });
        toast("座位表版本已保存", "success");
        await reloadFn(result.snapshot.id);
      } catch (error) { toast(error.message, "error"); }
      finally { saveBtn.disabled = false; }
    });

    host.append(el("div", { class: "card" },
      el("div", { class: "row-gap", style: { justifyContent: "space-between" } },
        el("div", {}, el("h3", {}, snapshot ? snapshot.name : "创建座位表"),
          snapshot ? el("p", { class: "muted" }, `${fmtDateTime(snapshot.snapshot_at)}${snapshot.id === current?.id ? " · 当前版本" : " · 历史版本"}`) : null), importBtn),
      importBox,
      students.length ? editorBox : emptyState("还没有学生", "请先在学生档案中添加学生。"),
      students.length ? el("div", { class: "form-grid", style: { marginTop: "14px" } }, field("新版本名称", nameInput), field("调整说明", noteInput)) : null,
      students.length ? saveBtn : null));
  }

  function renderVersions(versions, current, reloadFn) {
    const card = el("div", { class: "card" }, el("h3", {}, "全部版本"));
    if (!versions.length) { card.append(el("p", { class: "muted" }, "创建后会在这里保留历史版本。")); host.append(card); return; }
    for (const snapshot of versions) {
      card.append(el("div", { class: "seating-version-row" },
        el("div", {}, el("b", {}, snapshot.name),
          el("div", { class: "muted", style: { fontSize: "12px" } }, `${fmtDateTime(snapshot.snapshot_at)} · ${snapshot.rows}×${snapshot.cols} · 已排 ${snapshot.layout_json?.flat().filter(Boolean).length || 0} 人${snapshot.id === current?.id ? " · 当前" : ""}`)),
        el("div", { class: "row-gap" },
          el("button", { class: "secondary", type: "button", onclick: () => reloadFn(snapshot.id) }, "选择"),
          el("button", { class: "text-button", type: "button", onclick: () => renameVersion(snapshot, reloadFn) }, "重命名"),
          el("button", { class: "text-button", type: "button", style: { color: "var(--danger)" }, onclick: () => deleteVersion(snapshot, reloadFn) }, "删除"))));
    }
    host.append(card);
  }

  function renameVersion(snapshot, reloadFn) {
    const input = el("input", { type: "text", value: snapshot.name, maxlength: "100" });
    const error = el("div");
    openModal({
      title: "重命名座位表",
      body: el("div", {}, field("名称", input), error),
      actions: [
        { label: "取消", kind: "secondary" },
        { label: "保存", kind: "primary", closeOnDone: true, onClick: async ({ setSubmitting }) => {
          if (!input.value.trim()) { error.replaceChildren(fieldError("名称不能为空")); return false; }
          setSubmitting(true);
          try { await api(`/seating/${snapshot.id}`, { method: "PATCH", body: { name: input.value.trim() } }); toast("已重命名", "success"); await reloadFn(snapshot.id); }
          catch (err) { error.replaceChildren(fieldError(err.message)); return false; }
          finally { setSubmitting(false); }
        } },
      ],
    });
  }

  async function deleteVersion(snapshot, reloadFn) {
    const confirmed = await confirmDanger({ title: "删除座位表版本", lines: [`将删除“${snapshot.name}”。`, "删除后不能恢复，其他版本不受影响。"], confirmLabel: "确认删除" });
    if (!confirmed) return;
    try { await api(`/seating/${snapshot.id}`, { method: "DELETE" }); toast("座位表版本已删除", "success"); await reloadFn(); }
    catch (error) { toast(error.message, "error"); }
  }

  await reload();
}
