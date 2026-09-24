// 点名广播：三段式或自定义句子，冻结后同时显示与播报；不是考勤。

import { el, clear, toast, fmtDateTime, debounce } from "../util.js";
import { api } from "../api.js";
import { state, refreshStudents } from "../state.js";
import { compareStudents } from "../studentOrder.js";
import { appConfig } from "../config.js";
import { pageHeader, field, errorPanel, skeleton, emptyState, dataTable, statusBadge, metricCard } from "../components.js";

const TIME_PICKS = ["现在", "下课后", "今天大课间", "放学后", "马上"];
const TASK_PICKS = ["去扫地", "来老师办公室", "到讲台领取作业", "来一趟办公室", "到教室后门集合"];
let activeView = null;

export function dispose() {
  if (activeView) {
    window.clearTimeout(activeView.pollTimer);
    activeView.preview?.cancel?.();
    activeView.controller?.abort();
  }
  activeView = null;
}

function studentPicker(students, selected, onChange) {
  const search = el("input", { type: "search", placeholder: "搜索学号或姓名", "aria-label": "搜索学生" });
  const listHost = el("div", { class: "student-pick-list", role: "group", "aria-label": "本班学生" });

  function draw() {
    const needle = search.value.trim().toLowerCase();
    const rows = students.filter((row) => !needle
      || row.student_no.toLowerCase().includes(needle) || row.name.toLowerCase().includes(needle));
    clear(listHost);
    if (!rows.length) {
      listHost.append(emptyState("没有匹配的学生", "换个学号或姓名再试"));
      return;
    }
    for (const row of rows) {
      const box = el("input", { type: "checkbox", value: row.id, checked: selected.has(row.id),
        "aria-label": `${row.student_no} ${row.name}` });
      box.addEventListener("change", () => {
        if (box.checked) selected.add(row.id); else selected.delete(row.id);
        onChange();
      });
      listHost.append(el("label", { class: "student-pick-item" }, box,
        el("span", { class: "student-no" }, row.student_no), el("span", {}, row.name)));
    }
  }

  search.addEventListener("input", draw);
  draw();
  const counter = el("span", { class: "muted" }, "");
  const box = el("div", { class: "card" },
    el("h3", {}, "选择学生"),
    el("p", { class: "muted" }, "名单按学号自然升序；可单选或多选。自定义句子模式不需要选人。"),
    field("搜索", search),
    el("div", { class: "row-gap" },
      el("button", { class: "text-button", type: "button", onclick: () => { selected.clear(); draw(); onChange(); } }, "清空"),
      el("button", { class: "text-button", type: "button", onclick: () => { students.forEach((row) => selected.add(row.id)); draw(); onChange(); } }, "全选"),
      counter),
    listHost);
  return { el: box, redraw: draw, counter };
}

function quickPicks(label, values, input, onChange) {
  return field(label, el("div", { class: "quick-picks" },
    values.map((value) => el("button", { class: "secondary", type: "button",
      onclick: () => { input.value = value; onChange(); } }, value)), input));
}

