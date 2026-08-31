// 考勤：按日期+时段录入（PUT /attendance 直接写入）、查询、后端统计汇总。

import { el, clear, toast, todayStr, addDaysStr, fmtDate, pct } from "../util.js";
import { api } from "../api.js";
import { state, refreshStudents } from "../state.js";
import { pageHeader, dataTable, statusBadge, field, errorPanel, skeleton, emptyState } from "../components.js";

const PERIODS = [["full_day", "全天"], ["morning", "上午"], ["afternoon", "下午"], ["recess", "大课间"], ["care_1", "晚托一"], ["care_2", "晚托二"]];
const STATUSES = [["present", "出勤"], ["late", "迟到"], ["absent", "缺勤"], ["leave", "请假"]];

export async function render(mount) {
  const host = el("div");
  mount.append(pageHeader("考勤", "默认全员出勤，只需标记迟到、缺勤或请假。"));

  const tabs = el("div", { class: "tabs", role: "tablist" },
    el("button", { class: "active", type: "button", role: "tab", onclick: (e) => switchTab("record", e.target) }, "录入"),
    el("button", { type: "button", role: "tab", onclick: (e) => switchTab("query", e.target) }, "查询"),
    el("button", { type: "button", role: "tab", onclick: (e) => switchTab("summary", e.target) }, "统计"));
  mount.append(tabs, host);

  function switchTab(tab, btn) {
    tabs.querySelectorAll("button").forEach((b) => b.classList.toggle("active", b === btn));
    if (tab === "record") renderRecord();
    if (tab === "query") renderQuery();
    if (tab === "summary") renderSummary();
  }

  /* ---------- 录入 ---------- */
  async function renderRecord() {
    clear(host);
    const dateInput = el("input", { type: "date", value: todayStr() });
    const periodSelect = el("select", {}, PERIODS.map(([v, l]) => el("option", { value: v }, l)));
    const gridHost = el("div");
    host.append(
      el("div", { class: "filter-bar" },
        field("日期", dateInput), field("时段", periodSelect),
        el("button", { class: "secondary", type: "button", onclick: loadGrid }, "载入当天记录")),
      gridHost);

    async function loadGrid() {
      clear(gridHost);
      gridHost.append(skeleton(5));
      let students, records;
      try {
        [students, records] = await Promise.all([
          refreshStudents(),
          api(`/attendance?class_id=${state.classId}&start_date=${dateInput.value}&end_date=${dateInput.value}&period=${periodSelect.value}`),
        ]);
      } catch (error) {
        clear(gridHost);
        gridHost.append(errorPanel(error, { onRetry: loadGrid }));
        return;
      }
      clear(gridHost);
      if (!students.length) { gridHost.append(emptyState("没有学生", "请先在学生档案中添加学生。")); return; }
      const existing = new Map(records.map((r) => [r.student_id, r]));
      const selects = new Map();
      const optionGroups = new Map();
      const table = el("table", { class: "data-table" },
        el("thead", {}, el("tr", {}, el("th", {}, "学生"), el("th", {}, "考勤状态"), el("th", {}, "备注"))));
      const tbody = el("tbody");
      for (const s of students) {
        const current = existing.get(s.id)?.status || "present";
        selects.set(s.id, { status: current, note: existing.get(s.id)?.note || "" });
        const group = el("div", { class: "attendance-options" });
        for (const [value, label] of STATUSES) {
          const button = el("button", { class: `attendance-option status-${value}${value === current ? " active" : ""}`, type: "button" }, label);
          button.addEventListener("click", () => {
            selects.get(s.id).status = value;
            group.querySelectorAll("button").forEach((item) => item.classList.toggle("active", item === button));
          });
          group.append(button);
        }
        optionGroups.set(s.id, group);
        const noteInput = el("input", { type: "text", value: existing.get(s.id)?.note || "", placeholder: "可选" });
        noteInput.addEventListener("input", () => { selects.get(s.id).note = noteInput.value.trim(); });
        tbody.append(el("tr", {}, el("td", {}, el("b", {}, s.name), el("div", { class: "muted", style: { fontSize: "12px" } }, `学号 ${s.student_no}`)), el("td", {}, group), el("td", {}, noteInput)));
      }
      table.append(tbody);
      const allPresentBtn = el("button", { class: "secondary", type: "button" }, "全部设为出勤");
      allPresentBtn.addEventListener("click", () => {
        for (const student of students) {
          selects.get(student.id).status = "present";
          optionGroups.get(student.id).querySelectorAll("button").forEach((button) => button.classList.toggle("active", button.classList.contains("status-present")));
        }
      });
      const saveInfo = el("p", { class: "muted" }, "没有单独登记的学生按出勤计算。保存时只写入例外或对已有记录的更正。");
      const saveBtn = el("button", { class: "primary", type: "button" }, "保存考勤");
      saveBtn.addEventListener("click", async () => {
        saveBtn.disabled = true;
        let failed = 0;
        const changed = students.filter((s) => selects.get(s.id).status !== "present" || existing.has(s.id));
        for (const s of changed) {
          const record = selects.get(s.id);
          try {
            await api("/attendance", {
              method: "PUT",
              body: {
                class_id: state.classId, student_id: s.id, attendance_date: dateInput.value,
                period: periodSelect.value, status: record.status, note: record.note || null,
              },
            });
          } catch (error) {
            failed += 1;
            toast(`${s.name} 保存失败：${error.message}`, "error");
          }
        }
        saveBtn.disabled = false;
        if (!failed) toast(changed.length ? `已保存 ${changed.length} 条考勤记录，其余默认出勤` : "全员默认出勤，无需额外保存", "success");
        else toast(`${failed} 条保存失败，其余已保存`, "error");
      });
      gridHost.append(el("div", { class: "attendance-summary-strip" }, el("b", {}, `${students.length} 名学生`), el("span", {}, "默认状态：出勤"), allPresentBtn), el("div", { class: "table-wrap" }, table), saveInfo, saveBtn);
    }
    await loadGrid();
  }

  /* ---------- 查询 ---------- */
  function renderQuery() {
    clear(host);
    const startInput = el("input", { type: "date", value: addDaysStr(todayStr(), -7) });
    const endInput = el("input", { type: "date", value: todayStr() });
    const periodSelect = el("select", {}, el("option", { value: "" }, "全部时段"), ...PERIODS.map(([v, l]) => el("option", { value: v }, l)));
    const statusSelect = el("select", {}, el("option", { value: "" }, "全部状态"), ...STATUSES.map(([v, l]) => el("option", { value: v }, l)));
    const listHost = el("div");
    host.append(
      el("div", { class: "filter-bar" },
        field("开始日期", startInput), field("结束日期", endInput),
        field("时段", periodSelect), field("状态", statusSelect),
        el("button", { class: "primary", type: "button", onclick: load }, "查询"),
        el("button", { class: "text-button", type: "button", onclick: () => { startInput.value = addDaysStr(todayStr(), -7); endInput.value = todayStr(); periodSelect.value = ""; statusSelect.value = ""; load(); } }, "清空筛选")),
      listHost);

    async function load() {
      clear(listHost);
      listHost.append(skeleton(5));
      const params = new URLSearchParams({ class_id: state.classId });
      if (startInput.value) params.set("start_date", startInput.value);
      if (endInput.value) params.set("end_date", endInput.value);
      if (periodSelect.value) params.set("period", periodSelect.value);
      if (statusSelect.value) params.set("status", statusSelect.value);
      let rows;
      try {
        rows = await api(`/attendance?${params}`);
      } catch (error) {
        clear(listHost);
        listHost.append(errorPanel(error, { onRetry: load }));
        return;
      }
      const students = await refreshStudents().catch(() => []);
      const byId = new Map(students.map((s) => [s.id, s]));
      clear(listHost);
      listHost.append(dataTable({
        columns: [
          { key: "attendance_date", label: "日期", render: (r) => fmtDate(r.attendance_date) },
          { key: "student", label: "学生", render: (r) => { const s = byId.get(r.student_id); return s ? `${s.name}（${s.student_no}）` : r.student_id; } },
          { key: "period", label: "时段", render: (r) => (PERIODS.find(([v]) => v === r.period) || [])[1] || r.period },
          { key: "status", label: "状态", render: (r) => statusBadge(r.status) },
          { key: "note", label: "备注", render: (r) => r.note || "—" },
        ],
        rows,
        empty: { title: "当前条件下没有记录", hint: "考勤表只保存例外或点名记录，无记录不等于未出勤。" },
      }));
    }
    load();
  }

  /* ---------- 统计 ---------- */
  function renderSummary() {
    clear(host);
    const startInput = el("input", { type: "date", value: addDaysStr(todayStr(), -7) });
    const endInput = el("input", { type: "date", value: todayStr() });
    const box = el("div");
    host.append(
      el("div", { class: "filter-bar" },
        field("开始日期", startInput), field("结束日期", endInput),
        el("button", { class: "primary", type: "button", onclick: load }, "统计")),
      box);

    async function load() {
      clear(box);
      box.append(skeleton(4));
      let data;
      try {
        data = await api(`/attendance/summary?class_id=${state.classId}&start_date=${startInput.value}&end_date=${endInput.value}`);
      } catch (error) {
        clear(box);
        box.append(errorPanel(error, { onRetry: load }));
        return;
      }
      clear(box);
      const students = await refreshStudents().catch(() => []);
      const byId = new Map(students.map((s) => [s.id, s]));
      const rows = data.students || data.items || [];
      box.append(el("p", { class: "muted", style: { fontSize: "13px" } }, "说明：大课间记录不计入全天出勤率。"));
      box.append(dataTable({
        columns: [
          { key: "student", label: "学生", render: (r) => { const s = byId.get(r.student_id); return s ? `${s.name}（${s.student_no}）` : r.student_id; } },
          { key: "attendance_rate", label: "出勤率", render: (r) => pct(r.attendance_rate) },
          { key: "late", label: "迟到", render: (r) => r.late ?? r.late_count ?? 0 },
          { key: "absent", label: "缺勤", render: (r) => r.absent ?? r.absent_count ?? 0 },
          { key: "leave", label: "请假", render: (r) => r.leave ?? r.leave_count ?? 0 },
        ],
        rows,
        empty: { title: "范围内没有考勤记录", hint: "先在“录入”页签登记考勤。" },
      }));
    }
    load();
  }

  renderRecord();
}
