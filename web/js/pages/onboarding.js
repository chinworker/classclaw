// 班级创建向导（onboarding）：网页专属。
// 流程：班级信息 → 学生名单 → 课表与节次 → 预览确认 → 智能体与微信。
// 只有最终确认走 /preview + /write-proposals/{id}/confirm；草稿反复编辑用 PATCH + expected_revision。

import { el, clear, toast, debounce } from "../util.js";
import { api, ApiError } from "../api.js";
import { state, refreshIdentity, refreshClassInfo } from "../state.js";
import { navigate } from "../router.js";
import {
  field, fieldError, fileDropzone, proposalReview, qrBindingPanel, statusBadge, openclawBlocked, emptyState,
} from "../components.js";
import { timetableGridEditor } from "../timetableGrid.js";

const STEPS = [
  { key: "class_info", label: "班级信息" },
  { key: "students", label: "学生名单" },
  { key: "timetable", label: "课表与节次" },
  { key: "review", label: "复核与创建" },
];

export async function render(mount, ctx, helpers) {
  clear(mount);
  const local = {
    session: null,
    step: 0,
    nameCheck: null,      // { name, available, message }
    proposal: null,
    classResult: null,    // confirm 后的 result_json
    provisionDone: false,
    qrPanel: null,
  };

  // 页面内编辑器实例（跨渲染保留）
  let ttEditor = null;
  const studentRowsHost = { current: [] }; // 结构化学生表的本地镜像

  /* ---------- 会话加载/创建 ---------- */

  async function loadOrCreate() {
    if (ctx.params.id) {
      local.session = await api(`/class-onboarding/sessions/${ctx.params.id}`);
      return;
    }
    try {
      local.session = await api("/class-onboarding/sessions", { method: "POST", body: { initial_draft: {}, created_by: state.user.username } });
      navigate(`/onboarding/${local.session.id}`);
    } catch (error) {
      if (error.code === "ONBOARDING_ALREADY_EXISTS" && error.details?.session_id) {
        clear(mount);
        mount.append(el("div", { class: "card" },
          el("h3", {}, "已有未完成的班级创建引导"),
          el("p", { class: "muted" }, "一个账号同时只能保留一个进行中的创建引导。请继续已有草稿。"),
          el("button", { class: "primary", type: "button", onclick: () => navigate(`/onboarding/${error.details.session_id}`) }, "继续填写")));
        return;
      }
      if (error.code === "CLASS_LIMIT_REACHED") {
        clear(mount);
        mount.append(el("div", { class: "card" },
          el("h3", {}, "一个班主任账号只能拥有一个班级"),
          el("p", { class: "muted" }, error.message),
          el("button", { class: "primary", type: "button", onclick: () => navigate("/dashboard") }, "回到班级工作台")));
        return;
      }
      throw error;
    }
  }

  try {
    await loadOrCreate();
  } catch (error) {
    clear(mount);
    mount.append(el("div", { class: "error-panel" }, el("b", {}, error.message)));
    return;
  }
  if (!local.session) return; // 已渲染阻止态

  if (local.session.status === "completed" && local.session.class_id) {
    // 班级已创建（可能刷新后回来）：直接进入智能体/微信收尾
    local.classResult = { class_id: local.session.class_id };
    await refreshIdentity().catch(() => {});
    local.step = STEPS.length; // done 阶段
    renderDone();
    return;
  }

  /* ---------- 草稿读写 ---------- */

  const draft = () => local.session.draft_json || {};

  function collectDraft() {
    syncStudentRowsFromDom();
    return {
      class_info: readClassInfo(),
      students: studentRowsHost.current,
      periods: readPeriodsFromDom(),
      base_timetable: ttEditor ? ttEditor.getItems() : (draft().base_timetable || []),
    };
  }

  async function saveDraft(stepKey = null) {
    const body = {
      expected_revision: local.session.revision,
      draft_patch: collectDraft(),
      replace_lists: true,
    };
    if (stepKey) body.current_step = stepKey;
    try {
      local.session = await api(`/class-onboarding/sessions/${local.session.id}`, { method: "PATCH", body });
      return true;
    } catch (error) {
      if (error.code === "PENDING_CONFIRMATION_REQUIRED") {
        toast("草稿在其他地方被修改过，已重新载入最新版本", "error");
        local.session = await api(`/class-onboarding/sessions/${local.session.id}`);
        renderStep();
        return false;
      }
      toast(error.message, "error");
      return false;
    }
  }

  /* ---------- 步骤 1：班级信息 ---------- */

  const classInputs = {};
  function readClassInfo() {
    const info = {};
    for (const [key, input] of Object.entries(classInputs)) {
      const value = input.value.trim();
      if (value) info[key] = value;
    }
    return info;
  }

  const nameStatus = el("small", { class: "field-hint" }, "输入后自动检查班级与智能体名称冲突");
  const doNameCheck = debounce(async () => {
    const name = classInputs.name.value.trim();
    if (!name) { nameStatus.textContent = "输入后自动检查班级与智能体名称冲突"; local.nameCheck = null; return; }
    nameStatus.textContent = "正在检查名称…";
    try {
      const result = await api(`/class-onboarding/name-check?class_name=${encodeURIComponent(name)}`);
      if (classInputs.name.value.trim() !== name) return;
      local.nameCheck = result;
      nameStatus.textContent = result.message;
      nameStatus.style.color = result.available ? "var(--success)" : "var(--danger)";
    } catch (error) {
      local.nameCheck = { available: false, message: error.message };
      nameStatus.textContent = error.message;
      nameStatus.style.color = "var(--danger)";
    }
  }, 450);

  function renderClassInfo(panel) {
    const info = draft().class_info || {};
    const specs = [
      ["name", "班级名称 *", "text", "例如：高一（3）班"],
      ["grade", "年级 *", "text", "例如：高一"],
      ["head_teacher", "班主任", "text", "例如：李老师"],
      ["room", "本班教室", "text", "例如：教学楼 303"],
      ["semester_name", "学期名称", "text", "例如：2026 秋季学期"],
      ["semester_start", "开学日期", "date", null],
      ["semester_end", "学期结束", "date", null],
    ];
    const grid = el("div", { class: "form-grid" });
    for (const [key, label, type, placeholder] of specs) {
      const input = el("input", { type, value: info[key] || "", placeholder: placeholder || "", autocomplete: "off" });
      classInputs[key] = input;
      grid.append(field(label, input, key === "name" ? null : null));
    }
    classInputs.name.addEventListener("input", () => { local.nameCheck = null; nameStatus.style.color = ""; doNameCheck(); });
    classInputs.name.addEventListener("blur", () => doNameCheck());
    panel.append(
      el("p", { class: "muted" }, "确定性字段，保存时不会被智能体改写。"),
      grid, nameStatus);
    if (info.name) doNameCheck();
  }

  /* ---------- 步骤 2：学生名单 ---------- */

  let studentsTbody = null;
  const STUDENT_COLS = [
    ["student_no", "学号 *"], ["name", "姓名 *"], ["gender", "性别"], ["phone", "电话"],
    ["boarding_status", "住宿"], ["group_no", "小组"], ["notes", "备注"],
  ];

  function appendStudentRow(row = {}) {
    const tr = el("tr");
    for (const [key] of STUDENT_COLS) {
      const input = el("input", { type: "text", value: row[key] ?? "", dataset: { key } });
      tr.append(el("td", {}, input));
    }
    const del = el("button", { class: "text-button", type: "button", onclick: () => { tr.remove(); } }, "删除");
    tr.append(el("td", {}, del));
    studentsTbody.append(tr);
  }

  function syncStudentRowsFromDom() {
    if (!studentsTbody || !studentsTbody.isConnected) return;
    const rows = [];
    for (const tr of studentsTbody.querySelectorAll("tr")) {
      const row = { tags: [], status: "active" };
      tr.querySelectorAll("input[data-key]").forEach((input) => {
        const value = input.value.trim();
        if (value) row[input.dataset.key] = value;
      });
      if (row.student_no && row.name) rows.push(row);
    }
    studentRowsHost.current = rows;
  }

  function renderStudents(panel) {
    studentRowsHost.current = (draft().students || []).map((s) => ({ ...s }));
    panel.append(el("p", { class: "muted" }, "上传文件生成名单。识别后可修改。"));
    panel.append(fileDropzone({
      hint: "拖拽学生名单文件到这里，或点击选择（最多 8 个）",
      multiple: true,
      onFiles: (files, { setBusy }) => uploadSection("students", files, setBusy),
    }));
    const addBtn = el("button", { class: "secondary", type: "button", onclick: () => appendStudentRow() }, "添加学生");
    panel.append(el("div", { class: "row-gap", style: { justifyContent: "space-between", marginBottom: "8px" } },
      el("b", {}, "结构化名单"), addBtn));
    studentsTbody = el("tbody");
    const table = el("table", { class: "data-table" },
      el("thead", {}, el("tr", {}, [...STUDENT_COLS.map(([, label]) => el("th", {}, label)), el("th", {}, "")])),
      studentsTbody);
    studentRowsHost.current.forEach((row) => appendStudentRow(row));
    panel.append(el("div", { class: "table-wrap" }, table));
    if (!studentRowsHost.current.length) panel.append(emptyState("尚未上传名单", "上传文件或手动添加学生。"));
  }

  /* ---------- 步骤 3：课表与节次 ---------- */

  let periodsTbody = null;

  function appendPeriodRow(row = {}) {
    const noInput = el("input", { type: "number", min: "1", value: row.period_no ?? "", dataset: { key: "period_no" }, style: { width: "80px" } });
    const nameInput = el("input", { type: "text", value: row.name ?? "", placeholder: "可选", dataset: { key: "name" } });
    const del = el("button", { class: "text-button", type: "button", onclick: () => { tr.remove(); } }, "删除");
    const tr = el("tr", {}, el("td", {}, noInput), el("td", {}, nameInput), el("td", {}, del));
    periodsTbody.append(tr);
  }

  function readPeriodsFromDom() {
    if (!periodsTbody || !periodsTbody.isConnected) return draft().periods || [];
    const rows = [];
    for (const tr of periodsTbody.querySelectorAll("tr")) {
      const no = Number(tr.querySelector('[data-key="period_no"]').value);
      const name = tr.querySelector('[data-key="name"]').value.trim();
      if (no >= 1) rows.push({ period_no: no, name: name || null, sort_order: no, enabled: true });
    }
    return rows;
  }

  function renderTimetable(panel) {
    panel.append(el("p", { class: "muted" }, "上传文件生成课表。识别后可修改。"));
    panel.append(fileDropzone({
      hint: "拖拽课表或作息文件到这里，或点击选择",
      multiple: true,
      onFiles: (files, { setBusy }) => uploadSection("timetable", files, setBusy),
    }));

    periodsTbody = el("tbody");
    (draft().periods || []).forEach((row) => appendPeriodRow(row));
    panel.append(el("div", { class: "row-gap", style: { justifyContent: "space-between", margin: "10px 0 6px" } },
      el("b", {}, "节次"),
      el("button", { class: "secondary", type: "button", onclick: () => appendPeriodRow() }, "添加节次")));
    panel.append(el("div", { class: "table-wrap", style: { maxWidth: "560px" } },
      el("table", { class: "data-table" },
        el("thead", {}, el("tr", {}, el("th", {}, "顺序 *"), el("th", {}, "自定义名称"), el("th", {}, ""))),
        periodsTbody)));

    panel.append(el("b", { style: { display: "block", margin: "16px 0 6px" } }, "基础课表矩阵（周一至周五 × 节次）"));
    const periodsNow = () => readPeriodsFromDom();
    ttEditor = timetableGridEditor({
      periods: draft().periods || [],
      items: draft().base_timetable || [],
      editable: true,
      defaultRoom: classInputs.room?.value.trim() || null,
    });
    // 节次表格变化时刷新矩阵纵轴
    periodsTbody.addEventListener("input", () => ttEditor.setPeriods(periodsNow()));
    panel.append(ttEditor.el);
    panel.append(el("p", { class: "muted", style: { fontSize: "12px" } }, "点击单元格编辑。清空即可删除课程。"));
  }

  /* ---------- 文件上传 ---------- */

  async function uploadSection(section, files, setBusy) {
    if (files.length > 8) { toast("每次最多上传 8 个文件", "error"); return; }
    const saved = await saveDraft(section);
    if (!saved) return;
    setBusy(true, "正在识别本次文件…");
    const body = new FormData();
    body.set("target_section", section);
    body.set("expected_revision", String(local.session.revision));
    files.forEach((file) => body.append("files", file));
    try {
      const result = await api(`/class-onboarding/sessions/${local.session.id}/files`, { method: "POST", body });
      local.session = result.session;
      renderStep();
      const warnings = result.analysis?.warnings || [];
      toast(warnings.length ? `解析完成，有 ${warnings.length} 项需核对` : "解析完成，请核对结构化数据", warnings.length ? "error" : "success");
      if (warnings.length) toast(warnings.slice(0, 3).join("；"), "info");
    } catch (error) {
      if (error.code === "PENDING_CONFIRMATION_REQUIRED") {
        local.session = await api(`/class-onboarding/sessions/${local.session.id}`);
        renderStep();
      }
      toast(error.message, "error");
      if (error.code === "OPENCLAW_CONNECTION_REQUIRED" || error.code === "OPENCLAW_PROCESSING_FAILED") {
        // 在面板中长期可见
        const panel = document.querySelector(".ob-panel");
        if (panel) panel.prepend(openclawBlocked(state.openclaw, () => helpers.refreshOpenclawDot()));
      }
    } finally {
      setBusy(false);
    }
  }

  /* ---------- 步骤 4：预览与确认 ---------- */

  let reviewBox = null;

  async function doPreview() {
    const saved = await saveDraft("review");
    if (!saved) return;
    try {
      local.proposal = await api(`/class-onboarding/sessions/${local.session.id}/preview`, { method: "POST", body: { requested_by: state.user.username } });
      local.session = { ...local.session, status: "awaiting_confirmation" };
      renderReview();
    } catch (error) {
      if (error.code === "CLASS_NAME_CONFLICT") toast(`班级名称冲突：${error.message}`, "error");
      else toast(error.message, "error");
    }
  }

  function renderReview() {
    clear(reviewBox);
    if (!local.proposal) {
      reviewBox.append(emptyState("尚未生成预览", "点击“生成最终预览”，检查无误后即可创建。"));
      return;
    }
    reviewBox.append(proposalReview(local.proposal));
    const checkbox = el("input", { type: "checkbox", id: "ob-confirm-check" });
    const nameInput = el("input", { type: "text", autocomplete: "off", placeholder: "再次输入完整班级名称" });
    const commitBtn = el("button", { class: "danger", type: "button", disabled: true }, "确认并创建班级");
    const ready = !!local.proposal.preview_json?.ready;
    function update() {
      commitBtn.disabled = !ready || !checkbox.checked || nameInput.value.trim() !== (classInputs.name.value.trim() || draft().class_info?.name || "");
    }
    checkbox.addEventListener("change", update);
    nameInput.addEventListener("input", update);
    commitBtn.addEventListener("click", async () => {
      commitBtn.disabled = true;
      try {
        const completed = await api(`/write-proposals/${local.proposal.id}/confirm`, {
          method: "POST",
          body: { revision: local.proposal.revision, confirmed_by: state.user.username, confirmation_note: "网页端已核对班级、名单与课表" },
        });
        local.classResult = completed.result_json;
        toast("班级已创建", "success");
        await afterClassCreated();
      } catch (error) {
        if (error.code === "PENDING_CONFIRMATION_REQUIRED") {
          toast("预览已过期或草稿已变化，请重新生成预览", "error");
          local.proposal = null;
          renderReview();
        } else if (error.code === "CLASS_LIMIT_REACHED") {
          toast(error.message, "error");
        } else if (error.code === "CLASS_NAME_CONFLICT") {
          toast(error.message, "error");
        } else {
          toast(error.message, "error");
        }
        commitBtn.disabled = false;
      }
    });
    reviewBox.append(el("div", { class: "card", style: { marginTop: "14px" } },
      el("label", { class: "row-gap", style: { alignItems: "center" } }, checkbox, el("span", {}, "我已核对班级、名单、课表和科目。")),
      field("输入班级名称再次确认", nameInput),
      commitBtn));
  }

  /* ---------- 创建成功后：智能体 + 微信 ---------- */

  async function afterClassCreated() {
    await refreshIdentity();
    await refreshClassInfo().catch(() => null);
    local.step = STEPS.length;
    renderDone();
    // 自动创建专属智能体
    try {
      await api(`/classes/${local.classResult.class_id}/agent-binding/provision`, { method: "POST", body: {} });
      local.provisionDone = true;
    } catch (error) {
      local.provisionDone = false;
      local.provisionError = error;
    }
    renderDone();
  }

  let doneBox = null;
  function renderDone() {
    clear(mount);
    const result = local.classResult || {};
    doneBox = el("div", { class: "card" },
      el("h3", {}, `班级「${result.class_name || state.classInfo?.name || ""}」已创建`),
      el("div", { class: "metric-grid" },
        ["student_count", "subject_count", "period_count", "timetable_item_count"].filter((k) => result[k] !== undefined)
          .map((k) => el("div", { class: "metric-card" },
            el("span", { class: "metric-value" }, String(result[k])),
            el("span", { class: "metric-label" }, { student_count: "学生", subject_count: "科目", period_count: "节次", timetable_item_count: "课程" }[k])))),
      el("h3", { style: { marginTop: "14px" } }, "班级专属助手"),
      local.provisionDone
        ? el("p", { class: "muted" }, "专属智能体已创建。微信绑定是可选项，可稍后到“智能体”页面绑定。")
        : local.provisionError
          ? el("div", {},
              el("p", { class: "field-error" }, `智能体创建失败：${local.provisionError.message}`),
              el("button", {
                class: "secondary", type: "button",
                onclick: async (e) => {
                  e.target.disabled = true;
                  try { await api(`/classes/${result.class_id}/agent-binding/provision`, { method: "POST", body: {} }); local.provisionDone = true; local.provisionError = null; } catch (err) { local.provisionError = err; toast(err.message, "error"); }
                  renderDone();
                },
              }, "重试创建智能体"))
          : el("p", { class: "muted" }, "正在创建专属智能体…"),
      local.provisionDone ? (local.qrPanel = qrBindingPanel(result.class_id, { onDone: () => navigate("/dashboard") })).el : null,
      el("div", { class: "row-gap", style: { marginTop: "16px" } },
        el("button", { class: "primary", type: "button", onclick: () => navigate("/dashboard") }, "进入班级工作台"),
        local.provisionDone ? el("button", { class: "secondary", type: "button", onclick: () => { local.qrPanel?.stop(); navigate("/agent"); } }, "暂不绑定") : null));
    mount.append(doneBox);
  }

  /* ---------- 向导骨架 ---------- */

  let panelHost = null;
  let stepsHost = null;
  let saveStateLine = null;

  function renderShell() {
    clear(mount);
    stepsHost = el("div", { class: "wizard-steps", role: "tablist" });
    panelHost = el("div");
    saveStateLine = el("span", { class: "muted", style: { fontSize: "12px" } });
    const prevBtn = el("button", { class: "secondary", type: "button", onclick: async () => { if (await saveDraft(STEPS[local.step].key)) setStep(local.step - 1); } }, "上一步");
    const nextBtn = el("button", { class: "primary", type: "button", onclick: async () => { if (await saveDraft(STEPS[local.step].key)) setStep(local.step + 1); } }, "保存并下一步");
    const saveBtn = el("button", { class: "secondary", type: "button", onclick: async () => { if (await saveDraft(STEPS[local.step].key)) toast("草稿已保存", "success"); } }, "保存草稿");
    const previewBtn = el("button", { class: "primary", type: "button", onclick: doPreview }, "生成最终预览");
    const footer = el("div", { class: "row-gap", style: { marginTop: "16px" } }, prevBtn, saveBtn, saveStateLine, el("span", { class: "spacer" }), nextBtn, previewBtn);
    mount.append(stepsHost, panelHost, footer);
    local._footer = { prevBtn, nextBtn, previewBtn };
  }

  function setStep(step) {
    local.step = Math.max(0, Math.min(STEPS.length - 1, step));
    renderStep();
  }

  function renderStep() {
    const { prevBtn, nextBtn, previewBtn } = local._footer;
    clear(stepsHost);
    STEPS.forEach((s, index) => {
      const btn = el("button", {
        class: `wizard-step${index === local.step ? " active" : ""}${index < local.step ? " done" : ""}`,
        type: "button", role: "tab", "aria-selected": index === local.step ? "true" : "false",
        onclick: () => setStep(index),
      }, el("b", {}, String(index + 1)), s.label);
      stepsHost.append(btn);
    });
    saveStateLine.textContent = `草稿版本 ${local.session.revision} · 会话 ${local.session.id.slice(0, 8)}…`;
    prevBtn.disabled = local.step === 0;
    nextBtn.classList.toggle("hidden", local.step === STEPS.length - 1);
    previewBtn.classList.toggle("hidden", local.step !== STEPS.length - 1);

    const panel = el("div", { class: "card ob-panel" });
    const renderers = [renderClassInfo, renderStudents, renderTimetable, null];
    if (local.step === STEPS.length - 1) {
      reviewBox = el("div");
      panel.append(el("p", { class: "muted" }, "请核对全部内容，再确认创建。"), reviewBox);
      if (local.proposal) renderReview(); else renderReview();
    } else {
      renderers[local.step](panel);
    }
    clear(panelHost);
    panelHost.append(panel);
  }

  renderShell();
  // 恢复到服务器记录的步骤
  const currentStep = local.session.current_step === "seating" ? "review" : local.session.current_step;
  const stepIndex = STEPS.findIndex((s) => s.key === currentStep);
  setStep(stepIndex >= 0 ? stepIndex : 0);
}
