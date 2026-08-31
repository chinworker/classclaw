// 课表与调课：节次、基础课表矩阵（TimetableGridEditor）、每日课表、临时覆盖、互换与批量调课（领域预览+确认）。

import { el, clear, toast, todayStr, WEEKDAY_NAMES, weekdayOf, periodLabel } from "../util.js";
import { api } from "../api.js";
import { state, refreshClassInfo, refreshSubjects } from "../state.js";
import { pageHeader, field, errorPanel, skeleton, emptyState, statusBadge, confirmDanger, fileDropzone } from "../components.js";
import { timetableGridEditor } from "../timetableGrid.js";

export async function render(mount, ctx, helpers) {
  clear(mount);
  mount.append(pageHeader("课表与调课", "编辑基础课表，处理调课。"));
  const host = el("div");
  mount.append(host);
  host.append(skeleton(5));

  let periods, baseItems, subjects;
  try {
    [periods, baseItems, subjects] = await Promise.all([
      api(`/classes/${state.classId}/periods`),
      api(`/classes/${state.classId}/timetable/base`),
      refreshSubjects({ force: true }),
    ]);
  } catch (error) {
    clear(host);
    host.append(errorPanel(error, { onRetry: () => render(mount, ctx, helpers) }));
    return;
  }
  clear(host);

  /* ---------- 节次管理（只有创建接口） ---------- */
  const periodCard = el("div", { class: "card" }, el("h3", {}, "节次"));
  if (periods.length) {
    periodCard.append(el("p", { class: "muted" }, `当前共 ${periods.length} 节。`));
  } else {
    periodCard.append(el("p", { class: "muted" }, "尚无节次，请先添加。"));
  }
  const nameInput = el("input", { type: "text", placeholder: "自定义名称（可选）", style: { width: "180px" } });
  const addPeriodBtn = el("button", { class: "secondary", type: "button" }, "新增一节");
  addPeriodBtn.addEventListener("click", async () => {
    const no = periods.length ? Math.max(...periods.map((period) => period.period_no)) + 1 : 1;
    addPeriodBtn.disabled = true;
    try {
      await api(`/classes/${state.classId}/periods`, { method: "POST", body: { period_no: no, name: nameInput.value.trim() || null, sort_order: no } });
      toast("节次已创建", "success");
      render(mount, ctx, helpers);
    } catch (error) { toast(error.message, "error"); addPeriodBtn.disabled = false; }
  });
  periodCard.append(el("div", { class: "row-gap" }, nameInput, addPeriodBtn));
  host.append(periodCard);

  /* ---------- 基础课表矩阵 ---------- */
  const classInfo = state.classInfo || await refreshClassInfo().catch(() => null);
  const editorHost = el("div");
  const importBox = el("div", { class: "import-panel hidden" });
  const analysisBox = el("div");
  const saveBtn = el("button", { class: "primary", type: "button", disabled: !periods.length }, "保存整张基础课表");
  const resetBtn = el("button", { class: "secondary", type: "button" }, "重新设置基础课表");
  const saveInfo = el("span", { class: "muted", style: { fontSize: "13px" } });
  let importedPeriods = null;
  let editor = null;

  function showEditor(nextPeriods, nextItems) {
    periods = nextPeriods;
    editor = timetableGridEditor({ periods, items: nextItems, editable: true, defaultRoom: classInfo?.room || null });
    editorHost.replaceChildren(editor.el);
    saveBtn.disabled = false;
  }
  if (periods.length) showEditor(periods, baseItems);
  else editorHost.append(emptyState("还没有基础课表", "可以先添加节次手动填写，也可以直接上传现有课表。"));

  const upload = fileDropzone({
    hint: "上传课表图片、PDF、Word、Excel 或文本文件",
    accept: ".xlsx,.xlsm,.docx,.pptx,.csv,.pdf,.png,.jpg,.jpeg,.webp,.txt,.md,.json",
    multiple: true,
    busyText: "正在识别课表，请稍候…",
    onFiles: async (files, { setBusy }) => {
      setBusy(true);
      clear(analysisBox);
      const form = new FormData();
      files.slice(0, 4).forEach((file) => form.append("files", file));
      try {
        const result = await api(`/classes/${state.classId}/timetable/import-preview`, { method: "POST", body: form });
        importedPeriods = result.periods;
        showEditor(result.periods, result.items);
        analysisBox.append(
          el("div", { class: "issue issue-ok" }, `已识别 ${result.items.length} 节课。请直接在表格里核对或修改，再点保存。`),
          ...(result.analysis?.warnings || []).map((text) => el("div", { class: "issue issue-warn" }, text)),
        );
      } catch (error) {
        analysisBox.append(errorPanel(error));
      } finally { setBusy(false); }
    },
  });
  importBox.append(el("p", { class: "muted" }, "上传后会先生成可编辑预览，只有你点击保存才会替换当前基础课表。"), upload, analysisBox);
  resetBtn.addEventListener("click", () => {
    importBox.classList.toggle("hidden");
    resetBtn.textContent = importBox.classList.contains("hidden") ? "重新设置基础课表" : "收起文件导入";
  });
  saveBtn.addEventListener("click", async () => {
    const items = editor?.getItems() || [];
    if (!items.length) { toast("课表至少需要一节课", "error"); return; }
    const ok = await confirmDanger({
      title: "保存新的基础课表",
      lines: [`将用当前表格中的 ${items.length} 节课替换原基础课表。`, "临时调课记录不会受影响。"],
      confirmLabel: "确认保存",
    });
    if (!ok) return;
    saveBtn.disabled = true;
    saveInfo.textContent = "保存中…";
    try {
      if (importedPeriods) {
        await api(`/classes/${state.classId}/timetable/import-apply`, { method: "POST", body: { periods: importedPeriods, items } });
        importedPeriods = null;
      } else {
        await api(`/classes/${state.classId}/timetable/base`, { method: "PUT", body: { items } });
      }
      toast("基础课表已保存", "success");
      subjects = await refreshSubjects({ force: true });
      saveInfo.textContent = "";
    } catch (error) {
      toast(error.message, "error");
      saveInfo.textContent = "保存失败，当前编辑内容仍保留";
    } finally { saveBtn.disabled = false; }
  });
  host.append(el("div", { class: "card" },
    el("div", { class: "row-gap", style: { justifyContent: "space-between" } }, el("h3", {}, "基础课表"), resetBtn),
    el("p", { class: "muted" }, "点击任意单元格填写科目、任课老师和教室。"),
    importBox,
    editorHost,
    el("div", { class: "row-gap" }, saveBtn, saveInfo)));

  /* ---------- 每日课表与临时覆盖 ---------- */
  const dailyDate = el("input", { type: "date", value: todayStr() });
  const dailyHost = el("div");
  const dailyCard = el("div", { class: "card" },
    el("h3", {}, "每日课表（含临时变更）"),
    el("div", { class: "row-gap", style: { marginBottom: "10px" } },
      field("日期", dailyDate), el("button", { class: "secondary", type: "button", onclick: loadDaily }, "查看"),
      el("button", { class: "secondary", type: "button", onclick: openOverrideModal }, "新增临时覆盖"),
      el("button", { class: "secondary", type: "button", onclick: openSwapModal }, "两节互换"),
      el("button", { class: "secondary", type: "button", onclick: openBatchModal }, "长期调课")),
    dailyHost);
  host.append(dailyCard);

  async function loadDaily() {
    clear(dailyHost);
    dailyHost.append(skeleton(3));
    let rows;
    try {
      rows = await api(`/classes/${state.classId}/timetable/daily?lesson_date=${dailyDate.value}`);
    } catch (error) {
      clear(dailyHost);
      dailyHost.append(errorPanel(error, { onRetry: loadDaily }));
      return;
    }
    clear(dailyHost);
    if (!rows.length) { dailyHost.append(el("p", { class: "muted" }, `${dailyDate.value}（${WEEKDAY_NAMES[weekdayOf(dailyDate.value)]}）没有课程`)); return; }
    dailyHost.append(...rows.map((lesson) => el("div", {
      class: `row-gap${lesson.is_changed ? " daily-changed" : ""}`,
      style: { justifyContent: "space-between", padding: "8px 10px", borderRadius: "10px", borderBottom: "1px solid var(--border)" },
    },
      el("span", { style: lesson.is_cancelled ? { textDecoration: "line-through", opacity: 0.65 } : null },
        el("b", {}, `${lesson.period_name || `第${lesson.period_no}节`} · `),
        lesson.is_cancelled ? `${lesson.original_subject}（已取消）` : lesson.subject,
        lesson.teacher ? el("span", { class: "muted" }, ` ${lesson.teacher}`) : null,
        lesson.room ? el("span", { class: "muted" }, ` · ${lesson.room}`) : null),
      el("span", { class: "row-gap" },
        lesson.is_changed ? statusBadge("warn", lesson.is_cancelled ? "已取消" : "临时变更") : statusBadge("normal", "按课表"),
        lesson.is_changed && lesson.change_reason ? el("span", { class: "muted", style: { fontSize: "12px" } }, lesson.change_reason) : null))));
    if (rows.some((l) => l.is_changed)) {
      dailyHost.append(el("p", { class: "muted", style: { fontSize: "12px" } }, "如需更正临时变更，可为同一天同一节重新填写正确安排。"));
    }
  }

  function openOverrideModal() {
    const periodSel = el("select", {}, periods.map((p) => el("option", { value: String(p.period_no) }, periodLabel(p))));
    const subjectInput = el("select", {}, el("option", { value: "" }, "不替换科目"), ...subjects.map((s) => el("option", { value: s.name }, s.name)));
    const teacherInput = el("input", { type: "text", placeholder: "替换老师（可选）" });
    const roomInput = el("input", { type: "text", placeholder: "替换教室（可选）" });
    const reasonInput = el("input", { type: "text", placeholder: "原因，如：老师请假" });
    const cancelSel = el("select", {}, el("option", { value: "normal" }, "替换课程"), el("option", { value: "cancelled" }, "取消本节"));
    openModalLike("新增临时覆盖", el("div", {},
      el("div", { class: "form-grid" },
        field("日期 *", el("input", { type: "date", value: todayStr(), id: "ov-date" })),
        field("节次 *", periodSel), field("类型", cancelSel),
        field("替换科目", subjectInput), field("替换老师", teacherInput), field("替换教室", roomInput)),
      field("原因 *", reasonInput)),
      async () => {
        const date = document.getElementById("ov-date").value;
        if (!date || !reasonInput.value.trim()) { toast("日期和原因必填", "error"); return false; }
        await api("/lesson-overrides", {
          method: "POST",
          body: {
            class_id: state.classId, lesson_date: date, period_no: Number(periodSel.value),
            replacement_subject: subjectInput.value || null, replacement_teacher: teacherInput.value.trim() || null,
            replacement_room: roomInput.value.trim() || null, status: cancelSel.value, reason: reasonInput.value.trim(),
          },
        });
        toast("临时覆盖已创建", "success");
        loadDaily();
      });
  }

  function openSwapModal() {
    const dateInput = el("input", { type: "date", value: todayStr() });
    const aSel = el("select", {}, periods.map((p) => el("option", { value: String(p.period_no) }, periodLabel(p))));
    const bSel = el("select", {}, periods.map((p) => el("option", { value: String(p.period_no) }, periodLabel(p))));
    const reasonInput = el("input", { type: "text", placeholder: "互换原因" });
    const previewBox = el("div");
    const previewBtn = el("button", { class: "secondary", type: "button" }, "预览影响");
    const confirmBtn = el("button", { class: "primary", type: "button", disabled: true }, "确认互换");
    previewBtn.addEventListener("click", async () => {
      clear(previewBox);
      if (!reasonInput.value.trim()) { toast("请填写互换原因", "error"); return; }
      previewBtn.disabled = true;
      try {
        const preview = await api("/lesson-swaps/preview", { method: "POST", body: swapBody() });
        const lines = (preview.changes || []).map((c) => `${c.lesson_key}：「${c.from}」→「${c.to}」`);
        previewBox.append(el("div", { class: "issue issue-warn" }, `互换影响：${lines.join("；") || "无变化"}`),
          (preview.conflicts || []).length ? el("div", { class: "issue issue-error" }, `以下课程已有临时覆盖，不能互换：${preview.conflicts.join("、")}`) : null);
        confirmBtn.disabled = (preview.conflicts || []).length > 0;
      } catch (error) { previewBox.append(errorPanel(error)); }
      finally { previewBtn.disabled = false; }
    });
    confirmBtn.addEventListener("click", async () => {
      confirmBtn.disabled = true;
      try {
        await api("/lesson-swaps/confirm", { method: "POST", body: swapBody() });
        toast("课程互换已生效", "success");
        document.querySelector(".modal-close")?.click();
        loadDaily();
      } catch (error) { toast(error.message, "error"); confirmBtn.disabled = false; }
    });
    function swapBody() {
      return { class_id: state.classId, lesson_date: dateInput.value, period_a: Number(aSel.value), period_b: Number(bSel.value), reason: reasonInput.value.trim() };
    }
    openModalLike("两节互换（先预览再确认）", el("div", {},
      el("div", { class: "form-grid" }, field("日期 *", dateInput), field("节次 A", aSel), field("节次 B", bSel)),
      field("原因 *", reasonInput), el("div", { class: "row-gap" }, previewBtn, confirmBtn), previewBox), null);
  }

  function openBatchModal() {
    const startInput = el("input", { type: "date", value: todayStr() });
    const endInput = el("input", { type: "date", value: todayStr() });
    const weekdayChecks = [1, 2, 3, 4, 5].map((day) => {
      const input = el("input", { type: "checkbox", checked: true });
      return { day, input, node: el("label", { class: "chip", style: { cursor: "pointer" } }, input, ` ${WEEKDAY_NAMES[day]}`) };
    });
    const weekdayControl = el("div", { class: "chips", style: { margin: 0 } }, weekdayChecks.map((item) => item.node));
    const periodSel = el("select", {}, periods.map((p) => el("option", { value: String(p.period_no) }, periodLabel(p))));
    const subjectInput = el("select", {}, el("option", { value: "" }, "不替换科目"), ...subjects.map((s) => el("option", { value: s.name }, s.name)));
    const teacherInput = el("input", { type: "text", placeholder: "替换老师（可选）" });
    const reasonInput = el("input", { type: "text", placeholder: "调课原因" });
    const previewBox = el("div");
    const previewBtn = el("button", { class: "secondary", type: "button" }, "预览影响");
    const confirmBtn = el("button", { class: "primary", type: "button", disabled: true }, "确认调课");
    function batchBody() {
      return {
        class_id: state.classId, start_date: startInput.value, end_date: endInput.value,
        weekdays: weekdayChecks.filter((item) => item.input.checked).map((item) => item.day), period_no: Number(periodSel.value),
        replacement_subject: subjectInput.value || null, replacement_teacher: teacherInput.value.trim() || null,
        reason: reasonInput.value.trim(),
      };
    }
    previewBtn.addEventListener("click", async () => {
      clear(previewBox);
      if (!reasonInput.value.trim()) { toast("请填写调课原因", "error"); return; }
      previewBtn.disabled = true;
      try {
        const preview = await api("/lesson-batch-changes/preview", { method: "POST", body: batchBody() });
        previewBox.append(el("div", { class: "issue issue-warn" }, `将影响 ${preview.total ?? (preview.changes || []).length} 天的课程`),
          (preview.conflicts || []).length ? el("div", { class: "issue issue-error" }, `冲突 ${(preview.conflicts || []).length} 项：${preview.conflicts.map((c) => c.message || JSON.stringify(c)).join("；")}`) : null,
          el("div", { class: "json-details" }, jsonDetailsLocal(preview.changes || [], "查看影响明细")));
        confirmBtn.disabled = (preview.conflicts || []).length > 0;
      } catch (error) { previewBox.append(errorPanel(error)); }
      finally { previewBtn.disabled = false; }
    });
    confirmBtn.addEventListener("click", async () => {
      confirmBtn.disabled = true;
      try {
        await api("/lesson-batch-changes/confirm", { method: "POST", body: batchBody() });
        toast("批量调课已生效", "success");
        document.querySelector(".modal-close")?.click();
        loadDaily();
      } catch (error) { toast(error.message, "error"); confirmBtn.disabled = false; }
    });
    openModalLike("长期调课（先预览再确认）", el("div", {},
      el("div", { class: "form-grid" },
        field("开始日期 *", startInput), field("结束日期 *", endInput),
        field("星期（可多选）", weekdayControl), field("节次", periodSel),
        field("替换科目", subjectInput), field("替换老师", teacherInput)),
      field("原因 *", reasonInput), el("div", { class: "row-gap" }, previewBtn, confirmBtn), previewBox), null);
  }

  function jsonDetailsLocal(value, summary) {
    const pre = el("pre", { class: "json-pre" });
    pre.textContent = JSON.stringify(value, null, 2);
    return el("details", {}, el("summary", {}, summary), pre);
  }

  await loadDaily();
}

function openModalLike(title, body, onConfirm) {
  // 局部小封装，避免与 components 循环依赖
  return import("../components.js").then(({ openModal }) => {
    openModal({
      title, body, wide: true,
      actions: [
        { label: "取消", kind: "secondary" },
        ...(onConfirm ? [{
          label: "保存", kind: "primary",
          onClick: async ({ setSubmitting, close }) => {
            setSubmitting(true);
            try { await onConfirm(); close(); } catch (error) { toast(error.message, "error"); }
            finally { setSubmitting(false); }
          },
        }] : []),
      ],
    });
  });
}
