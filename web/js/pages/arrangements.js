// 日常安排：分组展示 + 新增 + 完成登记；提醒时间最多 3 个，未指定时默认截止前 3 小时。

import { el, clear, toast, fmtDateTime } from "../util.js";
import { api } from "../api.js";
import { state } from "../state.js";
import { pageHeader, field, fieldError, errorPanel, skeleton, emptyState, statusBadge, openModal, confirmDanger } from "../components.js";

const GROUPS = [
  ["pending", "待处理"], ["in_progress", "进行中"], ["overdue", "已逾期"], ["completed", "已完成"],
];
const PRIORITIES = [["high", "高"], ["medium", "中"], ["low", "低"]];

export async function render(mount) {
  const host = el("div");
  mount.append(
    pageHeader("日常安排", "设置事项和提醒。",
      el("button", { class: "primary", type: "button", onclick: openCreateModal }, "新增安排")),
    host);

  async function load() {
    clear(host);
    host.append(skeleton(5));
    let rows;
    try {
      rows = await api(`/arrangements?class_id=${state.classId}`);
    } catch (error) {
      clear(host);
      host.append(errorPanel(error, { onRetry: load }));
      return;
    }
    clear(host);
    if (!rows.length) { host.append(emptyState("暂无安排", "点击“新增安排”。默认提前 3 小时提醒一次。")); return; }
    for (const [status, label] of GROUPS) {
      const items = rows.filter((a) => a.status === status);
      if (!items.length) continue;
      host.append(el("div", { class: "card" },
        el("h3", {}, `${label}（${items.length}）`),
        ...items.map((a) => el("div", { class: "row-gap", style: { justifyContent: "space-between", padding: "8px 0", borderBottom: "1px solid var(--border)" } },
          el("div", {},
            el("div", { class: "row-gap" }, statusBadge(a.priority), el("b", {}, a.title)),
            el("div", { class: "muted", style: { fontSize: "12px" } },
              [a.summary || null, a.start_at ? `开始 ${fmtDateTime(a.start_at)}` : null, a.due_at ? `截止 ${fmtDateTime(a.due_at)}` : null].filter(Boolean).join(" · "))),
          el("div", { class: "row-gap" },
            statusBadge(a.status),
            ["pending", "in_progress", "overdue"].includes(a.status)
              ? el("button", { class: "secondary", type: "button", onclick: async () => {
                  try { await api(`/arrangements/${a.id}/complete`, { method: "POST", body: {} }); toast("已完成", "success"); load(); }
                  catch (error) { toast(error.message, "error"); }
                } }, "完成")
              : null)))));
    }
  }

  function openCreateModal() {
    const titleInput = el("input", { type: "text", placeholder: "如：收体检回执" });
    const summaryInput = el("textarea", { rows: "2", placeholder: "补充说明（可选）" });
    const startInput = el("input", { type: "datetime-local" });
    const dueInput = el("input", { type: "datetime-local" });
    const prioritySel = el("select", {}, PRIORITIES.map(([v, l]) => el("option", { value: v }, l)));
    prioritySel.value = "medium";
    const reminderInputs = [el("input", { type: "datetime-local" }), el("input", { type: "datetime-local" }), el("input", { type: "datetime-local" })];
    const errorLine = el("div");
    openModal({
      title: "新增安排",
      body: el("div", {},
        field("标题 *", titleInput), field("说明", summaryInput),
        el("div", { class: "form-grid" }, field("开始时间", startInput), field("截止时间", dueInput), field("优先级", prioritySel)),
        el("b", { style: { display: "block", margin: "8px 0 4px" } }, "提醒（最多 3 次，留空则提前 3 小时）"),
        ...reminderInputs.map((input, i) => field(`提醒 ${i + 1}`, input)),
        errorLine),
      actions: [
        { label: "取消", kind: "secondary" },
        {
          label: "创建", kind: "primary",
          onClick: async ({ close, setSubmitting }) => {
            clear(errorLine);
            if (!titleInput.value.trim()) { errorLine.append(fieldError("标题必填")); return; }
            const reminderTimes = reminderInputs.map((i) => i.value).filter(Boolean).map((v) => new Date(v).toISOString());
            if (reminderTimes.length && !startInput.value && !dueInput.value) {
              errorLine.append(fieldError("设置提醒前必须提供开始或截止时间"));
              return;
            }
            setSubmitting(true);
            try {
              await api("/arrangements", {
                method: "POST",
                body: {
                  class_id: state.classId, title: titleInput.value.trim(),
                  summary: summaryInput.value.trim() || null,
                  start_at: startInput.value ? new Date(startInput.value).toISOString() : null,
                  due_at: dueInput.value ? new Date(dueInput.value).toISOString() : null,
                  priority: prioritySel.value, status: "pending", source_type: "web",
                  reminder_times: reminderTimes,
                },
              });
              close();
              toast("安排已创建，并会出现在当天早报中", "success");
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
