// 班级创建向导（onboarding）：网页专属。
// 流程：班级信息 → 学生名单 → 课表与节次 → 预览确认 → 智能体与微信。
// 只有最终确认走 /preview + /write-proposals/{id}/confirm；草稿反复编辑用 PATCH + expected_revision。

import { el, clear, toast, debounce } from "../util.js";
import { api, AI_REQUEST_TIMEOUT_MS } from "../api.js";
import { appConfig, featureEnabled } from "../config.js";
import { state, refreshIdentity, refreshClassInfo, refreshOpenclaw } from "../state.js";
import { navigate } from "../router.js";
import {
  field, fieldError, fileDropzone, proposalReview, qrBindingPanel, statusBadge, openclawBlocked, emptyState, showAiRejection,
} from "../components.js";
import { compareStudents } from "../studentOrder.js";
import { timetableGridEditor } from "../timetableGrid.js";

let activeView = null;

export function dispose() {
  if (!activeView) return;
  activeView.qrPanel?.dispose();
  activeView = null;
}

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
    stepSaved: true,
    savedSteps: new Set(),
    maxUnlockedStep: 0,
    analysisInProgress: false,
    previewInProgress: false,
    commitInProgress: false,
  };
  activeView = local;

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
      periods: draft().periods || [],
      base_timetable: ttEditor ? ttEditor.getItems() : (draft().base_timetable || []),
    };
  }

  async function saveDraft(stepKey = null, { allowDuringAnalysis = false, signal = null } = {}) {
    if (local.analysisInProgress && !allowDuringAnalysis) {
      toast("请先完成或取消文件解析", "error");
      return false;
    }
    const body = {
      expected_revision: local.session.revision,
      draft_patch: collectDraft(),
      replace_lists: true,
    };
    if (stepKey) body.current_step = stepKey;
    try {
      local.session = await api(`/class-onboarding/sessions/${local.session.id}`, { method: "PATCH", body, signal });
      local.savedSteps.add(local.step);
      local.stepSaved = true;
      local.maxUnlockedStep = Math.max(local.maxUnlockedStep, Math.min(local.step + 1, STEPS.length - 1));
      local.proposal = null;
      refreshNavigation();
      return true;
    } catch (error) {
      if (error.code === "REQUEST_CANCELLED") return false;
      if (error.code === "PENDING_CONFIRMATION_REQUIRED") {
        toast("草稿在其他地方被修改过，已重新载入最新版本", "error");
        local.session = await api(`/class-onboarding/sessions/${local.session.id}`);
        local.savedSteps.add(local.step);
        local.stepSaved = true;
        renderStep();
        return false;
      }
      toast(error.message, "error");
      return false;
    }
  }

  function markStepDirty() {
    if (local.step >= STEPS.length - 1) return;
    local.savedSteps.delete(local.step);
    local.stepSaved = false;
    local.proposal = null;
    refreshNavigation();
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
    const semesterDefaults = appConfig.semester_defaults || {};
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
      const input = el("input", {
        type,
        value: info[key] || semesterDefaults[key] || "",
        placeholder: placeholder || "",
        autocomplete: "off",
      });
      classInputs[key] = input;
      grid.append(field(label, input, null));
    }
    classInputs.name.addEventListener("input", () => { local.nameCheck = null; nameStatus.style.color = ""; doNameCheck(); });
    classInputs.name.addEventListener("blur", () => doNameCheck());
    panel.append(
      el("p", { class: "muted" }, "确定性字段，保存时不会被智能体改写。"),
      el("p", { class: "muted" }, "今天处于学期范围内时默认使用当前学期；寒暑假期间默认使用未来最近的一学期。春季自农历元宵节至 7 月 1 日，秋季自 9 月 1 日至次年春节前一周。"),
      grid, nameStatus);
    if (info.name) doNameCheck();
  }

  /* ---------- 步骤 2：学生名单 ---------- */

  let studentsTbody = null;
  const STUDENT_COLS = [
    ["student_no", "学号 *"], ["name", "姓名 *"], ["gender", "性别"], ["phone", "电话"],
    ["boarding_status", "住宿"], ["group_no", "小组"], ["notes", "备注"],
  ];

  function appendStudentRow(row = {}, dirty = false) {
    const tr = el("tr");
    for (const [key] of STUDENT_COLS) {
      const input = el("input", { type: "text", value: row[key] ?? "", dataset: { key } });
      tr.append(el("td", {}, input));
    }
    const del = el("button", { class: "text-button", type: "button", onclick: () => { tr.remove(); markStepDirty(); } }, "删除");
    tr.append(el("td", {}, del));
    studentsTbody.append(tr);
    if (dirty) markStepDirty();
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
    studentRowsHost.current = (draft().students || []).map((s) => ({ ...s })).sort(compareStudents);
    panel.append(el("p", { class: "muted" }, "上传文件生成名单。识别后可修改。"));
    panel.append(fileDropzone({
      hint: "拖拽学生名单文件到这里，或点击选择（最多 8 个）",
      multiple: true,
      maxFiles: 8,
      manualStart: true,
      busyText: "正在解析学生名单…",
      disabled: !featureEnabled("file_analysis"),
      onBusyChange: setAnalysisBusy,
      onFiles: (files, { signal, taskId }) => uploadSection("students", files, signal, taskId),
    }));
    const addBtn = el("button", { class: "secondary", type: "button", onclick: () => appendStudentRow({}, true) }, "添加学生");
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

  function renderTimetable(panel) {
    panel.append(el("p", { class: "muted" }, "上传文件生成课表。识别后可修改。"));
    panel.append(fileDropzone({
      hint: "拖拽课表或作息文件到这里，或点击选择",
      multiple: true,
      maxFiles: 8,
      manualStart: true,
      busyText: "正在解析课表…",
      disabled: !featureEnabled("file_analysis"),
      onBusyChange: setAnalysisBusy,
      onFiles: (files, { signal, taskId }) => uploadSection("timetable", files, signal, taskId),
    }));
    panel.append(el("b", { style: { display: "block", margin: "16px 0 6px" } }, "基础课表矩阵（周一至周五 × 节次）"));
    ttEditor = timetableGridEditor({
      periods: draft().periods || [],
      items: draft().base_timetable || [],
      editable: true,
      defaultRoom: classInputs.room?.value.trim() || draft().class_info?.room || null,
      onChange: markStepDirty,
    });
    panel.append(ttEditor.el);
    panel.append(el("p", { class: "muted", style: { fontSize: "12px" } },
      (draft().periods || []).length ? "点击单元格编辑。清空即可删除课程。" : "上传课表后会在这里生成可编辑预览。"));
  }

  /* ---------- 文件上传 ---------- */

  function setAnalysisBusy(value) {
    local.analysisInProgress = value;
    refreshNavigation();
  }

  async function uploadSection(section, files, signal, taskId) {
    if (files.length > 8) { toast("每次最多上传 8 个文件", "error"); return; }
    const saved = await saveDraft(section, { allowDuringAnalysis: true, signal });
    if (!saved) return;
    const body = new FormData();
    body.set("target_section", section);
    body.set("expected_revision", String(local.session.revision));
    files.forEach((file) => body.append("files", file));
    try {
      const result = await api(`/class-onboarding/sessions/${local.session.id}/files`, {
        method: "POST",
        body,
        timeoutMs: AI_REQUEST_TIMEOUT_MS,
        signal,
        aiTaskId: taskId,
      });
      if (!result.analysis?.accepted) {
        showAiRejection(result.analysis, section === "students" ? "学生名单未采用" : "课表数据未采用");
        return;
      }
      local.session = result.session;
      local.savedSteps.add(local.step);
      local.stepSaved = true;
      local.maxUnlockedStep = Math.max(local.maxUnlockedStep, Math.min(local.step + 1, STEPS.length - 1));
      renderStep();
      const analysis = result.analysis || {};
      const warnings = analysis.warnings || [];
      const review = analysis.review_required || warnings.length > 0;
      toast(review ? "解析完成，已填入草稿，请逐项核对" : "解析完成，请核对结构化数据", review ? "error" : "success");
      const notes = [...(analysis.reasons || []), ...warnings];
      if (review && notes.length) toast(notes.slice(0, 3).join("；"), "info");
    } catch (error) {
      if (error.code === "REQUEST_CANCELLED") {
        toast("已取消解析", "info");
        return;
      }
      if (error.code === "PENDING_CONFIRMATION_REQUIRED") {
        local.session = await api(`/class-onboarding/sessions/${local.session.id}`);
        renderStep();
      }
      toast(error.message, "error");
      if (error.code === "OPENCLAW_CONNECTION_REQUIRED" || error.code === "OPENCLAW_PROCESSING_FAILED") {
        const panel = document.querySelector(".ob-panel");
        if (panel) {
          const status = await refreshOpenclaw({ force: true }).catch(() => null);
          await helpers.refreshOpenclawDot({ force: false });
          panel.querySelector(".blocked-panel")?.remove();
          if (!(status?.gateway_live && status?.plugin_ready)) {
            let blockedPanel;
            blockedPanel = openclawBlocked(status, async () => {
              const latest = await refreshOpenclaw({ force: true });
              await helpers.refreshOpenclawDot({ force: false });
              if (!(latest.gateway_live && latest.plugin_ready)) throw new Error(latest.error || "文件识别服务仍未恢复");
              blockedPanel.remove();
              toast("文件识别服务已恢复，可以重新上传", "success");
            });
            panel.prepend(blockedPanel);
          }
        }
      }
    }
  }

  /* ---------- 步骤 4：预览与确认 ---------- */

  let reviewBox = null;

  async function doPreview() {
    const saved = await saveDraft("review");
    if (!saved) return;
    local.previewInProgress = true;
    refreshNavigation();
    try {
      local.proposal = await api(`/class-onboarding/sessions/${local.session.id}/preview`, { method: "POST", body: { requested_by: state.user.username } });
      local.session = { ...local.session, status: "awaiting_confirmation" };
      renderReview();
    } catch (error) {
      if (error.code === "CLASS_NAME_CONFLICT") toast(`班级名称冲突：${error.message}`, "error");
      else toast(error.message, "error");
    } finally {
      local.previewInProgress = false;
      refreshNavigation();
    }
  }

  function renderReview() {
    clear(reviewBox);
    if (!local.proposal) {
      reviewBox.append(emptyState("尚未生成预览", "点击“生成最终预览”，检查无误后即可创建。"));
      return;
    }
    reviewBox.append(proposalReview(local.proposal));
  }

  async function doCommit() {
    if (!local.proposal?.preview_json?.ready || local.commitInProgress) return;
    local.commitInProgress = true;
    refreshNavigation();
    try {
      const completed = await api(`/write-proposals/${local.proposal.id}/confirm`, {
        method: "POST",
        body: { revision: local.proposal.revision, confirmed_by: state.user.username, confirmation_note: "网页端根据最终预览创建班级" },
      });
      local.classResult = completed.result_json;
      toast("班级已创建", "success");
      await afterClassCreated();
    } catch (error) {
      if (error.code === "PENDING_CONFIRMATION_REQUIRED") {
        toast("预览已过期或草稿已变化，请重新生成预览", "error");
        local.proposal = null;
        renderReview();
      } else {
        toast(error.message, "error");
      }
    } finally {
      local.commitInProgress = false;
      refreshNavigation();
    }
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
        ? el("p", { class: "muted" }, "专属智能体已创建。微信绑定是可选项，可稍后到“账户设置”中绑定。")
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
    const prevBtn = el("button", {
      class: "secondary", type: "button", onclick: async () => {
        if (local.analysisInProgress) return;
        if (!local.stepSaved && !(await saveDraft(STEPS[local.step].key))) return;
        setStep(local.step - 1);
      },
    }, "上一步");
    const nextBtn = el("button", {
      class: "primary", type: "button", onclick: () => {
        if (!local.stepSaved) { toast("请先保存当前步骤", "error"); return; }
        setStep(local.step + 1);
      },
    }, "下一步");
    const saveBtn = el("button", { class: "secondary", type: "button", onclick: async () => { if (await saveDraft(STEPS[local.step].key)) toast("草稿已保存", "success"); } }, "保存草稿");
    const previewBtn = el("button", { class: "secondary", type: "button", onclick: doPreview }, "生成最终预览");
    const commitBtn = el("button", { class: "primary", type: "button", onclick: doCommit }, "创建班级");
    const primaryActions = el("div", { class: "row-gap" }, saveBtn, nextBtn, previewBtn, commitBtn);
    const footer = el("div", { class: "row-gap", style: { marginTop: "16px" } }, prevBtn, saveStateLine, el("span", { class: "spacer" }), primaryActions);
    mount.append(stepsHost, panelHost, footer);
    local._footer = { prevBtn, nextBtn, saveBtn, previewBtn, commitBtn, stepButtons: [] };
  }

  function setStep(step) {
    if (local.analysisInProgress) {
      toast("文件正在解析，请先取消解析", "error");
      return;
    }
    if (step > local.step && (!local.stepSaved || step > local.maxUnlockedStep)) {
      toast("请先保存当前步骤", "error");
      return;
    }
    local.step = Math.max(0, Math.min(STEPS.length - 1, step));
    if (local.step === STEPS.length - 1) local.savedSteps.add(local.step);
    local.stepSaved = local.savedSteps.has(local.step);
    renderStep();
  }

  function refreshNavigation() {
    if (!local._footer) return;
    const { prevBtn, nextBtn, saveBtn, previewBtn, commitBtn, stepButtons } = local._footer;
    saveStateLine.textContent = local.stepSaved
      ? `已保存 · 草稿版本 ${local.session.revision}`
      : `当前步骤有未保存修改 · 草稿版本 ${local.session.revision}`;
    prevBtn.disabled = local.step === 0 || local.analysisInProgress;
    saveBtn.disabled = local.analysisInProgress || local.step === STEPS.length - 1;
    nextBtn.disabled = local.analysisInProgress || !local.stepSaved;
    previewBtn.disabled = local.analysisInProgress || local.previewInProgress || local.commitInProgress || !local.stepSaved;
    commitBtn.disabled = local.analysisInProgress || local.previewInProgress || local.commitInProgress || !local.proposal?.preview_json?.ready;
    stepButtons.forEach((button, index) => {
      button.disabled = local.analysisInProgress || (index > local.step && (!local.stepSaved || index > local.maxUnlockedStep));
    });
  }

  function renderStep() {
    const { nextBtn, saveBtn, previewBtn, commitBtn } = local._footer;
    clear(stepsHost);
    local._footer.stepButtons = [];
    STEPS.forEach((s, index) => {
      const btn = el("button", {
        class: `wizard-step${index === local.step ? " active" : ""}${index < local.step ? " done" : ""}`,
        type: "button", role: "tab", "aria-selected": index === local.step ? "true" : "false",
        onclick: async () => {
          if (index < local.step && !local.stepSaved && !(await saveDraft(STEPS[local.step].key))) return;
          setStep(index);
        },
      }, el("b", {}, String(index + 1)), s.label);
      local._footer.stepButtons.push(btn);
      stepsHost.append(btn);
    });
    nextBtn.classList.toggle("hidden", local.step === STEPS.length - 1);
    saveBtn.classList.toggle("hidden", local.step === STEPS.length - 1);
    previewBtn.classList.toggle("hidden", local.step !== STEPS.length - 1);
    commitBtn.classList.toggle("hidden", local.step !== STEPS.length - 1);

    const panel = el("div", { class: "card ob-panel" });
    const renderers = [renderClassInfo, renderStudents, renderTimetable, null];
    if (local.step === STEPS.length - 1) {
      reviewBox = el("div");
      panel.append(el("p", { class: "muted" }, "请核对全部内容，再确认创建。"), reviewBox);
      renderReview();    } else {
      renderers[local.step](panel);
      panel.addEventListener("input", markStepDirty);
      panel.addEventListener("change", markStepDirty);
    }
    clear(panelHost);
    panelHost.append(panel);
    refreshNavigation();
  }

  renderShell();
  // 恢复到服务器记录的步骤
  const currentStep = local.session.current_step === "seating" ? "review" : local.session.current_step;
  const stepIndex = STEPS.findIndex((s) => s.key === currentStep);
  local.step = stepIndex >= 0 ? stepIndex : 0;
  local.maxUnlockedStep = local.step;
  if (local.session.revision > 1) {
    for (let index = 0; index <= local.step; index += 1) local.savedSteps.add(index);
  }
  local.stepSaved = local.savedSteps.has(local.step);
  renderStep();
}
