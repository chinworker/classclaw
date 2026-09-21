// 学生档案：搜索/筛选列表 + 详情 Drawer + 新增/编辑/软删除（直接调领域接口）。

import { el, clear, toast, fmtDate } from "../util.js";
import { api } from "../api.js";
import { state, refreshStudents } from "../state.js";
import {
  pageHeader, dataTable, statusBadge, openModal, openDrawer, confirmDanger, field, fieldError,
  errorPanel, skeleton, pagination,
} from "../components.js";

export async function render(mount, ctx) {
  const host = el("div");
  const queryInput = el("input", { type: "search", placeholder: "姓名 / 学号关键词", value: ctx.query.q || "" });
  const tagInput = el("input", { type: "text", placeholder: "标签", value: ctx.query.tag || "" });
  const statusSelect = el("select", {},
    el("option", { value: "" }, "全部状态"),
    el("option", { value: "active" }, "在读"),
    el("option", { value: "transferred" }, "转出"));
  if (ctx.query.status) statusSelect.value = ctx.query.status;

  let page = Number(ctx.query.page || 1);

  mount.append(
    pageHeader("学生档案", "查看和维护学生资料。",
      el("button", { class: "primary", type: "button", onclick: () => openStudentForm(null) }, "新增学生")),
    el("div", { class: "filter-bar" },
      field("搜索", queryInput),
      field("标签", tagInput),
      field("状态", statusSelect),
      el("button", { class: "primary", type: "button", onclick: () => { page = 1; load(); } }, "查询"),
      el("button", { class: "text-button", type: "button", onclick: () => { queryInput.value = ""; tagInput.value = ""; statusSelect.value = ""; page = 1; load(); } }, "清空筛选")),
    host);

  async function load() {
    clear(host);
    host.append(skeleton(5));
    const params = new URLSearchParams({ class_id: state.classId, page: String(page), page_size: "20" });
    if (queryInput.value.trim()) params.set("q", queryInput.value.trim());
    if (tagInput.value.trim()) params.set("tag", tagInput.value.trim());
    if (statusSelect.value) params.set("status", statusSelect.value);
    let data;
    try {
      data = await api(`/students?${params}`);
    } catch (error) {
      clear(host);
      host.append(errorPanel(error, { onRetry: load }));
      return;
    }
    clear(host);
    host.append(dataTable({
      columns: [
        { key: "student_no", label: "学号" },
        { key: "name", label: "姓名", render: (s) => el("b", {}, s.name) },
        { key: "gender", label: "性别", render: (s) => s.gender || "—" },
        { key: "boarding_status", label: "住宿", render: (s) => s.boarding_status || "—" },
        { key: "group_no", label: "小组", render: (s) => s.group_no || "—" },
        { key: "tags", label: "标签", render: (s) => (s.tags || []).map((t) => el("span", { class: "tag tag-info", style: { marginRight: "4px" } }, t)) },
        { key: "status", label: "状态", render: (s) => statusBadge(s.status) },
        { key: "actions", label: "操作", render: (s) => el("div", { class: "row-gap" },
          el("button", { class: "text-button", type: "button", onclick: () => openDetail(s) }, "详情"),
          el("button", { class: "text-button", type: "button", onclick: () => openStudentForm(s) }, "编辑"),
          el("button", { class: "text-button", type: "button", style: { color: "var(--danger)" }, onclick: () => removeStudent(s) }, "删除")) },
      ],
      rows: data.items,
      empty: { title: "没有学生", hint: "调整筛选条件，或点击“新增学生”。" },
    }), pagination({ total: data.total, page, pageSize: 20, onPage: (p) => { page = p; load(); } }));
  }

  async function openDetail(student) {
    const body = el("div", {}, skeleton(6));
    const drawer = openDrawer({ title: `${student.name} · 学号 ${student.student_no}`, body, wide: true });
    try {
      const d = await api(`/students/${student.id}`);
      clear(body);
      const s = d.student;
      body.append(el("div", { class: "card", style: { margin: "0 0 14px" } },
        el("h3", {}, "基础资料"),
        el("p", {}, `${s.name} · 学号 ${s.student_no} · ${s.gender || "性别未填"} · ${s.boarding_status || "住宿未填"}`, " ", statusBadge(s.status)),
        el("p", { class: "muted", style: { fontSize: "13px" } },
          `小组：${s.group_no || "—"} · 标签：${(s.tags || []).join("、") || "—"} · 入学：${fmtDate(s.enrollment_date)}`),
        el("p", { class: "muted", style: { fontSize: "13px" } }, `联系电话：${s.phone || "—"}（电话与家庭信息仅在此详情页展示）`),
        d.current_seat ? el("p", {}, `当前座位：第 ${d.current_seat.row} 排第 ${d.current_seat.col} 座`) : el("p", { class: "muted" }, "当前未排座位")));
      if (d.attention_items?.length) {
        body.append(el("div", { class: "card", style: { margin: "0 0 14px" } }, el("h3", {}, "关注项"),
          ...d.attention_items.map((e) => el("p", { class: "field-error", style: { fontSize: "13px" } }, `${fmtDate(e.event_date)} · ${e.subtype}：${e.content}`))));
      }
      body.append(sectionList("近期事件", (d.events || []).map((e) => `${fmtDate(e.event_date)} · ${e.subtype} · ${e.content}`)));
      body.append(sectionList("考勤（最近 30 条）", (d.attendance || []).map((a) => `${fmtDate(a.attendance_date)} · ${PERIOD[a.period] || a.period} · ${STATUS[a.status] || a.status}`)));
      body.append(sectionList("作业（最近 20 条）", (d.homework || []).map((h) => `${STATUS[h.status] || h.status}${h.score !== null && h.score !== undefined ? ` · ${h.score} 分` : ""}`)));
      body.append(sectionList("成绩（最近 30 条）", (d.scores || []).map((sc) => `${sc.subject} · ${sc.score}/${sc.full_score}${sc.class_rank ? ` · 第 ${sc.class_rank} 名` : ""}`)));
      body.append(sectionList("值日（最近 10 条）", (d.recent_duty || []).map((t) => `${fmtDate(t.duty_date)} · ${t.item_name} · ${STATUS[t.status] || t.status}`)));
    } catch (error) {
      clear(body);
      body.append(errorPanel(error));
    }
  }

  const PERIOD = { full_day: "全天", morning: "上午", afternoon: "下午", recess: "大课间", care_1: "晚托一", care_2: "晚托二" };
  const STATUS = { present: "出勤", late: "迟到/迟交", absent: "缺勤", leave: "请假", pending: "待交", submitted: "已交", missing: "未交", exempt: "免交", revision_required: "需订正", revised: "已订正", completed: "已完成" };

  function sectionList(title, lines) {
    return el("div", { class: "card", style: { margin: "0 0 14px" } }, el("h3", {}, title),
      lines.length ? lines.map((line) => el("p", { class: "muted", style: { fontSize: "13px" } }, line)) : el("p", { class: "muted" }, "暂无记录"));
  }

  function openStudentForm(student) {
    const isEdit = !!student;
    const inputs = {};
    const specs = [
      ["student_no", "学号 *", "text"], ["name", "姓名 *", "text"], ["gender", "性别", "text"],
      ["phone", "联系电话", "text"], ["boarding_status", "住宿情况", "text"], ["duty_role", "值日角色", "text"],
      ["group_no", "小组", "text"], ["tags", "标签（逗号分隔）", "text"], ["family_status", "家庭情况", "text"],
      ["father_name", "父亲姓名", "text"], ["father_phone", "父亲电话", "text"], ["mother_name", "母亲姓名", "text"],
      ["mother_phone", "母亲电话", "text"], ["notes", "备注", "text"],
    ];
    const grid = el("div", { class: "form-grid" });
    for (const [key, label, type] of specs) {
      const value = student ? (key === "tags" ? (student.tags || []).join(",") : student[key] ?? "") : "";
      const input = el("input", { type, value });
      inputs[key] = input;
      grid.append(field(label, input));
    }
    const errorLine = el("div");
    openModal({
      title: isEdit ? `编辑学生 · ${student.name}` : "新增学生",
      wide: true,
      body: el("div", {}, grid, errorLine),
      actions: [
        { label: "取消", kind: "secondary" },
        {
          label: isEdit ? "保存" : "创建", kind: "primary",
          onClick: async ({ close, setSubmitting }) => {
            clear(errorLine);
            const body = {};
            for (const [key, input] of Object.entries(inputs)) {
              const value = input.value.trim();
              if (key === "tags") { if (value) body.tags = value.split(/[,，]/).map((t) => t.trim()).filter(Boolean); else if (isEdit) body.tags = []; }
              else if (value) body[key] = value;
              else if (isEdit) body[key] = null;
            }
            if (!body.student_no && !isEdit) { errorLine.append(fieldError("学号必填")); return; }
            if (!body.name && !isEdit) { errorLine.append(fieldError("姓名必填")); return; }
            setSubmitting(true);
            try {
              if (isEdit) await api(`/students/${student.id}`, { method: "PATCH", body });
              else await api("/students", { method: "POST", body: { ...body, class_id: state.classId } });
              close();
              toast(isEdit ? "学生档案已更新" : "学生已创建", "success");
              refreshStudents({ force: true }).catch(() => {});
              load();
            } catch (error) {
              if (error.code === "STUDENT_NO_CONFLICT") errorLine.append(fieldError("同一班级内学号已存在"));
              else errorLine.append(fieldError(error.message));
            } finally { setSubmitting(false); }
          },
        },
      ],
    });
  }

  async function removeStudent(student) {
    const ok = await confirmDanger({
      title: `删除学生「${student.name}」`,
      lines: [
        `学生：${student.name}（学号 ${student.student_no}）。`,
        "当前为软删除：记录保留但不再出现在名单、排班和统计中。",
        "已有的历史考勤、成绩、事件记录不会被清除。",
      ],
      confirmLabel: "确认软删除",
    });
    if (!ok) return;
    try {
      await api(`/students/${student.id}`, { method: "DELETE" });
      toast("学生已软删除", "success");
      refreshStudents({ force: true }).catch(() => {});
      load();
    } catch (error) { toast(error.message, "error"); }
  }

  await load();
}
