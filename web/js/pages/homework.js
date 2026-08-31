// 作业管理：创建、列表、详情（完成率/未交清单）、批量收缴状态更新——全部直接调领域接口。

import { el, clear, toast, todayStr, fmtDate, fmtDateTime } from "../util.js";
import { api } from "../api.js";
import { state, refreshStudents, refreshSubjects } from "../state.js";
import {
  pageHeader, dataTable, statusBadge, openModal, openDrawer, field, fieldError,
  errorPanel, skeleton, emptyState,
} from "../components.js";

const STATUS_OPTIONS = [["pending", "待交"], ["submitted", "已交"], ["late", "迟交"], ["missing", "未交"], ["exempt", "免交"], ["revision_required", "需订正"], ["revised", "已订正"]];

export async function render(mount) {
  const subjects = await refreshSubjects().catch(() => []);
  const host = el("div");
  const subjectInput = el("select", {}, el("option", { value: "" }, "全部科目"), ...subjects.map((s) => el("option", { value: s.name }, s.name)));
  const statusSelect = el("select", {},
    el("option", { value: "" }, "全部状态"),
    ...["draft", "published", "closed", "cancelled"].map((s) => el("option", { value: s }, statusText(s))));

  mount.append(
    pageHeader("作业", "创建作业，登记收交情况。",
      el("button", { class: "primary", type: "button", onclick: openCreateModal }, "布置作业")),
    el("div", { class: "filter-bar" },
      field("科目", subjectInput), field("状态", statusSelect),
      el("button", { class: "primary", type: "button", onclick: load }, "查询"),
      el("button", { class: "text-button", type: "button", onclick: () => { subjectInput.value = ""; statusSelect.value = ""; load(); } }, "清空筛选")),
    host);

  function statusText(s) { return { draft: "草稿", published: "已发布", closed: "已关闭", cancelled: "已取消" }[s] || s; }

  async function load() {
    clear(host);
    host.append(skeleton(5));
    const params = new URLSearchParams({ class_id: state.classId });
    if (subjectInput.value) params.set("subject", subjectInput.value);
    if (statusSelect.value) params.set("status", statusSelect.value);
    let rows;
    try {
      rows = await api(`/homework?${params}`);
    } catch (error) {
      clear(host);
      host.append(errorPanel(error, { onRetry: load }));
      return;
    }
    clear(host);
    host.append(dataTable({
      columns: [
        { key: "title", label: "作业", render: (h) => el("div", {}, el("b", {}, h.title), h.description ? el("div", { class: "muted", style: { fontSize: "12px" } }, h.description.slice(0, 60)) : null) },
        { key: "subject", label: "科目" },
        { key: "assigned_date", label: "布置日期", render: (h) => fmtDate(h.assigned_date) },
        { key: "due_at", label: "截止时间", render: (h) => fmtDateTime(h.due_at) },
        { key: "status", label: "状态", render: (h) => statusBadge(h.status) },
        { key: "actions", label: "操作", render: (h) => el("button", { class: "text-button", type: "button", onclick: () => openDetail(h) }, "收缴明细") },
      ],
      rows,
      empty: { title: "暂无作业", hint: "点击“布置作业”创建第一条作业。" },
    }));
  }

  async function openDetail(homework) {
    const body = el("div", {}, skeleton(5));
    openDrawer({ title: `${homework.title} · ${homework.subject}`, body, wide: true });
    try {
      const [summary, students] = await Promise.all([api(`/homework/${homework.id}/summary`), refreshStudents()]);
      clear(body);
      const counts = summary.status_counts || summary.counts || {};
      body.append(el("div", { class: "metric-grid" },
        ...Object.entries(counts).map(([status, count]) => el("div", { class: "metric-card" },
          el("span", { class: "metric-value" }, String(count)),
          el("span", { class: "metric-label" }, (STATUS_OPTIONS.find(([v]) => v === status) || [])[1] || status)))));
      body.append(el("p", { class: "muted" },
        `布置：${fmtDate(homework.assigned_date)} · 截止：${fmtDateTime(homework.due_at)} · 完成率：${summary.completion_rate !== null && summary.completion_rate !== undefined ? `${Math.round(summary.completion_rate * 100)}%` : "—"}`));
      renderStatusEditor(body, homework, students);
    } catch (error) {
      clear(body);
      body.append(errorPanel(error));
    }
  }

  // 批量收缴状态编辑：提交按钮附近显示数量与范围
  function renderStatusEditor(body, homework, students) {
    const selects = new Map();
    const table = el("table", { class: "data-table" },
      el("thead", {}, el("tr", {}, el("th", {}, "学生"), el("th", {}, "状态"), el("th", {}, "分数"), el("th", {}, "备注"))));
    const tbody = el("tbody");
    const existing = new Map((homework._statuses || []).map((s) => [s.student_id, s]));
    for (const s of students) {
      const sel = el("select", {}, STATUS_OPTIONS.map(([v, l]) => el("option", { value: v }, l)));
      sel.value = existing.get(s.id)?.status || "pending";
      selects.set(s.id, sel);
      const scoreInput = el("input", { type: "number", min: "0", style: { width: "80px" }, value: existing.get(s.id)?.score ?? "" });
      const commentInput = el("input", { type: "text", value: existing.get(s.id)?.comment ?? "" });
      tbody.append(el("tr", {},
        el("td", {}, `${s.name}（${s.student_no}）`),
        el("td", {}, sel), el("td", {}, scoreInput), el("td", {}, commentInput)));
      sel._score = scoreInput; sel._comment = commentInput;
    }
    table.append(tbody);
    const saveInfo = el("p", { class: "muted" }, `将批量保存 ${students.length} 名学生的「${homework.subject}」作业状态`);
    const saveBtn = el("button", { class: "primary", type: "button" }, `保存 ${students.length} 名学生的作业状态`);
    saveBtn.addEventListener("click", async () => {
      saveBtn.disabled = true;
      const items = students.map((s) => {
        const sel = selects.get(s.id);
        const item = { student_id: s.id, status: sel.value };
        if (sel._score.value !== "") item.score = Number(sel._score.value);
        if (sel._comment.value.trim()) item.comment = sel._comment.value.trim();
        return item;
      });
      try {
        await api(`/homework/${homework.id}/students`, { method: "PUT", body: { items } });
        toast("作业状态已批量更新", "success");
      } catch (error) { toast(error.message, "error"); }
      finally { saveBtn.disabled = false; }
    });
    body.append(el("h3", {}, "收缴状态"), el("div", { class: "table-wrap" }, table), saveInfo, saveBtn);
  }

  function openCreateModal() {
    const titleInput = el("input", { type: "text", placeholder: "如：练习册 P12-14" });
    const subjectInput = el("select", {}, el("option", { value: "" }, "选择科目"), ...subjects.map((s) => el("option", { value: s.name }, s.name)));
    const descInput = el("textarea", { rows: "2", placeholder: "要求说明（可选）" });
    const dateInput = el("input", { type: "date", value: todayStr() });
    const dueInput = el("input", { type: "datetime-local" });
    const statusSel = el("select", {}, el("option", { value: "published" }, "已发布"), el("option", { value: "draft" }, "草稿"));
    const errorLine = el("div");
    openModal({
      title: "布置作业",
      body: el("div", {},
        field("标题 *", titleInput), field("科目 *", subjectInput), field("说明", descInput),
        el("div", { class: "form-grid" }, field("布置日期 *", dateInput), field("截止时间", dueInput), field("状态", statusSel)),
        errorLine),
      actions: [
        { label: "取消", kind: "secondary" },
        {
          label: "创建", kind: "primary",
          onClick: async ({ close, setSubmitting }) => {
            clear(errorLine);
            if (!titleInput.value.trim()) { errorLine.append(fieldError("标题必填")); return; }
            if (!subjectInput.value) { errorLine.append(fieldError("科目必填")); return; }
            if (!dateInput.value) { errorLine.append(fieldError("布置日期必填")); return; }
            setSubmitting(true);
            try {
              await api("/homework", {
                method: "POST",
                body: {
                  class_id: state.classId, title: titleInput.value.trim(), subject: subjectInput.value,
                  description: descInput.value.trim() || null, assigned_date: dateInput.value,
                  due_at: dueInput.value ? new Date(dueInput.value).toISOString() : null, status: statusSel.value,
                },
              });
              close();
              toast("作业已创建", "success");
              load();
            } catch (error) { errorLine.append(fieldError(error.message)); }
            finally { setSubmitting(false); }
          },
        },
      ],
    });
  }

  await load();
}
