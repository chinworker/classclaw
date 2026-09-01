// 数据分析：学生分析 / 班级分析 / 重点关注。展示时间范围、覆盖度、指标、趋势、证据与警告。

import { el, clear, todayStr, addDaysStr, pct } from "../util.js";
import { api } from "../api.js";
import { state, refreshStudents, refreshSubjects } from "../state.js";
import { pageHeader, field, errorPanel, skeleton, emptyState, dataTable, jsonDetails, metricCard } from "../components.js";

const PRESETS = [
  ["近 7 天", () => [addDaysStr(todayStr(), -6), todayStr()]],
  ["近 14 天", () => [addDaysStr(todayStr(), -13), todayStr()]],
  ["近 30 天", () => [addDaysStr(todayStr(), -29), todayStr()]],
  ["本周", () => { const d = new Date(); const diff = (d.getDay() || 7) - 1; return [addDaysStr(todayStr(), -diff), todayStr()]; }],
  ["本月", () => { const d = new Date(); return [`${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-01`, todayStr()]; }],
];

export async function render(mount, ctx) {
  clear(mount);
  const section = ctx.path.split("/")[2] || "students";
  const titles = { students: "学生分析", class: "班级分析", attention: "重点关注" };

  const startInput = el("input", { type: "date", value: addDaysStr(todayStr(), -13) });
  const endInput = el("input", { type: "date", value: todayStr() });
  const subjects = await refreshSubjects().catch(() => []);
  const subjectInput = el("select", {}, el("option", { value: "" }, "全部科目"), ...subjects.map((s) => el("option", { value: s.name }, s.name)));
  const host = el("div");

  const presetBar = el("div", { class: "chips" }, PRESETS.map(([label, fn]) =>
    el("button", { class: "chip", type: "button", onclick: () => { const [s, e] = fn(); startInput.value = s; endInput.value = e; load(); } }, label)));

  mount.append(pageHeader(titles[section] || "分析", "按所选时段查看。"));

  if (section === "students") {
    const studentSel = el("select", {}, el("option", { value: "" }, "选择学生…"));
    mount.append(el("div", { class: "filter-bar" },
      field("学生 *", studentSel), field("开始日期", startInput), field("结束日期", endInput), field("科目", subjectInput),
      el("button", { class: "primary", type: "button", onclick: load }, "分析")), presetBar, host);
    const students = await refreshStudents().catch(() => []);
    students.forEach((s) => studentSel.append(el("option", { value: s.id }, `${s.name}（${s.student_no}）`)));

    async function load() {
      if (!studentSel.value) { toast0(); return; }
      clear(host);
      host.append(skeleton(5));
      try {
        const data = await api(`/analytics/students/${studentSel.value}/comprehensive?start_date=${startInput.value}&end_date=${endInput.value}${subjectInput.value ? `&subject=${encodeURIComponent(subjectInput.value)}` : ""}`);
        renderStudentAnalysis(data);
      } catch (error) {
        clear(host);
        host.append(errorPanel(error, { onRetry: load }));
      }
    }
    function toast0() { host.replaceChildren(emptyState("请选择学生", "从下拉框选择要分析的学生。")); }
    return;
  }

  mount.append(el("div", { class: "filter-bar" },
    field("开始日期", startInput), field("结束日期", endInput),
    section === "class" ? field("科目", subjectInput) : null,
    el("button", { class: "primary", type: "button", onclick: load }, "分析")), presetBar, host);

  async function load() {
    clear(host);
    host.append(skeleton(5));
    try {
      if (section === "class") {
        const data = await api(`/analytics/classes/${state.classId}/comprehensive?start_date=${startInput.value}&end_date=${endInput.value}${subjectInput.value ? `&subject=${encodeURIComponent(subjectInput.value)}` : ""}`);
        renderClassAnalysis(data);
      } else {
        const [attention, quality, cross] = await Promise.all([
          api(`/analytics/classes/${state.classId}/attention-students?start_date=${startInput.value}&end_date=${endInput.value}`),
          api(`/analytics/classes/${state.classId}/data-quality?start_date=${startInput.value}&end_date=${endInput.value}`),
          api(`/analytics/classes/${state.classId}/cross-module?start_date=${startInput.value}&end_date=${endInput.value}`),
        ]);
        renderAttention(attention, quality, cross);
      }
    } catch (error) {
      clear(host);
      host.append(errorPanel(error, { onRetry: load }));
    }
  }

  function periodNote(data) {
    return el("p", { class: "muted", style: { fontSize: "13px" } },
      `时间范围：${data.period?.start_date || startInput.value} 至 ${data.period?.end_date || endInput.value}`);
  }

  function warningsBlock(warnings) {
    return warnings?.length ? el("div", {}, warnings.map((w) => el("div", { class: "issue issue-warn" }, w))) : null;
  }

  function renderStudentAnalysis(data) {
    clear(host);
    const m = data.metrics || {};
    host.append(el("div", { class: "card" },
      el("h3", {}, `${data.student?.name || ""} · 综合分析`),
      periodNote(data),
      el("div", { class: "metric-grid" },
        metricCard("作业完成率", pct(m.homework?.completion_rate), `未交 ${m.homework?.missing_count ?? 0} · 迟交 ${m.homework?.late_count ?? 0} · 需订正 ${m.homework?.revision_required_count ?? 0}`),
        metricCard("出勤率", pct(m.attendance?.attendance_rate), `迟到 ${m.attendance?.late_count ?? 0} · 缺勤 ${m.attendance?.absent_count ?? 0} · 请假 ${m.attendance?.leave_count ?? 0}`),
        metricCard("行为事件", `${m.behavior?.positive_count ?? 0} 正 / ${m.behavior?.negative_count ?? 0} 负`, `严重 ${m.behavior?.serious_count ?? 0} 条`),
        metricCard("值日完成率", pct(m.duty?.completion_rate), `共 ${m.duty?.total ?? 0} 次`),
        metricCard("当前座位", m.seat ? `第 ${m.seat.row} 排 ${m.seat.col} 座` : "未排座")),
      el("p", { class: "muted", style: { fontSize: "13px" } },
        `数据覆盖：作业 ${data.data_coverage?.homework_count ?? 0} · 考勤例外 ${data.data_coverage?.attendance_exception_records ?? 0} · 事件 ${data.data_coverage?.event_count ?? 0} · 成绩 ${data.data_coverage?.exam_count ?? 0} · 值日 ${data.data_coverage?.duty_count ?? 0}`)));

    const timeline = m.scores?.timeline || [];
    if (timeline.length) {
      host.append(el("div", { class: "card" }, el("h3", {}, "成绩轨迹"),
        dataTable({
          columns: [
            { key: "exam_name", label: "考试", render: (t) => t.exam_name || t.exam_id || "—" },
            { key: "exam_date", label: "日期", render: (t) => t.exam_date || "—" },
            { key: "subjects", label: "各科", render: (t) => (t.subjects || []).map((s) => `${s.subject} ${s.score}`).join("、") },
          ],
          rows: timeline,
        }),
        data.trends?.length ? el("p", { class: "muted" }, `趋势：${data.trends.map((t) => `${t.metric} ${t.direction === "up" ? "↑" : t.direction === "down" ? "↓" : "→"} ${t.change !== null && t.change !== undefined ? (t.change * 100).toFixed(1) + "%" : ""}`).join("；")}`) : null));
    }
    if (data.attention_items?.length) {
      host.append(el("div", { class: "card" }, el("h3", {}, "关注项"),
        ...data.attention_items.map((a) => el("div", { class: "issue issue-warn" }, a.message || JSON.stringify(a)))));
    }
    if (data.positive_items?.length) {
      host.append(el("div", { class: "card" }, el("h3", {}, "正向记录"),
        ...data.positive_items.map((p) => el("p", { class: "muted", style: { fontSize: "13px" } }, `${p.date} · ${p.summary}`))));
    }
    host.append(el("div", { class: "card" }, el("h3", {}, "证据与警告"),
      warningsBlock(data.warnings),
      data.evidence?.length ? jsonDetails(data.evidence, "证据明细") : el("p", { class: "muted" }, "无额外证据记录")));
  }

  function renderClassAnalysis(data) {
    clear(host);
    const m = data.metrics || {};
    host.append(el("div", { class: "card" },
      el("h3", {}, `${data.class?.name || ""} · 班级综合分析`),
      periodNote(data),
      el("div", { class: "metric-grid" },
        metricCard("学生数", data.student_count ?? "—"),
        metricCard("作业完成率", pct(m.homework_completion_rate), `未交 ${m.missing_homework_count ?? 0} 条 / ${m.missing_homework_students ?? 0} 人`),
        metricCard("出勤率", pct(m.attendance_rate), `迟到 ${m.late_students ?? 0} 人 · 缺勤 ${m.absent_students ?? 0} 人`)),
      m.subject_averages && Object.keys(m.subject_averages).length
        ? el("div", {}, el("b", {}, "各科平均分"), el("div", { class: "chips", style: { marginTop: "6px" } },
            Object.entries(m.subject_averages).map(([name, avg]) => el("span", { class: "chip" }, `${name} ${avg}`))))
        : el("p", { class: "muted" }, "范围内没有成绩数据"),
      warningsBlock(data.warnings)));
    const neg = data.frequent_negative_behaviors || [];
    const pos = data.frequent_positive_behaviors || [];
    if (neg.length || pos.length) {
      const behaviorColumn = (title, rows) => el("div", { style: { flex: 1, minWidth: "220px" } },
        el("b", {}, title),
        ...(rows.length
          ? rows.map(([name, count]) => el("p", { class: "muted" }, `${name} × ${count}`))
          : [el("p", { class: "muted" }, "无")]));
      host.append(el("div", { class: "card" }, el("h3", {}, "高频行为"),
        el("div", { class: "row-gap", style: { alignItems: "flex-start" } },
          behaviorColumn("负向", neg),
          behaviorColumn("正向", pos))));
    }
    renderAttentionStudents(data.attention_students || [], "本周期关注学生");
  }

  function renderAttentionStudents(list, title) {
    host.append(el("div", { class: "card" }, el("h3", {}, `${title}（${list.length}）`),
      list.length ? dataTable({
        columns: [
          { key: "student", label: "学生", render: (s) => s.student?.name || "—" },
          { key: "reasons", label: "触发原因", render: (s) => (s.reasons || []).map((r) => r.message || r).join("；") },
        ],
        rows: list,
      }) : el("p", { class: "muted" }, "没有触发关注规则的学生"),
      el("p", { class: "muted", style: { fontSize: "12px" } }, "关注结果是当前时间范围内的规则触发，不是永久标签。")));
  }

  function renderAttention(attention, quality, cross) {
    clear(host);
    renderAttentionStudents(attention.students || [], "重点关注");
    host.append(el("div", { class: "card" }, el("h3", {}, "数据质量"),
      el("p", { class: "muted", style: { fontSize: "13px" } },
        `覆盖：学生 ${quality.counts?.students ?? 0} · 作业 ${quality.counts?.homework ?? 0} · 事件 ${quality.counts?.events ?? 0} · 考勤例外 ${quality.counts?.attendance_exception_records ?? 0} · 考试 ${quality.counts?.exams ?? 0}`),
      quality.missing_dimensions?.length ? el("p", { class: "muted" }, `缺口维度：${quality.missing_dimensions.join("、")}`) : null,
      warningsBlock(quality.warnings)));
    host.append(el("div", { class: "card" }, el("h3", {}, "跨模块发现"),
      el("p", { class: "muted", style: { fontSize: "12px" } }, cross.principle || "仅报告同时出现、可能相关和值得关注，不推断因果关系。"),
      (cross.findings || []).length ? cross.findings.map((f) => el("div", { class: "issue issue-warn" }, f.message || JSON.stringify(f))) : el("p", { class: "muted" }, "没有跨模块发现")));
  }

  if (section !== "students") await load();
}
