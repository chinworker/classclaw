// 课表与调课：本周最新课表、三类课程调整、基础课表维护。

import { el, clear, toast, todayStr, addDaysStr, WEEKDAY_NAMES, weekdayOf, periodLabel } from "../util.js";
import { api, AI_REQUEST_TIMEOUT_MS } from "../api.js";
import { state, refreshClassInfo, refreshSubjects } from "../state.js";
import { pageHeader, field, errorPanel, skeleton, emptyState, statusBadge, confirmDanger, fileDropzone } from "../components.js";
import { timetableGridEditor } from "../timetableGrid.js";

export async function render(mount, ctx, helpers) {
  clear(mount);
  mount.append(pageHeader("课表与调课", "先看本周最新安排，再处理课程调整；基础课表放在页面底部。"));
  const host = el("div");
  mount.append(host);
  host.append(skeleton(6));

  let periods;
  let baseItems;
  let subjects;
  let classInfo;
  try {
    [periods, baseItems, subjects, classInfo] = await Promise.all([
      api(`/classes/${state.classId}/periods`),
      api(`/classes/${state.classId}/timetable/base`),
      refreshSubjects({ force: true }),
      state.classInfo || refreshClassInfo(),
    ]);
  } catch (error) {
    host.replaceChildren(errorPanel(error, { onRetry: () => render(mount, ctx, helpers) }));
    return;
  }
  clear(host);

  const currentDate = todayStr();
  const currentWeekStart = addDaysStr(currentDate, 1 - weekdayOf(currentDate));
  let weekOffset = 0;
  let weekDates = Array.from({ length: 7 }, (_, index) => addDaysStr(currentWeekStart, index));
  const dailyCache = new Map();
  const previewRefreshers = [];
  const weekHost = el("div");
  const weekTitle = el("h3", {}, "本周课表");
  const weekRange = el("strong", { class: "week-range" });
  const previousWeekBtn = el("button", { class: "secondary week-nav-button", type: "button" }, "上一周");
  const currentWeekBtn = el("button", { class: "secondary week-nav-button", type: "button", disabled: true }, "回到本周");
  const nextWeekBtn = el("button", { class: "secondary week-nav-button", type: "button" }, "下一周");

  function periodOptions() {
    return periods.map((period) => el("option", { value: String(period.period_no) }, periodLabel(period)));
  }

  function periodSelect(selected = null) {
    const select = el("select", {}, periodOptions());
    if (selected !== null) select.value = String(selected);
    return select;
  }

  function subjectSelect(placeholder = "选择科目") {
    return el("select", {},
      el("option", { value: "" }, placeholder),
      ...subjects.map((subject) => el("option", { value: subject.name }, subject.name)));
  }

  function shortDate(dateString) {
    const [, month, day] = dateString.split("-");
    return `${Number(month)}月${Number(day)}日`;
  }

  function fullDate(dateString) {
    const [year, month, day] = dateString.split("-");
    return `${year}年${Number(month)}月${Number(day)}日`;
  }

  function updateWeekHeader() {
    if (weekOffset === 0) weekTitle.textContent = "本周课表";
    else if (weekOffset === -1) weekTitle.textContent = "上周课表";
    else if (weekOffset === 1) weekTitle.textContent = "下周课表";
    else weekTitle.textContent = weekOffset < 0 ? `${Math.abs(weekOffset)}周前课表` : `${weekOffset}周后课表`;
    weekRange.textContent = `${fullDate(weekDates[0])}—${fullDate(weekDates[6])}`;
    currentWeekBtn.disabled = weekOffset === 0;
  }

  async function getDaily(dateString, force = false) {
    if (!force && dailyCache.has(dateString)) return await dailyCache.get(dateString);
    const pending = api(`/classes/${state.classId}/timetable/daily?lesson_date=${dateString}`);
    dailyCache.set(dateString, pending);
    try {
      const rows = await pending;
      dailyCache.set(dateString, rows);
      return rows;
    } catch (error) {
      dailyCache.delete(dateString);
      throw error;
    }
  }

  function lessonPreview(lesson, emptyText = "该日期和节次没有课程") {
    if (!lesson) return el("div", { class: "lesson-preview empty" }, el("span", { class: "muted" }, emptyText));
    const subject = lesson.is_cancelled ? lesson.original_subject : lesson.subject;
    return el("div", { class: `lesson-preview${lesson.is_changed ? " changed" : ""}` },
      el("div", { class: "lesson-preview-title" },
        el("b", {}, subject || "未设置科目"),
        lesson.is_changed ? statusBadge("warn", lesson.is_cancelled ? "已取消" : "已调整") : statusBadge("normal", "当前安排")),
      el("div", { class: "lesson-preview-meta" },
        el("span", {}, `教师：${lesson.teacher || "未填写"}`),
        el("span", {}, `教室：${lesson.room || "未填写"}`)),
      lesson.change_reason ? el("small", { class: "muted" }, `调整原因：${lesson.change_reason}`) : null);
  }

  function bindLatestPreview(dateInput, periodInput, previewHost, { outputs = null, onLesson = null } = {}) {
    let requestNo = 0;
    async function refresh() {
      const currentRequest = ++requestNo;
      previewHost.replaceChildren(el("p", { class: "muted compact-loading" }, "正在读取最新课表…"));
      try {
        const rows = await getDaily(dateInput.value);
        if (currentRequest !== requestNo) return;
        const lesson = rows.find((row) => Number(row.period_no) === Number(periodInput.value)) || null;
        previewHost.replaceChildren(lessonPreview(lesson));
        if (outputs) {
          outputs.subject.value = lesson?.is_cancelled ? "" : (lesson?.subject || "");
          outputs.teacher.value = lesson?.is_cancelled ? "" : (lesson?.teacher || "");
          outputs.room.value = lesson?.is_cancelled ? "" : (lesson?.room || "");
        }
        onLesson?.(lesson);
      } catch (error) {
        if (currentRequest !== requestNo) return;
        previewHost.replaceChildren(errorPanel(error));
        if (outputs) Object.values(outputs).forEach((input) => { input.value = ""; });
        onLesson?.(null);
      }
    }
    dateInput.addEventListener("change", refresh);
    periodInput.addEventListener("change", refresh);
    previewRefreshers.push(refresh);
    refresh();
    return refresh;
  }

  async function refreshScheduleViews() {
    dailyCache.clear();
    await Promise.all([loadWeek(), ...previewRefreshers.map((refresh) => refresh())]);
  }

  /* ---------- 本周最新课表 ---------- */

  function weeklyCell(lesson, dateString) {
    if (!lesson) return el("td", { class: `weekly-lesson empty${dateString === currentDate ? " today" : ""}` }, "—");
    const subject = lesson.is_cancelled ? lesson.original_subject : lesson.subject;
    return el("td", {
      class: `weekly-lesson${lesson.is_changed ? " changed" : ""}${lesson.is_cancelled ? " cancelled" : ""}${dateString === currentDate ? " today" : ""}`,
      title: lesson.change_reason || "",
    },
    el("b", {}, subject || "—"),
    lesson.teacher ? el("span", {}, lesson.teacher) : null,
    lesson.room ? el("small", {}, lesson.room) : null,
    lesson.is_changed ? el("em", {}, lesson.is_cancelled ? "取消" : "调整") : null);
  }

  async function loadWeek() {
    const requestedDates = [...weekDates];
    previousWeekBtn.disabled = true;
    currentWeekBtn.disabled = true;
    nextWeekBtn.disabled = true;
    weekHost.replaceChildren(skeleton(4));
    try {
      const days = await Promise.all(requestedDates.map(async (dateString) => ({ dateString, rows: await getDaily(dateString, true) })));
      if (!periods.length) {
        weekHost.replaceChildren(emptyState("还没有课表", "请在页面底部设置基础课表。"));
        return;
      }
      const table = el("table", { class: "weekly-timetable" });
      table.append(el("thead", {}, el("tr", {},
        el("th", { class: "weekly-period" }, "节次"),
        ...days.map(({ dateString }) => el("th", { class: dateString === currentDate ? "today" : "" },
          el("b", {}, WEEKDAY_NAMES[weekdayOf(dateString)]),
          el("span", {}, shortDate(dateString)))))));
      const tbody = el("tbody");
      for (const period of periods) {
        tbody.append(el("tr", {},
          el("th", { class: "weekly-period" }, periodLabel(period)),
          ...days.map(({ dateString, rows }) => weeklyCell(rows.find((row) => row.period_no === period.period_no), dateString))));
      }
      table.append(tbody);
      weekHost.replaceChildren(el("div", { class: "weekly-timetable-scroll" }, table));
    } catch (error) {
      weekHost.replaceChildren(errorPanel(error, { onRetry: loadWeek }));
    } finally {
      previousWeekBtn.disabled = false;
      nextWeekBtn.disabled = false;
      currentWeekBtn.disabled = weekOffset === 0;
    }
  }

  async function showWeek(offset) {
    weekOffset = offset;
    const weekStart = addDaysStr(currentWeekStart, weekOffset * 7);
    weekDates = Array.from({ length: 7 }, (_, index) => addDaysStr(weekStart, index));
    updateWeekHeader();
    await loadWeek();
  }

  previousWeekBtn.addEventListener("click", () => showWeek(weekOffset - 1));
  currentWeekBtn.addEventListener("click", () => showWeek(0));
  nextWeekBtn.addEventListener("click", () => showWeek(weekOffset + 1));
  updateWeekHeader();

  const weeklyCard = el("section", { class: "card timetable-section" },
    el("div", { class: "timetable-section-header" },
      el("div", {}, el("span", { class: "section-kicker" }, "实时安排"), weekTitle,
        el("p", { class: "muted" }, "已合并基础课表、临时调整和长期调整。")),
      el("div", { class: "schedule-meta" },
        weekRange,
        el("div", { class: "week-navigation", aria: { label: "切换课表周次" } }, previousWeekBtn, currentWeekBtn, nextWeekBtn),
        el("div", { class: "schedule-legend" }, el("span", {}, "基础课程"), el("span", { class: "changed" }, "调整后")))),
    weekHost);

  /* ---------- 课程调整 ---------- */

  function adjustmentHeading(number, title, description) {
    return el("div", { class: "adjustment-heading" },
      el("span", { class: "adjustment-number" }, String(number)),
      el("div", {}, el("h4", {}, title), el("p", { class: "muted" }, description)));
  }

  function flowArrow(symbol, label) {
    return el("div", { class: "adjustment-arrow", aria: { label } }, el("span", {}, symbol), el("small", {}, label));
  }

  function buildSingleAdjustment() {
    const dateInput = el("input", { type: "date", value: currentDate });
    const periodInput = periodSelect();
    const sourcePreview = el("div");
    const subjectInput = subjectSelect("选择替换科目");
    const teacherInput = el("input", { type: "text", placeholder: "可留空" });
    const roomInput = el("input", { type: "text", placeholder: "可留空" });
    const reasonInput = el("input", { type: "text", placeholder: "调整原因（可选）" });
    const resultHost = el("div");
    const applyBtn = el("button", { class: "primary", type: "button" }, "应用单课调整");
    let sourceLesson = null;
    bindLatestPreview(dateInput, periodInput, sourcePreview, { onLesson: (lesson) => { sourceLesson = lesson; } });

    applyBtn.addEventListener("click", async () => {
      if (!sourceLesson) { toast("所选日期和节次没有课程", "error"); return; }
      if (!subjectInput.value) { toast("请选择替换科目", "error"); return; }
      applyBtn.disabled = true;
      clear(resultHost);
      try {
        await api("/lesson-overrides", {
          method: "POST",
          body: {
            class_id: state.classId,
            lesson_date: dateInput.value,
            period_no: Number(periodInput.value),
            replacement_subject: subjectInput.value,
            replacement_teacher: teacherInput.value.trim() || null,
            replacement_room: roomInput.value.trim() || null,
            status: "normal",
            reason: reasonInput.value.trim() || "网页单课调整",
          },
        });
        toast("单课调整已生效", "success");
        resultHost.append(el("div", { class: "issue issue-ok" }, "已更新，本周课表和当前课程预览已同步。"));
        await refreshScheduleViews();
      } catch (error) {
        resultHost.append(errorPanel(error));
      } finally {
        applyBtn.disabled = false;
      }
    });

    return el("section", { class: "adjustment-panel" },
      adjustmentHeading(1, "单课调整", "选择一节最新课程，替换其中一项或多项信息。"),
      el("div", { class: "adjustment-flow" },
        el("div", { class: "adjustment-side source" }, el("b", {}, "当前课程"),
          el("div", { class: "adjustment-fields two" }, field("日期", dateInput), field("节次", periodInput)), sourcePreview),
        flowArrow("→", "替换为"),
        el("div", { class: "adjustment-side target" }, el("b", {}, "调整后"),
          el("div", { class: "adjustment-fields three" }, field("科目", subjectInput), field("教师（可选）", teacherInput), field("教室（可选）", roomInput)))),
      el("div", { class: "adjustment-footer" }, field("调整原因", reasonInput), applyBtn),
      resultHost);
  }

  function buildSwapAdjustment() {
    const dateA = el("input", { type: "date", value: currentDate });
    const dateB = el("input", { type: "date", value: currentDate });
    const periodA = periodSelect();
    const periodB = periodSelect(periods[1]?.period_no ?? periods[0]?.period_no ?? null);
    const previewA = el("div");
    const previewB = el("div");
    const outputSubject = el("input", { type: "text", readonly: true, placeholder: "自动读取" });
    const outputTeacher = el("input", { type: "text", readonly: true, placeholder: "自动读取" });
    const outputRoom = el("input", { type: "text", readonly: true, placeholder: "自动读取" });
    const reasonInput = el("input", { type: "text", placeholder: "互换原因（可选）" });
    const resultHost = el("div");
    const confirmBtn = el("button", { class: "primary", type: "button" }, "确认互换");
    let lessonA = null;
    let lessonB = null;

    bindLatestPreview(dateA, periodA, previewA, { onLesson: (lesson) => { lessonA = lesson; } });
    bindLatestPreview(dateB, periodB, previewB, {
      outputs: { subject: outputSubject, teacher: outputTeacher, room: outputRoom },
      onLesson: (lesson) => { lessonB = lesson; },
    });

    function swapBody() {
      return {
        class_id: state.classId,
        lesson_date_a: dateA.value,
        lesson_date_b: dateB.value,
        period_a: Number(periodA.value),
        period_b: Number(periodB.value),
        reason: reasonInput.value.trim() || "网页课程互换",
      };
    }

    [dateA, dateB, periodA, periodB, reasonInput].forEach((input) => input.addEventListener("change", () => clear(resultHost)));
    reasonInput.addEventListener("input", () => clear(resultHost));

    confirmBtn.addEventListener("click", async () => {
      if (!lessonA || !lessonB) { toast("两侧都必须选择有课程的日期和节次", "error"); return; }
      confirmBtn.disabled = true;
      clear(resultHost);
      try {
        const preview = await api("/lesson-swaps/preview", { method: "POST", body: swapBody() });
        if ((preview.conflicts || []).length) {
          resultHost.append(el("div", { class: "issue issue-error" }, "课程互换存在冲突，请重新选择。"));
          return;
        }
        await api("/lesson-swaps/confirm", { method: "POST", body: swapBody() });
        toast("课程互换已生效", "success");
        clear(resultHost);
        await refreshScheduleViews();
      } catch (error) {
        resultHost.append(errorPanel(error));
      } finally {
        confirmBtn.disabled = false;
      }
    });

    return el("section", { class: "adjustment-panel" },
      adjustmentHeading(2, "课程互换", "两节课可以跨日期互换，左右两侧都读取调整后的最新安排。"),
      el("div", { class: "adjustment-flow" },
        el("div", { class: "adjustment-side source" }, el("b", {}, "课程 A"),
          el("div", { class: "adjustment-fields two" }, field("日期", dateA), field("节次", periodA)), previewA),
        flowArrow("⇄", "相互交换"),
        el("div", { class: "adjustment-side target" }, el("b", {}, "课程 B"),
          el("div", { class: "adjustment-fields two" }, field("日期", dateB), field("节次", periodB)), previewB,
          el("div", { class: "adjustment-fields three read-only-course" },
            field("科目", outputSubject), field("教师", outputTeacher), field("教室", outputRoom)))),
      el("div", { class: "adjustment-footer" }, field("互换原因", reasonInput), confirmBtn),
      resultHost);
  }

  function buildBatchAdjustment() {
    const startInput = el("input", { type: "date", value: currentDate });
    const endInput = el("input", { type: "date", value: addDaysStr(currentDate, 28) });
    const weekdayInput = el("select", {},
      ...[1, 2, 3, 4, 5].map((day) => el("option", { value: String(day) }, WEEKDAY_NAMES[day])));
    weekdayInput.value = String(Math.min(weekdayOf(currentDate), 5));
    const periodInput = periodSelect();
    const sourcePreview = el("div", { class: "base-preview-list" });
    const subjectInput = subjectSelect("选择替换科目");
    const teacherInput = el("input", { type: "text", placeholder: "可留空" });
    const roomInput = el("input", { type: "text", placeholder: "可留空" });
    const reasonInput = el("input", { type: "text", placeholder: "长期调整原因（可选）" });
    const resultHost = el("div");
    const confirmBtn = el("button", { class: "primary", type: "button" }, "确认长期调整");

    function renderBasePreview() {
      const day = Number(weekdayInput.value);
      const lesson = baseItems.find((item) => item.weekday === day && item.period_no === Number(periodInput.value));
      sourcePreview.replaceChildren(el("div", { class: "base-preview-row" },
        el("b", {}, WEEKDAY_NAMES[day]),
        lesson
          ? el("span", {}, lesson.subject,
              el("small", { class: "muted" }, `${lesson.teacher || "未填写教师"} · ${lesson.room || "未填写教室"}`))
          : el("span", { class: "muted" }, "基础课表无课程")));
    }

    function batchBody() {
      return {
        class_id: state.classId,
        start_date: startInput.value,
        end_date: endInput.value,
        weekdays: [Number(weekdayInput.value)],
        period_no: Number(periodInput.value),
        replacement_subject: subjectInput.value || null,
        replacement_teacher: teacherInput.value.trim() || null,
        replacement_room: roomInput.value.trim() || null,
        status: "normal",
        reason: reasonInput.value.trim() || "网页长期课程调整",
      };
    }

    [startInput, endInput, weekdayInput, periodInput, subjectInput, teacherInput, roomInput, reasonInput].forEach((input) => {
      input.addEventListener("change", () => clear(resultHost));
      input.addEventListener("input", () => clear(resultHost));
    });
    periodInput.addEventListener("change", renderBasePreview);
    weekdayInput.addEventListener("change", renderBasePreview);
    renderBasePreview();

    confirmBtn.addEventListener("click", async () => {
      if (!subjectInput.value) { toast("请选择替换科目", "error"); return; }
      confirmBtn.disabled = true;
      clear(resultHost);
      try {
        const preview = await api("/lesson-batch-changes/preview", { method: "POST", body: batchBody() });
        const conflicts = preview.conflicts || [];
        if (conflicts.length) {
          resultHost.append(el("div", { class: "issue issue-error" }, `发现 ${conflicts.length} 项冲突，请调整范围。`));
          return;
        }
        await api("/lesson-batch-changes/confirm", { method: "POST", body: batchBody() });
        toast("长期调整已生效", "success");
        clear(resultHost);
        await refreshScheduleViews();
      } catch (error) {
        resultHost.append(errorPanel(error));
      } finally {
        confirmBtn.disabled = false;
      }
    });

    return el("section", { class: "adjustment-panel" },
      adjustmentHeading(3, "长期调整", "来源始终按基础课表预览，再批量应用到所选日期范围。"),
      el("div", { class: "adjustment-flow" },
        el("div", { class: "adjustment-side source" }, el("b", {}, "基础课程"),
          el("div", { class: "adjustment-fields two" }, field("开始日期", startInput), field("结束日期", endInput)),
          el("div", { class: "adjustment-fields one" }, field("星期", weekdayInput)),
          el("div", { class: "adjustment-fields one" }, field("节次", periodInput)),
          sourcePreview),
        flowArrow("→", "批量替换为"),
        el("div", { class: "adjustment-side target" }, el("b", {}, "调整后"),
          el("div", { class: "adjustment-fields three" }, field("科目", subjectInput), field("教师（可选）", teacherInput), field("教室（可选）", roomInput)))),
      el("div", { class: "adjustment-footer" }, field("调整原因", reasonInput), confirmBtn),
      resultHost);
  }

  const adjustmentCard = el("section", { class: "card timetable-section course-adjustments" },
    el("div", { class: "timetable-section-header" },
      el("div", {}, el("span", { class: "section-kicker" }, "调整"), el("h3", {}, "课程调整"),
        el("p", { class: "muted" }, "单课和互换读取最新安排；长期调整读取基础课表。"))),
    periods.length
      ? el("div", { class: "adjustment-list" }, buildSingleAdjustment(), buildSwapAdjustment(), buildBatchAdjustment())
      : emptyState("还没有可调整的节次", "请先在页面底部建立基础课表。"));

  /* ---------- 基础课表 ---------- */

  function buildBaseTimetable() {
    const editorHost = el("div");
    const importBox = el("div", { class: "import-panel hidden" });
    const analysisBox = el("div");
    const saveBtn = el("button", { class: "primary", type: "button", disabled: !periods.length }, "保存整张基础课表");
    const resetBtn = el("button", { class: "secondary", type: "button" }, "重新设置基础课表");
    const saveInfo = el("span", { class: "muted", style: { fontSize: "13px" } });
    const periodNameInput = el("input", { type: "text", placeholder: "节次名称（可选）" });
    const addPeriodBtn = el("button", { class: "secondary", type: "button" }, "新增一节");
    let importedPeriods = null;
    let editor = null;

    function showEditor(nextPeriods, nextItems) {
      periods = nextPeriods;
      editor = timetableGridEditor({ periods, items: nextItems, editable: true, defaultRoom: classInfo?.room || null });
      editorHost.replaceChildren(editor.el);
      saveBtn.disabled = false;
    }
    if (periods.length) showEditor(periods, baseItems);
    else editorHost.append(emptyState("还没有基础课表", "可以新增节次手动填写，也可以上传现有课表。"));

    addPeriodBtn.addEventListener("click", async () => {
      const no = periods.length ? Math.max(...periods.map((period) => period.period_no)) + 1 : 1;
      addPeriodBtn.disabled = true;
      try {
        await api(`/classes/${state.classId}/periods`, {
          method: "POST",
          body: { period_no: no, name: periodNameInput.value.trim() || null, sort_order: no },
        });
        toast("节次已创建", "success");
        await render(mount, ctx, helpers);
      } catch (error) {
        toast(error.message, "error");
        addPeriodBtn.disabled = false;
      }
    });

    const upload = fileDropzone({
      hint: "上传课表图片、PDF、Word、Excel 或文本文件",
      accept: ".xlsx,.xlsm,.docx,.pptx,.csv,.pdf,.png,.jpg,.jpeg,.webp,.txt,.md,.json",
      multiple: true,
      manualStart: true,
      busyText: "正在识别课表，请稍候…",
      onFiles: async (files, { signal }) => {
        clear(analysisBox);
        const form = new FormData();
        files.slice(0, 4).forEach((file) => form.append("files", file));
        try {
          const result = await api(`/classes/${state.classId}/timetable/import-preview`, {
            method: "POST",
            body: form,
            timeoutMs: AI_REQUEST_TIMEOUT_MS,
            signal,
          });
          importedPeriods = result.periods;
          showEditor(result.periods, result.items);
          analysisBox.append(
            el("div", { class: "issue issue-ok" }, `已识别 ${result.items.length} 节课，请核对后保存。`),
            ...(result.analysis?.warnings || []).map((text) => el("div", { class: "issue issue-warn" }, text)));
        } catch (error) {
          if (error.code === "REQUEST_CANCELLED") toast("已取消课表识别", "info");
          else analysisBox.append(errorPanel(error));
        }
      },
    });
    importBox.append(el("p", { class: "muted" }, "文件先生成可编辑预览，保存后才替换基础课表。"), upload, analysisBox);
    resetBtn.addEventListener("click", () => {
      importBox.classList.toggle("hidden");
      resetBtn.textContent = importBox.classList.contains("hidden") ? "重新设置基础课表" : "收起文件导入";
    });

    saveBtn.addEventListener("click", async () => {
      const items = editor?.getItems() || [];
      if (!items.length) { toast("课表至少需要一节课", "error"); return; }
      const accepted = await confirmDanger({
        title: "保存新的基础课表",
        lines: [`将用当前表格中的 ${items.length} 节课替换原基础课表。`, "已创建的课程调整不会自动删除。"],
        confirmLabel: "确认保存",
      });
      if (!accepted) return;
      saveBtn.disabled = true;
      saveInfo.textContent = "保存中…";
      try {
        if (importedPeriods) {
          await api(`/classes/${state.classId}/timetable/import-apply`, { method: "POST", body: { periods: importedPeriods, items } });
        } else {
          await api(`/classes/${state.classId}/timetable/base`, { method: "PUT", body: { items } });
        }
        toast("基础课表已保存", "success");
        await refreshSubjects({ force: true });
        await render(mount, ctx, helpers);
      } catch (error) {
        toast(error.message, "error");
        saveInfo.textContent = "保存失败，当前编辑内容仍保留";
        saveBtn.disabled = false;
      }
    });

    const periodSettings = el("details", { class: "period-settings" },
      el("summary", {}, `节次设置 · 当前 ${periods.length} 节`),
      el("div", { class: "row-gap" }, periodNameInput, addPeriodBtn));
    return el("section", { class: "card timetable-section" },
      el("div", { class: "timetable-section-header" },
        el("div", {}, el("span", { class: "section-kicker" }, "基础"), el("h3", {}, "基础课表"),
          el("p", { class: "muted" }, "周一至周五横向排列，点击单元格修改科目、教师和教室。")), resetBtn),
      importBox,
      periodSettings,
      editorHost,
      el("div", { class: "base-save-row" }, saveBtn, saveInfo));
  }

  host.append(weeklyCard, adjustmentCard, buildBaseTimetable());
  await loadWeek();
}
