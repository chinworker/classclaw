// TimetableGridEditor：周一至周五横轴 × 节次纵轴的课表编辑器。
// onboarding 与正式课表页复用，区别只在数据来源与保存适配器。

import { el, clear, WEEKDAY_NAMES, periodLabel, toast } from "./util.js";

const SUBJECT_HUES = new Map();
let hueSeed = 0;
function subjectTone(subject) {
  if (!SUBJECT_HUES.has(subject)) SUBJECT_HUES.set(subject, (hueSeed += 47) % 360);
  return SUBJECT_HUES.get(subject);
}

export function timetableGridEditor({ periods = [], items = [], editable = true, onChange = null, defaultRoom = null }) {
  const sortedPeriods = [...periods].sort((a, b) => (a.sort_order - b.sort_order) || (a.period_no - b.period_no));
  // cell key: `${weekday}:${period_no}`
  const cells = new Map();
  for (const item of items) {
    if (!item.subject) continue;
    cells.set(`${item.weekday}:${item.period_no}`, { subject: item.subject, teacher: item.teacher || "", room: item.room || "" });
  }

  let container = null;
  let tableBox = null;
  let weekendBox = null;
  let editingCell = null;

  function emit() {
    renderAll();
    if (onChange) onChange(getItems());
  }

  function getItems() {
    const out = [];
    const seen = new Set();
    for (const period of sortedPeriods) {
      for (let weekday = 1; weekday <= 7; weekday++) {
        const key = `${weekday}:${period.period_no}`;
        const cell = cells.get(key);
        if (!cell || !cell.subject.trim()) continue;
        if (seen.has(key)) continue;
        seen.add(key);
        out.push({
          weekday,
          period_no: period.period_no,
          subject: cell.subject.trim(),
          teacher: cell.teacher?.trim() || null,
          room: cell.room?.trim() || defaultRoom || null,
        });
      }
    }
    return out;
  }

  function cellContent(weekday, periodNo) {
    const cell = cells.get(`${weekday}:${periodNo}`);
    if (!cell || !cell.subject.trim()) {
      return el("span", { class: "cell-empty muted" }, editable ? "＋ 添加" : "—");
    }
    const hue = subjectTone(cell.subject.trim());
    return el("span", { class: "cell-lesson", style: { borderLeftColor: `hsl(${hue} 35% 45%)` } },
      el("b", {}, cell.subject),
      cell.teacher ? el("span", { class: "cell-teacher" }, cell.teacher) : null,
      cell.room ? el("span", { class: "cell-room muted" }, cell.room) : null);
  }

  function openCellEditor(weekday, periodNo, anchor) {
    if (!editable) return;
    closeCellEditor();
    const key = `${weekday}:${periodNo}`;
    const cell = cells.get(key) || { subject: "", teacher: "", room: "" };
    const subjectInput = el("input", { type: "text", value: cell.subject, placeholder: "科目 *", maxlength: "100" });
    const teacherInput = el("input", { type: "text", value: cell.teacher, placeholder: "任课老师", maxlength: "100" });
    const roomInput = el("input", { type: "text", value: cell.room, placeholder: "教室（可选）", maxlength: "100" });
    const label = periodLabel(sortedPeriods.find((period) => period.period_no === periodNo) || periodNo);
    const save = el("button", { class: "primary", type: "button" }, "保存");
    const clearBtn = el("button", { class: "secondary", type: "button" }, "清空此格");
    const pop = el("div", { class: "cell-popover", role: "dialog", "aria-label": `${WEEKDAY_NAMES[weekday]} ${label}` },
      el("b", {}, `${WEEKDAY_NAMES[weekday]} · ${label}`),
      subjectInput, teacherInput, roomInput,
      el("div", { class: "row-gap" }, save, clearBtn));
    // The editor lives inside a clickable table cell. Keep form clicks from reopening
    // the cell editor and replacing the focused input.
    pop.addEventListener("click", (event) => event.stopPropagation());
    function commit() {
      const subject = subjectInput.value.trim();
      if (!subject) { toast("科目不能为空；要删除课程请点“清空此格”", "error"); return; }
      cells.set(key, { subject, teacher: teacherInput.value.trim(), room: roomInput.value.trim() });
      closeCellEditor();
      emit();
    }
    save.addEventListener("click", commit);
    subjectInput.addEventListener("keydown", (e) => { if (e.key === "Enter") commit(); });
    teacherInput.addEventListener("keydown", (e) => { if (e.key === "Enter") commit(); });
    roomInput.addEventListener("keydown", (e) => { if (e.key === "Enter") commit(); });
    clearBtn.addEventListener("click", () => { cells.delete(key); closeCellEditor(); emit(); });
    pop.addEventListener("keydown", (e) => { if (e.key === "Escape") { e.stopPropagation(); closeCellEditor(); anchor.focus(); } });
    anchor.append(pop);
    editingCell = { anchor, pop };
    subjectInput.focus();
  }

  function closeCellEditor() {
    if (editingCell) { editingCell.pop.remove(); editingCell = null; }
  }

  function focusCell(weekday, periodNo) {
    const target = tableBox.querySelector(`[data-cell="${weekday}:${periodNo}"]`);
    if (target) target.focus();
  }

  function buildTable(weekdays, host, weekend = false) {
    const table = el("table", { class: "tt-grid" });
    table.append(el("thead", {}, el("tr", {},
      el("th", { scope: "col", class: "tt-period-col" }, "节次"),
      weekdays.map((d) => el("th", { scope: "col" }, WEEKDAY_NAMES[d])))));
    const tbody = el("tbody");
    for (const period of sortedPeriods) {
      const tr = el("tr");
      tr.append(el("th", { scope: "row", class: "tt-period-col" }, el("b", {}, periodLabel(period))));
      for (const weekday of weekdays) {
        const td = el("td", {
          class: "tt-cell",
          tabindex: editable ? "0" : null,
          dataset: { cell: `${weekday}:${period.period_no}` },
          role: editable ? "button" : null,
          "aria-label": `${WEEKDAY_NAMES[weekday]}${periodLabel(period)}`,
        }, cellContent(weekday, period.period_no));
        if (editable) {
          td.addEventListener("click", () => openCellEditor(weekday, period.period_no, td));
          td.addEventListener("keydown", (e) => {
            const rowIndex = sortedPeriods.indexOf(period);
            const colIndex = weekdays.indexOf(weekday);
            if (e.key === "Enter" || e.key === " ") { e.preventDefault(); openCellEditor(weekday, period.period_no, td); }
            if (e.key === "ArrowDown" && rowIndex < sortedPeriods.length - 1) { e.preventDefault(); focusCell(weekday, sortedPeriods[rowIndex + 1].period_no); }
            if (e.key === "ArrowUp" && rowIndex > 0) { e.preventDefault(); focusCell(weekday, sortedPeriods[rowIndex - 1].period_no); }
            if (e.key === "ArrowRight" && colIndex < weekdays.length - 1) { e.preventDefault(); focusCell(weekdays[colIndex + 1], period.period_no); }
            if (e.key === "ArrowLeft" && colIndex > 0) { e.preventDefault(); focusCell(weekdays[colIndex - 1], period.period_no); }
            // 复制上一格（上一节同 weekday）
            if (e.key === "c" && (e.ctrlKey || e.metaKey) === false && e.altKey) {
              const prev = sortedPeriods[rowIndex - 1];
              const source = prev && cells.get(`${weekday}:${prev.period_no}`);
              if (source) { cells.set(`${weekday}:${period.period_no}`, { ...source }); emit(); toast("已复制上一格"); }
            }
          });
        }
        tr.append(td);
      }
      tbody.append(tr);
    }
    table.append(tbody);
    clear(host);
    host.append(table);
  }

  function renderAll() {
    closeCellEditor();
    buildTable([1, 2, 3, 4, 5], tableBox);
    const hasWeekend = [...cells.keys()].some((key) => Number(key.split(":")[0]) >= 6);
    if (hasWeekend || editable) {
      weekendBox.classList.remove("hidden");
      const content = weekendBox.querySelector(".weekend-content");
      buildTable([6, 7], content, true);
      if (!hasWeekend) content.append(el("p", { class: "muted" }, "周末暂无课程，可在此添加"));
    } else {
      weekendBox.classList.add("hidden");
    }
  }

  const weekendContent = el("div", { class: "weekend-content" });
  weekendBox = el("details", { class: "weekend-box hidden" },
    el("summary", {}, "周末（周六 / 周日）"), weekendContent);
  tableBox = el("div", { class: "tt-scroll" });
  container = el("div", { class: "tt-editor" }, tableBox, weekendBox);
  renderAll();

  return {
    el: container,
    getItems,
    setItems(nextItems) {
      cells.clear();
      for (const item of nextItems) {
        if (item.subject) cells.set(`${item.weekday}:${item.period_no}`, { subject: item.subject, teacher: item.teacher || "", room: item.room || "" });
      }
      renderAll();
    },
    setPeriods(nextPeriods) {
      sortedPeriods.length = 0;
      sortedPeriods.push(...[...nextPeriods].sort((a, b) => (a.sort_order - b.sort_order) || (a.period_no - b.period_no)));
      renderAll();
    },
  };
}