export async function render(mount, ctx, helpers) {
  clear(mount);
  mount.append(skeleton(5));
  const classId = state.classId;
  const limits = appConfig.classroom || {};
  let status;
  let students;
  try {
    [status, students] = await Promise.all([
      api(`/classes/${classId}/classroom/status`),
      refreshStudents(),
    ]);
  } catch (error) {
    clear(mount);
    mount.append(errorPanel(error, { onRetry: () => render(mount, ctx, helpers) }));
    return;
  }

  clear(mount);
  const device = status.device;
  const view = {
    classId,
    selected: new Set(),
    mode: "three_part",
    merge: "combined",
    previewData: null,
    pollingId: null,
    pollTimer: null,
  };
  activeView = view;
  const live = () => activeView === view && mount.isConnected && state.classId === classId;

  mount.append(pageHeader("点名广播",
    "以「称谓＋时间＋事项」组成完整句子，教室屏幕显示全文、扬声器播报相同内容。这不是考勤，不判断到场情况。"));

  const deviceCard = el("div", { class: "card" },
    el("h3", {}, "教室终端"),
    el("div", { class: "metric-grid" },
      metricCard("连接状态", device?.online ? "在线" : "离线", device ? null : "尚未配对"),
      metricCard("显示屏", device?.display_name || "未设置"),
      metricCard("扬声器", device?.audio_output_name || "未设置"),
      metricCard("系统音量", device?.volume_level == null ? "未知" : `${device.volume_level}%`, device?.muted ? "已静音" : null)),
    el("p", { class: "muted" }, status.note),
    (status.device_warnings || []).length
      ? el("ul", { class: "warning-list" }, status.device_warnings.map((text) => el("li", {}, text))) : null,
    !device
      ? el("p", {}, el("a", { href: "#/classroom" }, "先去教室设备页完成终端配对"))
      : null);
  mount.append(deviceCard);

  /* ---------- 句子编辑 ---------- */
  const timeInput = el("input", { type: "text", value: "现在", maxlength: 60, "aria-label": "时间" });
  const taskInput = el("input", { type: "text", placeholder: "例如：去扫地", maxlength: 200, "aria-label": "事项" });
  const salutationInput = el("input", { type: "text", value: "同学", maxlength: 60, "aria-label": "称呼" });
  const customInput = el("textarea", { rows: 3, maxlength: limits.broadcast_max_chars || 160,
    placeholder: "直接填写完整句子，例如：请今天负责卫生的同学现在带好工具到教室后门集合。", "aria-label": "自定义句子" });
  const mergeHost = el("div", { class: "row-gap" });
  const displayInput = el("input", { type: "number", min: 3, max: limits.display_seconds_max || 120,
    value: limits.display_seconds_default || 15, "aria-label": "停留时长（秒）" });
  const repeatInput = el("input", { type: "number", min: 1, max: limits.speak_repeat_max || 3, value: 1, "aria-label": "播报次数" });
  const volumeInput = el("input", { type: "number", min: 0, max: limits.volume_ceiling || 100, placeholder: "不改变", "aria-label": "本次播报音量" });
  const screenSelect = el("select", { "aria-label": "目标屏幕" },
    el("option", { value: "" }, "终端默认显示屏"),
    ...[...new Set([...(device?.capabilities?.displays || [])])].map((name) => el("option", { value: name }, name)));

  const picker = studentPicker([...students].sort(compareStudents), view.selected, () => { picker.redraw(); schedulePreview(); });
  const previewHost = el("div", { class: "broadcast-preview", "aria-live": "polite" }, el("p", { class: "muted" }, "填写后自动预览最终句子。"));
  const warningHost = el("div");

  function body() {
    const common = {
      mode: view.mode,
      merge_mode: view.merge,
      display_seconds: Number(displayInput.value) || null,
      repeat_count: Number(repeatInput.value) || 1,
      volume: volumeInput.value === "" ? null : Number(volumeInput.value),
      target_screen: screenSelect.value || null,
    };
    return view.mode === "custom"
      ? { ...common, text: customInput.value }
      : { ...common, student_ids: [...view.selected], salutation: salutationInput.value,
          time_phrase: timeInput.value, predicate: taskInput.value };
  }

  function drawMergeOptions() {
    clear(mergeHost);
    if (view.mode !== "three_part") return;
    for (const [value, label, hint] of [["combined", "合并成一句", "请张三、李四同学下课后来老师办公室。"],
                                        ["per_student", "逐人分别播报", "每人一句完整句子，按学号顺序逐句显示并播报"]]) {
      const radio = el("input", { type: "radio", name: "merge-mode", value, checked: view.merge === value });
      radio.addEventListener("change", () => { view.merge = value; schedulePreview(); });
      mergeHost.append(el("label", { class: "radio-row" }, radio, el("span", {}, label), el("small", { class: "muted" }, hint)));
    }
  }

  function drawMode() {
    threePartHost.hidden = view.mode !== "three_part";
    customHost.hidden = view.mode !== "custom";
    picker.el.hidden = view.mode !== "three_part";
    threePartButton.setAttribute("aria-pressed", String(view.mode === "three_part"));
    customButton.setAttribute("aria-pressed", String(view.mode === "custom"));
    drawMergeOptions();
    schedulePreview();
  }

  const threePartHost = el("div", { class: "form-grid" },
    quickPicks("时间", TIME_PICKS, timeInput, schedulePreview),
    field("称呼（接在姓名后）", salutationInput, "默认「同学」，可改成「小朋友」等；留空则只显示姓名"),
    quickPicks("事项（谓词）", TASK_PICKS, taskInput, schedulePreview));
  const customHost = el("div", {},
    field("完整句子", customInput, "按原文显示和播报，不会自动追加「请」「同学」或标点"));

  for (const input of [timeInput, taskInput, salutationInput, customInput, displayInput, repeatInput, volumeInput]) {
    input.addEventListener("input", schedulePreview);
  }
  screenSelect.addEventListener("change", schedulePreview);

  const sendButton = el("button", { class: "primary", type: "button", disabled: true }, "显示并播报");
  const threePartButton = el("button", { class: "secondary", type: "button" }, "三段式");
  const customButton = el("button", { class: "secondary", type: "button" }, "自定义句子");
  threePartButton.addEventListener("click", () => { view.mode = "three_part"; drawMode(); });
  customButton.addEventListener("click", () => { view.mode = "custom"; drawMode(); });
  const modeButtons = el("div", { class: "segmented", role: "group", "aria-label": "点名方式" }, threePartButton, customButton);

  mount.append(el("div", { class: "card" },
    el("h3", {}, "编写广播内容"), modeButtons, picker.el, threePartHost, customHost,
    field("合并方式", mergeHost),
    el("div", { class: "form-grid" },
      field("停留时长（秒）", displayInput), field("播报次数", repeatInput),
      field("本次音量（%）", volumeInput, "留空表示不改变教室音量"), field("目标屏幕", screenSelect)),
    previewHost, warningHost, sendButton));

  /* ---------- 预览 ---------- */
  async function loadPreview() {
    if (!live()) return;
    const revision = view.previewRevision;
    const payload = body();
    if (view.mode === "three_part" && !payload.student_ids.length) {
      view.previewData = null;
      clear(previewHost).append(el("p", { class: "muted" }, "请先选择学生。"));
      return;
    }
    if (view.mode === "custom" && !payload.text.trim()) {
      view.previewData = null;
      clear(previewHost).append(el("p", { class: "muted" }, "请填写完整句子。"));
      return;
    }
    try {
      const data = await api(`/classes/${classId}/classroom/broadcasts/preview`, { method: "POST", body: payload });
      if (!live() || revision !== view.previewRevision) return;
      view.previewData = data;
      view.previewBody = payload;
      clear(previewHost).append(
        el("b", {}, `最终句子（${data.texts.length} 句，${data.merge_mode === "per_student" ? "逐人" : "合并"}）`),
        el("ol", { class: "broadcast-texts" }, data.texts.map((text) => el("li", {}, text))),
        el("p", { class: "muted" }, `停留 ${data.display_seconds} 秒 · 播报 ${data.repeat_count} 次 · 间隔 ${data.gap_seconds} 秒`,
          data.recipients.length ? ` · 对象：${data.recipients.map((row) => `${row.student_no} ${row.name}`).join("、")}` : ""));
      clear(warningHost);
      for (const text of data.warnings || []) warningHost.append(el("p", { class: "field-error" }, text));
      sendButton.disabled = view.sending || !data.device_online;
    } catch (error) {
      if (!live() || revision !== view.previewRevision) return;
      view.previewData = null;
      clear(previewHost).append(el("p", { class: "field-error", role: "alert" }, error.message));
      sendButton.disabled = true;
    }
  }

  function schedulePreview() {
    view.previewRevision = (view.previewRevision || 0) + 1;
    view.previewData = null;
    view.previewBody = null;
    sendButton.disabled = true;
    if (!view.preview) view.preview = debounce(loadPreview, 350);
    view.preview();
  }

  /* ---------- 发送与状态 ---------- */
  const resultHost = el("div");
  const historyHost = el("div");
  mount.append(el("div", { class: "card" }, el("h3", {}, "本次结果"), resultHost));
  mount.append(el("div", { class: "card" }, el("h3", {}, "最近点名"), historyHost));

  sendButton.addEventListener("click", async () => {
    if (view.sending) return;
    if (!view.previewData || JSON.stringify(view.previewBody) !== JSON.stringify(body())) {
      toast("请先确认最新预览内容", "error"); return;
    }
    view.sending = true;
    sendButton.disabled = true;
    try {
      const data = await api(`/classes/${classId}/classroom/broadcasts`, {
        method: "POST", body: { ...view.previewBody, expected_texts: view.previewData.texts },
      });
      if (!live()) return;
      toast("已下发教室终端", "success");
      view.pollingId = data.broadcast_id;
      drawResult(data);
      await pollResult();
      await loadHistory();
    } catch (error) {
      if (!live()) return;
      toast(error.message, "error");
      clear(resultHost).append(el("p", { class: "field-error", role: "alert" }, `${error.message}（${error.code}）`));
    } finally {
      view.sending = false;
      if (live()) sendButton.disabled = !view.previewData?.device_online;
    }
  });

  function drawResult(row) {
    clear(resultHost).append(
      el("ol", { class: "broadcast-texts" }, (row.texts || []).map((text) => el("li", {}, text))),
      el("div", { class: "row-gap" },
        el("span", {}, "屏幕显示："), statusBadge(row.display_status),
        el("span", {}, "语音播报："), statusBadge(row.speak_status)),
      el("p", {}, row.status_note || ""),
      el("p", { class: "muted" }, `发起：${row.created_by || "—"} · ${fmtDateTime(row.created_at)}`),
      el("div", { class: "row-gap" },
        el("button", { class: "secondary", type: "button", onclick: () => control(row.broadcast_id, "stop") }, "停止播报"),
        el("button", { class: "secondary", type: "button", onclick: () => control(row.broadcast_id, "clear") }, "清除屏幕内容")));
  }

  async function control(broadcastId, action) {
    try {
      await api(`/classes/${classId}/classroom/broadcasts/${broadcastId}/${action}`, { method: "POST", body: {} });
      if (live()) toast(action === "stop" ? "已下发停止播报" : "已下发清除屏幕", "success");
      await pollResult(true);
    } catch (error) {
      if (live()) toast(error.message, "error");
    }
  }

  async function pollResult(once = false) {
    if (!live() || !view.pollingId) return;
    window.clearTimeout(view.pollTimer);
    const broadcastId = view.pollingId;
    try {
      const row = await api(`/classes/${classId}/classroom/broadcasts/${broadcastId}`);
      if (!live() || view.pollingId !== broadcastId) return;
      drawResult(row);
      const settled = ["succeeded", "failed", "expired", "unknown"];
      if (!once && !(settled.includes(row.display_status) && settled.includes(row.speak_status))) {
        view.pollTimer = window.setTimeout(() => { if (live()) void pollResult(); }, 2000);
      }
    } catch (error) {
      if (live()) clear(resultHost).append(errorPanel(error, { onRetry: () => pollResult(true) }));
    }
  }

  async function loadHistory() {
    if (!live()) return;
    try {
      const data = await api(`/classes/${classId}/classroom/broadcasts?page=1&page_size=10`);
      if (!live()) return;
      clear(historyHost).append(dataTable({
        columns: [
          { key: "created_at", label: "时间", render: (row) => fmtDateTime(row.created_at) },
          { key: "texts", label: "内容", render: (row) => el("div", {}, row.texts.map((text) => el("div", {}, text))) },
          { key: "mode", label: "方式", render: (row) => (row.mode === "custom" ? "自定义" : row.merge_mode === "per_student" ? "三段式·逐人" : "三段式·合并") },
          { key: "display_status", label: "显示", render: (row) => statusBadge(row.display_status) },
          { key: "speak_status", label: "播报", render: (row) => statusBadge(row.speak_status) },
          { key: "status_note", label: "结果说明" },
        ],
        rows: data.items,
        empty: { title: "还没有点名记录", hint: "发送后会在这里显示真实的显示与播报状态" },
        caption: "最近点名广播",
      }));
    } catch (error) {
      if (live()) clear(historyHost).append(errorPanel(error, { onRetry: loadHistory }));
    }
  }

  drawMode();
  await loadHistory();
}
