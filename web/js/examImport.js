import { api, AI_REQUEST_TIMEOUT_MS } from "./api.js";
import { dataTable, errorPanel, field, fileDropzone, openModal } from "./components.js";
import { clear, el, toast } from "./util.js";

const rankText = (classRank, gradeRank) => [
  classRank != null ? `班级第 ${classRank}` : "",
  gradeRank != null ? `年级第 ${gradeRank}` : "",
].filter(Boolean).join(" · ");

export function openExamImport(classId, onImported, getSubjects = () => []) {
  const host = el("div");
  const resultHost = el("div");
  const lifetime = new AbortController();
  let saving = false;
  const modal = openModal({
    title: "从文件导入考试成绩", wide: true, body: host,
    actions: [{ label: "关闭", kind: "secondary" }],
    onClose: () => lifetime.abort(),
  });
  host.append(el("p", { class: "muted" }, "支持 Excel（.xlsx）和 CSV，每次最多 4 个文件。年级成绩表请保留班级、姓名和学号列。先预览，再选择要导入的考试或数据版本。"),
    fileDropzone({
      hint: "拖拽成绩表到这里，或点击选择",
      accept: ".xlsx,.csv", multiple: true, maxFiles: 4, manualStart: true,
      busyText: "正在识别考试、科目和排名列…",
      onFiles: async (files, { signal, taskId }) => {
        if (saving || lifetime.signal.aborted) return;
        clear(resultHost);
        const form = new FormData();
        files.forEach((file) => form.append("files", file));
        const controller = new AbortController();
        const cancel = () => controller.abort();
        signal.addEventListener("abort", cancel, { once: true });
        lifetime.signal.addEventListener("abort", cancel, { once: true });
        try {
          const result = await api(`/classes/${encodeURIComponent(classId)}/exams/import-preview`, {
            method: "POST", body: form, signal: controller.signal, aiTaskId: taskId, timeoutMs: AI_REQUEST_TIMEOUT_MS,
          });
          if (!controller.signal.aborted) renderPreview(result);
        } catch (error) {
          if (!controller.signal.aborted && !lifetime.signal.aborted) resultHost.append(errorPanel(error));
        } finally {
          signal.removeEventListener("abort", cancel);
          lifetime.signal.removeEventListener("abort", cancel);
        }
      },
    }), resultHost);

  function renderPreview(result) {
    const datasets = result.datasets || [];
    resultHost.append(...(result.warnings || []).map((warning) => el("p", { class: "issue issue-warn" }, warning)));
    if (!datasets.length) {
      resultHost.append(el("p", { class: "issue issue-error" },
        "没有可预览的数据。请使用一行表头，保留班级、姓名、学号，并将不同考试或数据版本放在独立工作表中。"));
      return;
    }
    const form = el("form", { novalidate: true });
    const validCount = datasets.filter((item) => !item.errors.length).length;
    const subjectOptions = getSubjects().map(({ name }) => name);
    const choices = datasets.map((item, index) => {
      const blocked = item.errors.length > 0;
      const selected = el("input", { type: "checkbox", disabled: blocked, checked: !blocked && validCount === 1 });
      const name = el("input", { type: "text", value: item.name, maxlength: "100", placeholder: "填写考试名称", disabled: blocked });
      const date = el("input", { type: "date", value: item.exam_date ?? "", disabled: blocked });
      const full = new Map(item.subjects.map((subject) => [subject.subject,
        el("input", { type: "number", min: "0.01", step: "any", value: subject.full_score ?? "", placeholder: "填写满分", disabled: blocked })]));
      const missing = new Map(item.subjects.filter((subject) => subject.requires_subject).map(({ subject }) => [
        subject, subjectOptions.length
          ? el("select", { disabled: blocked }, el("option", { value: "" }, "请选择科目"),
            ...subjectOptions.map((option) => el("option", { value: option }, option)))
          : el("input", { type: "text", maxlength: "100", placeholder: "填写科目", disabled: blocked }),
      ]));
      for (const input of missing.values()) input.value = "";
      const scores = new Map(item.scores.map((row) => [`${row.student_id}:${row.subject}`, row]));
      const totals = new Map(item.totals.map((row) => [row.student_id, row]));
      const display = (row) => row ? [row.score ?? "—", rankText(row.class_rank, row.grade_rank)].filter(Boolean).join(" · ") : "—";
      const columns = [{ key: "student_no", label: "学号" }, { key: "name", label: "学生" },
        ...item.subjects.map(({ subject }) => ({ key: subject, label: subject, render: (student) => display(scores.get(`${student.id}:${subject}`)) }))];
      if (totals.size) columns.push({ key: "total", label: "原表总分及排名", render: (student) => display(totals.get(student.id)) });
      const details = el("details", {}, el("summary", {}, "核对本班成绩与排名"),
        dataTable({ columns, rows: item.students }));
      const sourceLabel = item.simple_scores ? "姓名＋分数简表" : `第 ${item.header_row} 行表头`;
      form.append(el("section", { class: "card import-panel" },
        el("label", { class: "row-gap" }, selected, el("b", {}, `数据组 ${index + 1}：${item.name || "待命名考试"}`)),
        el("p", { class: "muted" }, `${item.source} · ${sourceLabel} · 本班 ${item.students.length} 人 · ${item.scores.length} 条成绩 · 已排除 ${item.excluded_rows} 行非匹配记录`),
        ...item.errors.map((error) => el("p", { class: "issue issue-error" }, error)),
        ...item.warnings.map((warning) => el("p", { class: "issue issue-warn" }, warning)),
        item.unmapped_columns.length ? el("details", {}, el("summary", {}, "查看未导入的列"),
          el("p", {}, item.unmapped_columns.join("、"))) : null,
        el("div", { class: "form-grid" }, field("考试名称", name), field("考试日期", date),
          ...[...missing].map(([subject, input]) => field(`${subject}对应的科目`, input)),
          ...item.subjects.map(({ subject, score_header, class_rank_header, grade_rank_header }) => field(
            `${subject}满分`, full.get(subject), [
              `原表成绩列：${score_header}`, class_rank_header && `班级排名列：${class_rank_header}`,
              grade_rank_header && `年级排名列：${grade_rank_header}`,
            ].filter(Boolean).join(" · ")))),
        details));
      return { item, selected, name, date, full, missing };
    });
    resultHost.append(form);
    if (!result.preview_id) return;
    const errors = el("div");
    const confirm = el("button", { class: "primary", type: "submit" }, "确认导入所选数据");
    form.append(el("p", { class: "muted" }, "同一考试的多个版本请只选一个。同名同日的已有考试不会被覆盖；缺考和空白成绩不会填零。"), errors, confirm);
    form.addEventListener("submit", async (event) => {
      event.preventDefault();
      if (saving || lifetime.signal.aborted) return;
      clear(errors);
      const chosen = choices.filter(({ selected }) => selected.checked);
      if (!chosen.length) {
        errors.append(el("p", { class: "field-error" }, "请先选择要导入的数据组。"));
        return;
      }
      if (chosen.some(({ missing }) => [...missing.values()].some((input) => !input.value.trim()))) {
        errors.append(el("p", { class: "field-error" }, "请补齐所选数据组缺失的科目。"));
        return;
      }
      const payload = chosen.map(({ item, name, date, full, missing }) => ({
        dataset_id: item.id, name: name.value.trim(), exam_date: date.value,
        full_scores: Object.fromEntries([...full].map(([subject, input]) => [subject, Number(input.value)])),
        ...(missing.size ? { subject_names: Object.fromEntries([...missing].map(([key, input]) => [key, input.value.trim()])) } : {}),
      }));
      if (payload.some((row) => !row.name || !row.exam_date || Object.values(row.full_scores).some((v) => !(v > 0)))) {
        errors.append(el("p", { class: "field-error" }, "请为所选数据填写考试名称、日期，以及每科大于 0 的满分。"));
        return;
      }
      for (const [index, row] of payload.entries()) {
        const excessive = chosen[index].item.subjects.find((subject) => subject.highest_score > row.full_scores[subject.subject]);
        if (excessive) {
          errors.append(el("p", { class: "field-error" },
            `「${row.name}」的${excessive.subject}最高分 ${excessive.highest_score} 超过填写的满分，请核对满分或原表。`));
          return;
        }
      }
      saving = true;
      confirm.disabled = true;
      modal.setSubmitting(true);
      try {
        const data = await api(`/classes/${encodeURIComponent(classId)}/exams/import-apply`, {
          method: "POST", body: { preview_id: result.preview_id, datasets: payload },
        });
        if (!lifetime.signal.aborted) {
          modal.close();
          toast(`已导入 ${data.exams.length} 场考试`, "success");
          onImported();
        }
      } catch (error) {
        if (!lifetime.signal.aborted) errors.append(errorPanel(error));
      } finally {
        saving = false;
        confirm.disabled = false;
        modal.setSubmitting(false);
      }
    });
  }
  return modal.close;
}
