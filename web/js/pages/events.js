// 日常表现（学生事件）：查询视图 + 单条/批量登记（直接调领域接口）。

import { el, clear, toast, todayStr, addDaysStr, fmtDate } from "../util.js";
import { api } from "../api.js";
import { state, refreshStudents, refreshSubjects } from "../state.js";
import {
  pageHeader, dataTable, statusBadge, openModal, field, fieldError, errorPanel, skeleton, emptyState,
} from "../components.js";

const EVENT_TYPES = [["", "全部类型"], ["homework", "作业"], ["attendance", "考勤"], ["behavior", "行为"], ["communication", "沟通"], ["honor", "荣誉"], ["other", "其他"]];
export async function render(mount) {
  const subjects = await refreshSubjects().catch(() => []);
  const host = el("div");
  const startInput = el("input", { type: "date", value: addDaysStr(todayStr(), -14) });
  const endInput = el("input", { type: "date", value: todayStr() });
  const typeSelect = el("select", {}, EVENT_TYPES.map(([v, l]) => el("option", { value: v }, l)));
  const subjectInput = el("select", {}, el("option", { value: "" }, "全部科目"), ...subjects.map((s) => el("option", { value: s.name }, s.name)));
  const studentSelect = el("select", {}, el("option", { value: "" }, "全部学生"));

  mount.append(
    pageHeader("日常表现", "登记和查询学生表现。",
      el("button", { class: "primary", type: "button", onclick: () => openEventForm() }, "登记事件")),
    el("div", { class: "filter-bar" },
      field("开始日期", startInput), field("结束日期", endInput),
      field("学生", studentSelect), field("类型", typeSelect), field("科目", subjectInput),
      el("button", { class: "primary", type: "button", onclick: load }, "查询"),
      el("button", { class: "text-button", type: "button", onclick: () => { startInput.value = addDaysStr(todayStr(), -14); endInput.value = todayStr(); typeSelect.value = ""; subjectInput.value = ""; studentSelect.value = ""; load(); } }, "清空筛选")),
    el("p", { class: "muted", style: { fontSize: "12px" } }, "默认展示最近 14 天。"),
    host);

  refreshStudents().then((students) => {
    students.forEach((s) => studentSelect.append(el("option", { value: s.id }, `${s.name}（${s.student_no}）`)));
  }).catch(() => {});

  async function load() {
    clear(host);
    host.append(skeleton(5));
    const params = new URLSearchParams({ class_id: state.classId });
    if (startInput.value) params.set("start_date", startInput.value);
    if (endInput.value) params.set("end_date", endInput.value);
    if (studentSelect.value) params.set("student_id", studentSelect.value);
    if (typeSelect.value) params.set("event_type", typeSelect.value);
    if (subjectInput.value) params.set("subject", subjectInput.value);
    let rows;
    try {
      rows = await api(`/student-events?${params}`);
    } catch (error) {
      clear(host);
      host.append(errorPanel(error, { onRetry: load }));
      return;
    }
    const students = await refreshStudents().catch(() => []);
    const byId = new Map(students.map((s) => [s.id, s]));
    clear(host);
    host.append(dataTable({
      columns: [
        { key: "event_date", label: "日期", render: (e) => fmtDate(e.event_date) },
        { key: "student", label: "学生", render: (e) => { const s = byId.get(e.student_id); return s ? `${s.name}（${s.student_no}）` : e.student_id; } },
        { key: "event_type", label: "类型", render: (e) => (EVENT_TYPES.find(([v]) => v === e.event_type) || [])[1] || e.event_type },
        { key: "subtype", label: "子类" },
        { key: "subject", label: "科目", render: (e) => e.subject || "—" },
        { key: "content", label: "内容", render: (e) => el("span", { style: { fontSize: "13px" } }, e.content) },
        { key: "sentiment", label: "倾向", render: (e) => statusBadge(e.sentiment) },
        { key: "severity", label: "程度", render: (e) => statusBadge(e.severity) },
        { key: "actions", label: "操作", render: (e) => el("button", {
          class: "text-button", type: "button", style: { color: "var(--danger)" },
          onclick: async () => {
            try { await api(`/student-events/${e.id}`, { method: "DELETE" }); toast("事件已撤销", "success"); load(); }
            catch (error) { toast(error.message, "error"); }
          },
        }, "撤销") },
      ],
      rows,
      empty: { title: "当前条件下没有记录", hint: "调整日期范围或筛选条件。" },
    }));
  }

  async function openEventForm() {
    const students = await refreshStudents().catch(() => []);
    if (!students.length) { toast("请先添加学生", "error"); return; }
    const studentSel = el("select", {}, students.map((s) => el("option", { value: s.id }, `${s.name}（${s.student_no}）`)));
    const dateInput = el("input", { type: "date", value: todayStr() });
    const subjectInput = el("select", {}, el("option", { value: "" }, "不指定科目"), ...subjects.map((s) => el("option", { value: s.name }, s.name)));
    const contentInput = el("textarea", { rows: "4", placeholder: "直接描述发生了什么，例如：上课忘带课本、主动帮助同学" });
    const errorLine = el("div");
    const analysisBox = el("div");
    const analyzeBtn = el("button", { class: "secondary", type: "button" }, "让智能体判断");
    const saveBtn = el("button", { class: "primary", type: "button", disabled: true }, "确认登记");
    let analyzedEvent = null;
    let modal;
    const invalidateAnalysis = () => { analyzedEvent = null; saveBtn.disabled = true; clear(analysisBox); };
    contentInput.addEventListener("input", invalidateAnalysis);
    subjectInput.addEventListener("change", invalidateAnalysis);
    studentSel.addEventListener("change", invalidateAnalysis);
    dateInput.addEventListener("change", invalidateAnalysis);
    analyzeBtn.addEventListener("click", async () => {
      clear(errorLine);
      if (!contentInput.value.trim()) { errorLine.append(fieldError("请先填写事件内容")); return; }
      if (!dateInput.value) { errorLine.append(fieldError("日期必填")); return; }
      analyzeBtn.disabled = true;
      analysisBox.replaceChildren(el("p", { class: "muted" }, "正在判断类型、子类、倾向和程度…"));
      try {
        const result = await api(`/classes/${state.classId}/student-events/analyze`, {
          method: "POST",
          body: { student_id: studentSel.value, event_date: dateInput.value, content: contentInput.value.trim(), subject: subjectInput.value || null },
        });
        analyzedEvent = result.event;
        const typeLabel = (EVENT_TYPES.find(([value]) => value === analyzedEvent.event_type) || [])[1] || analyzedEvent.event_type;
        const sentimentLabel = { positive: "正向", neutral: "中性", negative: "负向" }[analyzedEvent.sentiment];
        const severityLabel = { normal: "普通", attention: "关注", serious: "严重" }[analyzedEvent.severity];
        analysisBox.replaceChildren(el("div", { class: "event-analysis-preview" },
          el("b", {}, "智能体判断"),
          el("span", {}, `${typeLabel} · ${analyzedEvent.subtype}`),
          el("span", {}, `${sentimentLabel} · ${severityLabel}${analyzedEvent.subject ? ` · ${analyzedEvent.subject}` : ""}`),
          result.summary ? el("small", { class: "muted" }, result.summary) : null));
        saveBtn.disabled = false;
      } catch (error) { analysisBox.replaceChildren(errorPanel(error)); }
      finally { analyzeBtn.disabled = false; }
    });
    saveBtn.addEventListener("click", async () => {
      if (!analyzedEvent) return;
      saveBtn.disabled = true;
      try {
        await api("/student-events", { method: "POST", body: analyzedEvent });
        modal.close();
        toast("事件已登记", "success");
        load();
      } catch (error) { errorLine.replaceChildren(fieldError(error.message)); saveBtn.disabled = false; }
    });
    modal = openModal({
      title: "登记学生事件",
      body: el("div", {},
        el("div", { class: "form-grid" },
          field("学生 *", studentSel), field("日期 *", dateInput), field("科目", subjectInput)),
        field("内容 *", contentInput),
        el("p", { class: "muted", style: { fontSize: "12px" } }, "子类、倾向和程度由智能体严格判断。未交、忘带、迟到、吵闹、睡觉等均按负向处理。"),
        el("div", { class: "row-gap" }, analyzeBtn, saveBtn), analysisBox,
        errorLine),
      actions: [{ label: "关闭", kind: "secondary" }],
    });
  }

  await load();
}
