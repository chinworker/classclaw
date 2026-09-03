// 成绩与考试：查询/创建考试、批量录入成绩、统计。

import { el, clear, toast, todayStr, fmtDate, fmtDateTime, pct } from "../util.js";
import { api } from "../api.js";
import { state, refreshStudents, refreshSubjects } from "../state.js";
import { pageHeader, dataTable, openModal, field, fieldError, errorPanel, skeleton, emptyState, statusBadge, metricCard, confirmDanger } from "../components.js";
import { listExams } from "../featureGaps.js";

export async function render(mount) {
  const classSubjects = await refreshSubjects().catch(() => []);
  const host = el("div");
  mount.append(
    pageHeader("成绩与考试", "创建考试，录入成绩。",
      el("button", { class: "primary", type: "button", onclick: openCreateExam }, "创建考试")),
    host);

  async function load() {
    clear(host);
    host.append(skeleton(4));
    try {
      const exams = await listExams(state.classId);
      renderExamList(exams || []);
    } catch (error) {
      clear(host);
      host.append(errorPanel(error, { onRetry: load }));
    }
  }

  function renderExamList(exams) {
    clear(host);
    if (!exams.length) {
      host.append(emptyState("暂无考试", "点击“创建考试”开始记录成绩。"));
    } else {
      host.append(dataTable({
        columns: [
          { key: "name", label: "考试", render: (e2) => el("b", {}, e2.name) },
          { key: "exam_date", label: "日期", render: (e2) => fmtDate(e2.exam_date) },
          { key: "status", label: "状态", render: (e2) => statusBadge(e2.status) },
          { key: "subjects", label: "科目", render: (e2) => (e2.subjects || []).map((s) => `${s.subject}（满分 ${s.full_score}）`).join("、") },
          { key: "actions", label: "操作", render: (e2) => el("div", { class: "row-gap" },
            el("button", { class: "text-button", type: "button", onclick: () => openScores(e2) }, "录入/查看成绩"),
            el("button", { class: "text-button", type: "button", onclick: () => openStatistics(e2) }, "统计"),
            el("button", { class: "text-button", type: "button", style: { color: "var(--danger)" }, onclick: () => removeExam(e2) }, "删除")) },
        ],
        rows: exams,
      }));
    }
  }

  function openCreateExam() {
    const nameInput = el("input", { type: "text", placeholder: "如：2026 秋季期中考试" });
    const dateInput = el("input", { type: "date", value: todayStr() });
    const subjectRows = el("tbody");
    const addSubject = (row = {}) => {
      const name = el("select", {}, el("option", { value: "" }, "选择科目"), ...classSubjects.map((s) => el("option", { value: s.name }, s.name)));
      name.value = row.subject || "";
      const full = el("input", { type: "number", min: "1", value: row.full_score ?? "100", style: { width: "90px" } });
      const tr = el("tr", {}, el("td", {}, name), el("td", {}, full),
        el("td", {}, el("button", { class: "text-button", type: "button", onclick: () => tr.remove() }, "删除")));
      subjectRows.append(tr);
    };
    addSubject();
    const errorLine = el("div");
    openModal({
      title: "创建考试",
      body: el("div", {},
        field("考试名称 *", nameInput), field("考试日期 *", dateInput),
        el("b", { style: { display: "block", margin: "8px 0 4px" } }, "科目与满分 *"),
        el("div", { class: "table-wrap", style: { maxWidth: "420px" } },
          el("table", { class: "data-table" }, el("thead", {}, el("tr", {}, el("th", {}, "科目"), el("th", {}, "满分"), el("th", {}, ""))), subjectRows)),
        el("button", { class: "secondary", type: "button", onclick: () => addSubject(), style: { marginTop: "8px" } }, "添加科目"),
        errorLine),
      actions: [
        { label: "取消", kind: "secondary" },
        {
          label: "创建", kind: "primary",
          onClick: async ({ close, setSubmitting }) => {
            clear(errorLine);
            const subjects = [...subjectRows.querySelectorAll("tr")].map((tr) => {
              const nameInput2 = tr.querySelector("select");
              const fullInput = tr.querySelector('input[type="number"]');
              return { subject: nameInput2.value, full_score: Number(fullInput.value) };
            }).filter((s) => s.subject);
            if (!nameInput.value.trim()) { errorLine.append(fieldError("考试名称必填")); return; }
            if (!dateInput.value) { errorLine.append(fieldError("考试日期必填")); return; }
            if (!subjects.length) { errorLine.append(fieldError("至少一个科目")); return; }
            if (subjects.some((s) => !(s.full_score > 0))) { errorLine.append(fieldError("满分必须大于 0")); return; }
            setSubmitting(true);
            try {
              await api("/exams", {
                method: "POST",
                body: { class_id: state.classId, name: nameInput.value.trim(), exam_date: dateInput.value, subjects },
              });
              close();
              toast("考试已创建", "success");
              load();
            } catch (error) { errorLine.append(fieldError(error.message)); }
            finally { setSubmitting(false); }
          },
        },
      ],
    });
  }

  async function removeExam(exam) {
    const confirmed = await confirmDanger({
      title: "删除考试",
      lines: [
        `将永久删除考试「${exam.name}」及其全部科目、成绩与统计缓存。`,
        "删除后无法恢复，如需保留成绩请先导出。",
      ],
      requireText: exam.name,
      confirmLabel: "确认删除",
    });
    if (!confirmed) return;
    try {
      await api(`/exams/${exam.id}`, { method: "DELETE" });
      toast("考试已删除", "success");
      load();
    } catch (error) {
      toast(error.message, "error");
    }
  }

  async function openScores(exam, preloaded = null) {
    const body = el("div", {}, skeleton(5));
    const modal = openModal({ title: `成绩录入 · ${exam.name}`, wide: true, headClose: false, body, actions: [{ label: "关闭", kind: "secondary" }] });
    try {
      const [students, scores] = await Promise.all([
        refreshStudents(),
        preloaded ? Promise.resolve(preloaded) : api(`/exams/${exam.id}/scores`),
      ]);
      clear(body);
      if (!students.length) { body.append(emptyState("没有学生", "请先添加学生。")); return; }
      const byKey = new Map(scores.map((s) => [`${s.student_id}:${s.subject}`, s]));
      const inputs = [];
      const table = el("table", { class: "data-table" });
      table.append(el("thead", {}, el("tr", {}, el("th", {}, "学生"), ...exam.subjects.map((s) => el("th", {}, `${s.subject}（${s.full_score}）`)))));
      const tbody = el("tbody");
      for (const student of students) {
        const tr = el("tr", {}, el("td", {}, `${student.name}（${student.student_no}）`));
        for (const subj of exam.subjects) {
          const existing = byKey.get(`${student.id}:${subj.subject}`);
          const input = el("input", { type: "number", min: "0", max: String(subj.full_score), step: "0.5", value: existing?.score ?? "", style: { width: "76px" } });
          inputs.push({ student, subject: subj, input });
          tr.append(el("td", {}, input));
        }
        tbody.append(tr);
      }
      table.append(tbody);
      const errorLine = el("div");
      const saveInfo = el("p", { class: "muted" });
      const saveBtn = el("button", { class: "primary", type: "button" }, "保存成绩");
      saveBtn.addEventListener("click", async () => {
        clear(errorLine);
        const entries = inputs.filter(({ input }) => input.value !== "");
        const invalid = entries.filter(({ input, subject }) => Number(input.value) < 0 || Number(input.value) > subject.full_score);
        if (invalid.length) {
          errorLine.append(fieldError(`有 ${invalid.length} 个分数超出 0–满分范围，已保留输入供修正`));
          invalid.forEach(({ input }) => input.focus());
          return;
        }
        if (!entries.length) { errorLine.append(fieldError("没有需要保存的成绩")); return; }
        saveInfo.textContent = `将整批提交 ${entries.length} 条成绩（任一失败则整批回滚，不产生部分写入）`;
        saveBtn.disabled = true;
        try {
          await api(`/exams/${exam.id}/scores`, {
            method: "POST",
            body: { scores: entries.map(({ student, subject, input }) => ({ student_id: student.id, subject: subject.subject, score: Number(input.value) })) },
          });
          toast(`已保存 ${entries.length} 条成绩`, "success");
          saveInfo.textContent = `已保存 ${entries.length} 条成绩`;
        } catch (error) {
          errorLine.append(fieldError(`保存失败（未写入任何成绩）：${error.message}`));
        } finally { saveBtn.disabled = false; }
      });
      body.append(el("div", { class: "table-wrap" }, table), saveInfo, errorLine);
      modal.foot.prepend(saveBtn);
    } catch (error) {
      clear(body);
      body.append(errorPanel(error));
    }
  }

  function chartSectionTitle(text) {
    return el("h3", { style: { marginTop: "16px" } }, text);
  }

  function barRow(label, value, tone) {
    const width = Math.max(0, Math.min(100, (value ?? 0) * 100));
    return el("div", { class: "chart-bar-row" },
      el("span", { class: "chart-bar-label" }, label),
      el("div", { class: "chart-bar-track" }, el("div", { class: `chart-bar-fill tone-${tone}`, style: { width: `${width}%` } })),
      el("span", { class: "chart-bar-value" }, pct(value)));
  }

  function subjectCompareChart(subjects) {
    const box = el("div", { class: "chart-box" });
    for (const s of subjects) {
      box.append(
        el("div", { class: "chart-subject-title" }, `${s.subject}（满分 ${s.full_score}）`),
        barRow("平均得分率", s.full_score ? s.average / s.full_score : null, "primary"),
        barRow("及格率", s.pass_rate, "ok"),
        barRow("优秀率", s.excellent_rate, "warn"));
    }
    return box;
  }

  const DISTRIBUTION_TONES = ["danger", "warn", "info", "soft", "ok"];

  function distributionChart(subjects) {
    const box = el("div", { class: "chart-box" });
    box.append(el("div", { class: "chart-legend" },
      ["<60% 不及格", "60–70% 及格", "70–80% 中等", "80–90% 良好", "≥90% 优秀"].map((label, index) =>
        el("span", {}, el("i", { class: `chart-legend-dot seg-${DISTRIBUTION_TONES[index]}` }), label))));
    for (const s of subjects) {
      const dist = s.distribution || [];
      const total = dist.reduce((acc, band) => acc + (band.count || 0), 0);
      const stack = el("div", { class: "chart-stack" });
      dist.forEach((band, index) => {
        if (!band.count) return;
        stack.append(el("div", {
          class: `chart-stack-seg seg-${DISTRIBUTION_TONES[index]}`,
          style: { width: `${(band.count / total) * 100}%` },
          title: `${band.label}：${band.count} 人`,
        }, String(band.count)));
      });
      if (!total) stack.append(el("span", { class: "chart-stack-empty" }, "暂无成绩"));
      box.append(el("div", { class: "chart-subject-title" }, `${s.subject}（${total} 人）`), stack);
    }
    return box;
  }

  async function openStatistics(exam) {
    const body = el("div", {}, skeleton(4));
    openModal({ title: `统计 · ${exam.name}`, wide: true, body, actions: [{ label: "关闭", kind: "secondary" }] });
    try {
      const stats = await api(`/exams/${exam.id}/statistics`);
      clear(body);
      const subjects = Array.isArray(stats.subjects)
        ? stats.subjects
        : Object.entries(stats.subjects || {}).map(([name, m]) => ({ subject: name, ...m }));
      const totals = stats.totals || [];
      const summary = stats.summary || {};

      body.append(el("div", { class: "metric-grid" },
        metricCard("参考学生", summary.student_count ?? "—"),
        metricCard("考试科目", summary.subject_count ?? "—"),
        metricCard("成绩条数", summary.score_count ?? "—")));
      if (summary.single_subject) {
        body.append(el("p", { class: "muted" }, "单科考试：总分排名即该科目排名，得分率按本科目满分计算。"));
      }

      if (subjects.length) {
        body.append(chartSectionTitle("科目对比"), subjectCompareChart(subjects));
        body.append(chartSectionTitle("分数段分布（按满分比例）"), distributionChart(subjects));
      }

      body.append(chartSectionTitle("总分排名"));
      body.append(dataTable({
        columns: [
          { key: "rank", label: "名次", width: "64px" },
          { key: "student_name", label: "学生", render: (r) => `${r.student_name ?? "—"}（${r.student_no ?? "—"}）` },
          { key: "total_score", label: "总分 / 满分", render: (r) => `${r.total_score} / ${r.total_full}` },
          { key: "rate", label: "得分率", render: (r) => pct(r.rate) },
          { key: "subjects", label: "各科成绩", render: (r) => Object.entries(r.subjects || {}).map(([k, v]) => `${k} ${v}`).join("、") || "—" },
        ],
        rows: totals,
        empty: { title: "暂无成绩", hint: "先录入成绩后生成排名。" },
      }));

      body.append(chartSectionTitle("科目明细"));
      body.append(dataTable({
        columns: [
          { key: "subject", label: "科目" },
          { key: "count", label: "人数" },
          { key: "average", label: "平均分", render: (r) => `${r.average} / ${r.full_score}` },
          { key: "median", label: "中位数" },
          { key: "std_dev", label: "标准差" },
          { key: "highest", label: "最高" },
          { key: "lowest", label: "最低" },
          { key: "pass_rate", label: "及格率", render: (r) => pct(r.pass_rate) },
          { key: "excellent_rate", label: "优秀率", render: (r) => pct(r.excellent_rate) },
        ],
        rows: subjects,
        empty: { title: "暂无统计数据", hint: "先录入成绩。" },
      }));

      if (stats.generated_at) {
        body.append(el("p", { class: "muted chart-meta" },
          `分析由程序基于库内数据确定性实时计算${stats.cache_hit ? "（命中缓存）" : "（本次新生成，已留缓存）"} · 生成于 ${fmtDateTime(stats.generated_at)}`));
      }
      if (stats.warnings?.length) body.append(...stats.warnings.map((w) => el("p", { class: "field-error" }, w)));
    } catch (error) {
      clear(body);
      body.append(errorPanel(error));
    }
  }

  await load();
}
