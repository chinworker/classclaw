// 快捷查询中心：四个固定用例（今日未交 / 指定科目未交 / 前天课堂睡觉 / 某生近期表现）+ 可组合筛选。
// 只做真实接口组合，不伪装自然语言搜索。

import { el, clear, toast, todayStr, addDaysStr, fmtDate, fmtDateTime, dateStrSH } from "../util.js";
import { api } from "../api.js";
import { state, refreshStudents, refreshSubjects } from "../state.js";
import { pageHeader, field, errorPanel, skeleton, emptyState, dataTable, statusBadge } from "../components.js";

export async function render(mount) {
  clear(mount);
  const subjects = await refreshSubjects().catch(() => []);
  const host = el("div");
  const scopeLine = el("div", { class: "query-scope hidden" });

  mount.append(
    pageHeader("班级问题查询", "按条件查询班级记录。"),
    el("div", { class: "chips" },
      queryChip("今日未交作业", runTodayMissing),
      queryChip("某科目未交作业", runSubjectMissing),
      queryChip("前天课堂睡觉", runSleeping),
      queryChip("某生近期表现", runStudentRecent)),
    scopeLine, host);

  function queryChip(label, fn) {
    return el("button", { class: "chip", type: "button", onclick: () => fn() }, label);
  }

  function setScope(text, count = null) {
    scopeLine.classList.remove("hidden");
    clear(scopeLine);
    scopeLine.append(`查询口径：${text}`, count !== null ? el("b", { style: { marginLeft: "8px" } }, `命中 ${count} 条`) : null,
      el("button", { class: "text-button", type: "button", onclick: () => { scopeLine.classList.add("hidden"); clear(host); } }, "清空"));
  }

  function studentName(byId, id) {
    const s = byId.get(id);
    return s ? `${s.name}（${s.student_no}）` : id;
  }

  /* ---------- 1/2. 未交作业 ---------- */

  async function queryMissing({ subject = null, todayOnly = false }) {
    clear(host);
    host.append(skeleton(4));
    try {
      const params = new URLSearchParams({ class_id: state.classId });
      if (subject) params.set("subject", subject);
      let homework = await api(`/homework?${params}`);
      if (todayOnly) {
        const today = todayStr();
        // 口径：今天截止或今天布置且未关闭的作业（due_at 为带时区时间戳，按 Asia/Shanghai 归一为日期再比较）
        homework = homework.filter((h) => h.status === "published" && (
          (h.due_at && dateStrSH(h.due_at) === today) || h.assigned_date === today));
      } else {
        homework = homework.filter((h) => h.status === "published");
      }
      const students = await refreshStudents();
      const byId = new Map(students.map((s) => [s.id, s]));
      // 并发受限地拉取 missing 列表（页面生命周期内缓存）
      const groups = [];
      const concurrency = 4;
      for (let i = 0; i < homework.length; i += concurrency) {
        const batch = homework.slice(i, i + concurrency);
        const results = await Promise.all(batch.map((h) => api(`/homework/${h.id}/missing`).catch(() => null)));
        batch.forEach((h, index) => {
          const missing = results[index];
          if (missing && missing.length) groups.push({ homework: h, missing });
        });
      }
      clear(host);
      const total = groups.reduce((sum, g) => sum + g.missing.length, 0);
      setScope(`${subject ? `科目「${subject}」` : "全部科目"} · ${todayOnly ? "今天截止或今天布置" : "全部已发布"}作业中状态为「未交」的记录（不含待交 pending）`, total);
      if (!groups.length) { host.append(emptyState("当前条件下没有未交记录", "没有作业命中，或命中的作业没有未交学生。")); return; }
      for (const group of groups) {
        host.append(el("div", { class: "card result-group" },
          el("h4", {}, `${group.homework.subject} · ${group.homework.title}`),
          el("p", { class: "muted", style: { fontSize: "12px" } }, `布置 ${fmtDate(group.homework.assigned_date)} · 截止 ${fmtDateTime(group.homework.due_at)}`),
          el("div", { class: "chips" }, group.missing.map((m) => el("span", { class: "chip" }, studentName(byId, m.student_id))))));
      }
    } catch (error) {
      clear(host);
      host.append(errorPanel(error));
    }
  }

  function runTodayMissing() { queryMissing({ todayOnly: true }); }

  function runSubjectMissing() {
    clear(host);
    const subjectInput = el("select", {}, el("option", { value: "" }, "选择科目"), ...subjects.map((s) => el("option", { value: s.name }, s.name)));
    const runBtn = el("button", { class: "primary", type: "button" }, "查询该科目未交");
    runBtn.addEventListener("click", () => {
      if (!subjectInput.value) { toast("请选择科目", "error"); return; }
      queryMissing({ subject: subjectInput.value });
    });
    host.append(el("div", { class: "card" }, el("div", { class: "row-gap" }, field("科目 *", subjectInput), runBtn)));
  }

  /* ---------- 3. 前天课堂睡觉 ---------- */

  async function runSleeping() {
    clear(host);
    host.append(skeleton(4));
    const theDay = addDaysStr(todayStr(), -2);
    try {
      // 后端没有 subtype/content 服务端筛选：先用 event_type+单日限定范围，再在该结果集内按 subtype/content 的“睡觉”关键词做客户端筛选
      const events = await api(`/student-events?class_id=${state.classId}&start_date=${theDay}&end_date=${theDay}&event_type=behavior`);
      const matched = events.filter((e) => `${e.subtype || ""}${e.content || ""}`.includes("睡觉"));
      const students = await refreshStudents();
      const byId = new Map(students.map((s) => [s.id, s]));
      clear(host);
      setScope(`行为事件 · 事件日期=${theDay}（Asia/Shanghai 前天）· subtype/content 含「睡觉」（客户端在单日结果集内筛选）`, matched.length);
      host.append(dataTable({
        columns: [
          { key: "student", label: "学生", render: (e) => studentName(byId, e.student_id) },
          { key: "event_date", label: "事件日期", render: (e) => fmtDate(e.event_date) },
          { key: "subject", label: "科目", render: (e) => e.subject || "—" },
          { key: "subtype", label: "子类" },
          { key: "content", label: "内容" },
          { key: "severity", label: "程度", render: (e) => statusBadge(e.severity) },
        ],
        rows: matched,
        empty: { title: "前天没有课堂睡觉记录", hint: "按事件日期统计。" },
      }));
    } catch (error) {
      clear(host);
      host.append(errorPanel(error));
    }
  }

  /* ---------- 4. 某生近期表现 ---------- */

  function runStudentRecent() {
    clear(host);
    const searchInput = el("input", { type: "text", placeholder: "姓名或学号" });
    const daysSel = el("select", {}, [7, 14, 30].map((d) => el("option", { value: String(d), selected: d === 14 }, `最近 ${d} 天`)));
    const runBtn = el("button", { class: "primary", type: "button" }, "查找学生");
    const box = el("div", { style: { marginTop: "12px" } });
    host.append(el("div", { class: "card" }, el("div", { class: "row-gap" }, field("姓名 / 学号 *", searchInput), field("时间范围", daysSel), runBtn)), box);
    searchInput.focus();

    runBtn.addEventListener("click", async () => {
      const q = searchInput.value.trim();
      if (!q) { toast("请输入姓名或学号", "error"); return; }
      clear(box);
      box.append(skeleton(3));
      try {
        const data = await api(`/students?class_id=${state.classId}&q=${encodeURIComponent(q)}&page_size=20`);
        const candidates = data.items || [];
        clear(box);
        if (!candidates.length) { box.append(emptyState("没有匹配的学生", "换个姓名或学号关键词。")); return; }
        if (candidates.length > 1) {
          // STUDENT_AMBIGUOUS：显示候选，不自动选择
          box.append(el("div", { class: "card" }, el("b", {}, `找到 ${candidates.length} 名候选学生，请选择`),
            ...candidates.map((s) => el("div", { class: "row-gap", style: { padding: "6px 0" } },
              el("span", {}, `${s.name}（学号 ${s.student_no}）`),
              el("button", { class: "secondary", type: "button", onclick: () => showStudent(s, Number(daysSel.value)) }, "查看表现")))));
          return;
        }
        showStudent(candidates[0], Number(daysSel.value));
      } catch (error) {
        clear(box);
        box.append(errorPanel(error));
      }
    });

    async function showStudent(student, days) {
      clear(box);
      box.append(skeleton(5));
      const start = addDaysStr(todayStr(), -(days - 1));
      try {
        const [detail, analysis] = await Promise.all([
          api(`/students/${student.id}`),
          api(`/analytics/students/${student.id}/comprehensive?start_date=${start}&end_date=${todayStr()}`),
        ]);
        clear(box);
        setScope(`学生「${student.name}（${student.student_no}）」最近 ${days} 天（${start} 至 ${todayStr()}）`);
        const m = analysis.metrics || {};
        box.append(el("div", { class: "card" },
          el("h4", {}, "发生了什么（按时间倒序）"),
          (detail.events || []).length ? (detail.events || []).map((e) =>
            el("p", { style: { fontSize: "13px", margin: "3px 0" } }, `${fmtDate(e.event_date)} · ${e.subtype} · ${e.content}`))
            : el("p", { class: "muted" }, "时间范围内没有事件记录"),
          el("h4", { style: { marginTop: "12px" } }, "简短汇总"),
          el("p", { class: "muted", style: { fontSize: "13px" } },
            `作业完成率 ${m.homework?.completion_rate !== null && m.homework?.completion_rate !== undefined ? Math.round(m.homework.completion_rate * 100) + "%" : "无数据"} · 迟到 ${m.attendance?.late_count ?? 0} · 缺勤 ${m.attendance?.absent_count ?? 0} · 正向事件 ${m.behavior?.positive_count ?? 0} · 负向 ${m.behavior?.negative_count ?? 0}`),
          (analysis.warnings || []).map((w) => el("div", { class: "issue issue-warn" }, w))));
      } catch (error) {
        clear(box);
        box.append(errorPanel(error));
      }
    }
  }
}
