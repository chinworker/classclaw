// 值日管理：规则、排班预览与确认、今日/本周、评分与统计——全部直接调领域接口。

import { el, clear, toast, todayStr, addDaysStr, fmtDate, WEEKDAY_NAMES, weekdayOf } from "../util.js";
import { api, AI_REQUEST_TIMEOUT_MS } from "../api.js";
import { state, refreshStudents } from "../state.js";
import { pageHeader, field, errorPanel, skeleton, dataTable, statusBadge, openModal, confirmDanger, aiButton } from "../components.js";

export async function render(mount) {
  let savedRules = [];
  try { savedRules = await api(`/duty/rules?class_id=${state.classId}`); } catch { /* 规则页仍可使用 */ }
  const host = el("div");
  const tabs = el("div", { class: "tabs", role: "tablist" },
    ...[["rules", "规则"], ["preview", "排班预览"], ["today", "今日 / 本周"], ["scores", "评分与统计"]].map(([key, label], i) =>
      el("button", { class: i === 0 ? "active" : "", type: "button", role: "tab", onclick: (e) => switchTab(key, e.target) }, label)));
  mount.append(pageHeader("值日管理", "设置规则，安排值日。"), tabs, host);

  function switchTab(tab, btn) {
    tabs.querySelectorAll("button").forEach((b) => b.classList.toggle("active", b === btn));
    if (tab === "rules") renderRules();
    if (tab === "preview") renderPreview();
    if (tab === "today") renderToday();
    if (tab === "scores") renderScores();
  }

  /* ---------- 规则 ---------- */
  function renderRules(editingRule = null) {
    clear(host);
    const nameInput = el("input", { type: "text", value: editingRule?.name || "", placeholder: "如：日常值日" });
    const fromInput = el("input", { type: "date", value: editingRule?.effective_from || todayStr() });
    const toInput = el("input", { type: "date", value: editingRule?.effective_to || addDaysStr(todayStr(), 120) });
    const itemsTbody = el("tbody");
    const addItem = (row = {}) => {
      const name = el("input", { type: "text", value: row.name || "", placeholder: "项目，如：擦黑板" });
      const count = el("input", { type: "number", min: "1", value: row.count ?? 1, style: { width: "70px" } });
      const area = el("input", { type: "text", value: row.area || "", placeholder: "区域（可选）" });
      const tr = el("tr", {}, el("td", {}, name), el("td", {}, count), el("td", {}, area),
        el("td", {}, el("button", { class: "text-button", type: "button", onclick: () => tr.remove() }, "删除")));
      itemsTbody.append(tr);
    };
    const initialItems = editingRule?.rule_json?.items || [{ name: "扫地", count: 2 }, { name: "擦黑板", count: 1 }];
    initialItems.forEach(addItem);
    const initialWorkdays = new Set(editingRule?.rule_json?.workdays || [1, 2, 3, 4, 5]);
    const workdayChecks = [1, 2, 3, 4, 5, 6, 7].map((d) => {
      const input = el("input", { type: "checkbox", checked: initialWorkdays.has(d) });
      return { input, node: el("label", { class: "chip", style: { cursor: "pointer" } }, input, ` ${WEEKDAY_NAMES[d]}`), day: d };
    });
    const naturalArea = el("textarea", { rows: "5", placeholder: "如：周五扫地 4 人；王小明周三不值日。" }, editingRule?.original_text || "");
    const analysisBox = el("div");
    const validateInfo = el("div");
    const saveBtn = el("button", { class: "primary", type: "button" }, editingRule ? "保存修改" : "新增规则");
    let analyzedRule = editingRule?.rule_json || null;

    function buildRuleJson() {
      return {
        items: [...itemsTbody.querySelectorAll("tr")].map((tr) => {
          const [n, c, a] = tr.querySelectorAll("input");
          return { name: n.value.trim(), count: Number(c.value), ...(a.value.trim() ? { area: a.value.trim() } : {}) };
        }).filter((i) => i.name),
        workdays: workdayChecks.filter((w) => w.input.checked).map((w) => w.day),
      };
    }

    function describeRule(rule) {
      const days = (rule.workdays || []).map((day) => WEEKDAY_NAMES[day]).join("、") || "未设置星期";
      const items = (rule.items || []).map((item) => `${item.name} ${item.count} 人${item.area ? `（${item.area}）` : ""}`).join("；");
      return `值日时间：${days}；项目：${items || "未识别"}`;
    }

    naturalArea.addEventListener("input", () => { analyzedRule = null; clear(analysisBox); });
    const analyzeAction = aiButton({
      label: "分析补充规则",
      runningLabel: "取消分析",
      onRun: async ({ signal }) => {
        if (!naturalArea.value.trim()) { toast("请先写下补充规则", "error"); return; }
        clear(analysisBox);
        analysisBox.append(el("p", { class: "muted" }, "正在整理规则…"));
        try {
          const result = await api(`/classes/${state.classId}/duty/rules/analyze`, {
            method: "POST",
            body: { text: naturalArea.value.trim(), base_rule: buildRuleJson() },
            timeoutMs: AI_REQUEST_TIMEOUT_MS,
            signal,
          });
          analyzedRule = result.rule_json;
          analysisBox.replaceChildren(
            el("div", { class: "issue issue-ok" }, describeRule(analyzedRule)),
            ...(result.analysis?.warnings || []).map((text) => el("div", { class: "issue issue-warn" }, text)),
          );
        } catch (error) {
          if (error.code === "REQUEST_CANCELLED") analysisBox.replaceChildren(el("p", { class: "muted" }, "已取消分析"));
          else analysisBox.replaceChildren(errorPanel(error));
        }
      },
    });

    saveBtn.addEventListener("click", async () => {
      if (naturalArea.value.trim() && !analyzedRule) { toast("补充说明有变化，请先重新分析", "error"); return; }
      const ruleJson = analyzedRule || buildRuleJson();
      if (!ruleJson) return;
      if (!nameInput.value.trim()) { toast("规则名称必填", "error"); return; }
      saveBtn.disabled = true;
      try {
        await api("/duty/rules/validate", { method: "POST", body: ruleJson });
        const payload = { name: nameInput.value.trim(), original_text: naturalArea.value.trim() || null, rule_json: ruleJson, effective_from: fromInput.value, effective_to: toInput.value, status: "active" };
        const saved = editingRule
          ? await api(`/duty/rules/${editingRule.id}`, { method: "PATCH", body: payload })
          : await api("/duty/rules", { method: "POST", body: { class_id: state.classId, ...payload } });
        savedRules = editingRule ? savedRules.map((rule) => rule.id === saved.id ? saved : rule) : [saved, ...savedRules];
        toast(editingRule ? "值日规则已更新" : "值日规则已新增", "success");
        renderRules();
      } catch (error) {
        clear(validateInfo);
        validateInfo.append(el("div", { class: "issue issue-error" }, error.message),
          ...(error.details?.errors || []).map((item) => el("div", { class: "issue issue-warn" }, item.message || "请检查规则内容")));
      } finally { saveBtn.disabled = false; }
    });

    host.append(el("div", { class: "card" },
      el("div", { class: "row-gap", style: { justifyContent: "space-between" } }, el("h3", {}, editingRule ? `修改规则：${editingRule.name}` : "新增值日规则"),
        editingRule ? el("button", { class: "secondary", type: "button", onclick: () => renderRules() }, "取消修改") : null),
      el("div", { class: "form-grid" }, field("规则名称 *", nameInput), field("生效开始 *", fromInput), field("生效结束 *", toInput)),
      el("b", { style: { display: "block", margin: "8px 0 4px" } }, "值日项目"),
      el("div", { class: "table-wrap", style: { maxWidth: "640px" } },
        el("table", { class: "data-table" }, el("thead", {}, el("tr", {}, el("th", {}, "项目 *"), el("th", {}, "人数"), el("th", {}, "区域"), el("th", {}, ""))), itemsTbody)),
      el("button", { class: "secondary", type: "button", onclick: () => addItem(), style: { marginTop: "8px" } }, "添加项目"),
      el("b", { style: { display: "block", margin: "12px 0 4px" } }, "值日星期"),
      el("div", { class: "chips" }, workdayChecks.map((w) => w.node)),
      el("b", { style: { display: "block", margin: "14px 0 4px" } }, "补充说明（可选）"),
      el("p", { class: "muted" }, "复杂要求直接用平常说话的方式写，系统会先整理成预览供你核对。"),
      naturalArea,
      el("div", { style: { marginTop: "8px" } }, analyzeAction.el), analysisBox,
      el("div", { style: { marginTop: "12px" } }, saveBtn), validateInfo));

    const listCard = el("div", { class: "card" }, el("h3", {}, `已保存规则（${savedRules.length}）`));
    if (!savedRules.length) listCard.append(el("p", { class: "muted" }, "暂无规则。"));
    for (const rule of savedRules) {
      listCard.append(el("div", { class: "duty-rule-row" },
        el("div", {}, el("b", {}, rule.name), el("div", { class: "muted", style: { fontSize: "12px" } }, `${rule.effective_from} 至 ${rule.effective_to} · ${describeRule(rule.rule_json)}`)),
        el("div", { class: "row-gap" },
          el("button", { class: "secondary", type: "button", onclick: () => renderRules(rule) }, "修改"),
          el("button", { class: "text-button", type: "button", style: { color: "var(--danger)" }, onclick: async () => {
            const confirmed = await confirmDanger({ title: "删除值日规则", lines: [`将删除“${rule.name}”。`, "已生成的值日排班不会被删除。"], confirmLabel: "确认删除" });
            if (!confirmed) return;
            try { await api(`/duty/rules/${rule.id}`, { method: "DELETE" }); savedRules = savedRules.filter((item) => item.id !== rule.id); toast("值日规则已删除", "success"); renderRules(); }
            catch (error) { toast(error.message, "error"); }
          } }, "删除"))));
    }
    host.append(listCard);
  }

  /* ---------- 排班预览 ---------- */
  function renderPreview() {
    clear(host);
    const nameInput = el("input", { type: "text", placeholder: "排班名称，如：10 月值日" });
    const startInput = el("input", { type: "date", value: todayStr() });
    const endInput = el("input", { type: "date", value: addDaysStr(todayStr(), 30) });
    const ruleSelect = el("select", {},
      savedRules.length ? savedRules.map((rule) => el("option", { value: rule.id }, rule.name)) : el("option", { value: "" }, "暂无已保存规则"));
    const previewBox = el("div");
    const previewBtn = el("button", { class: "secondary", type: "button" }, "生成预览");
    const confirmBtn = el("button", { class: "primary", type: "button", disabled: true }, "确认排班");
    let previewResult = null;

    function body() {
      const rule = savedRules.find((item) => item.id === ruleSelect.value);
      if (!rule) { toast("请先在“规则”页签保存一条规则", "error"); return null; }
      return { class_id: state.classId, name: nameInput.value.trim() || "值日排班", start_date: startInput.value, end_date: endInput.value, rule_id: rule.id, rule_json: rule.rule_json };
    }

    previewBtn.addEventListener("click", async () => {
      const payload = body();
      if (!payload) return;
      clear(previewBox);
      previewBtn.disabled = true;
      try {
        previewResult = await api("/duty/schedules/preview", { method: "POST", body: payload });
        const students = await refreshStudents().catch(() => []);
        const byId = new Map(students.map((s) => [s.id, s]));
        previewBox.append(
          el("div", { class: "issue issue-ok" }, `预览生成：${previewResult.assignments.length} 条安排 · 均衡度 ${previewResult.balance?.is_balanced ? "均衡" : `最大差 ${previewResult.balance?.max_min_spread}`}`),
          (previewResult.conflicts || []).length ? el("div", { class: "issue issue-error" }, `冲突 ${previewResult.conflicts.length} 项：${previewResult.conflicts.map((c) => `${c.date ? `${fmtDate(c.date)} ` : ""}${c.item_name || ""} 缺 ${c.missing_count} 人`).join("；")}`) : null,
          dataTable({
            columns: [
              { key: "duty_date", label: "日期", render: (a) => fmtDate(a.duty_date) },
              { key: "item_name", label: "项目" },
              { key: "student_id", label: "学生", render: (a) => a.student_name || byId.get(a.student_id)?.name || a.student_id },
            ],
            rows: previewResult.assignments.slice(0, 100),
          }),
          previewResult.assignments.length > 100 ? el("p", { class: "muted" }, `仅展示前 100 条，共 ${previewResult.assignments.length} 条`) : null);
        confirmBtn.disabled = (previewResult.conflicts || []).length > 0;
      } catch (error) {
        previewResult = null;
        previewBox.append(errorPanel(error));
      } finally { previewBtn.disabled = false; }
    });

    confirmBtn.addEventListener("click", async () => {
      if (!previewResult) return;
      const ok = await confirmDanger({
        title: "确认值日排班",
        lines: [`将写入 ${previewResult.assignments.length} 条值日安排（${startInput.value} 至 ${endInput.value}）。`],
        confirmLabel: "确认写入排班",
      });
      if (!ok) return;
      confirmBtn.disabled = true;
      try {
        await api("/duty/schedules/confirm", { method: "POST", body: { ...body(), preview_token: previewResult.preview_token } });
        toast("值日排班已确认", "success");
      } catch (error) {
        if (error.code === "DUTY_SCHEDULE_CONFLICT") toast("规则或日期已变化，请重新预览", "error");
        else toast(error.message, "error");
        confirmBtn.disabled = false;
      }
    });

    host.append(el("div", { class: "card" },
      el("h3", {}, "排班预览与确认"),
      el("p", { class: "muted" }, "选择一条已保存规则，先查看具体安排，再确认使用。"),
      el("div", { class: "form-grid" }, field("排班名称", nameInput), field("使用规则 *", ruleSelect), field("开始日期 *", startInput), field("结束日期 *", endInput)),
      el("div", { class: "row-gap" }, previewBtn, confirmBtn),
      previewBox));
  }

  /* ---------- 今日 / 本周 ---------- */
  async function renderToday() {
    clear(host);
    host.append(skeleton(4));
    let todayList, weekly;
    const weekStart = addDaysStr(todayStr(), -(weekdayOf(todayStr()) - 1));
    try {
      [todayList, weekly] = await Promise.all([
        api(`/duty/today?class_id=${state.classId}&day=${todayStr()}`),
        api(`/duty/weekly?class_id=${state.classId}&week_start=${weekStart}`),
      ]);
    } catch (error) {
      clear(host);
      host.append(errorPanel(error, { onRetry: renderToday }));
      return;
    }
    const students = await refreshStudents().catch(() => []);
    const byId = new Map(students.map((s) => [s.id, s]));
    clear(host);

    function scorePicker(assignment) {
      const box = el("div", { class: "score-picker", aria: { label: `${assignment.item_name}评分` } });
      for (let score = 0; score <= 5; score += 1) {
        const button = el("button", { class: `score-button${Number(assignment.score) === score ? " active" : ""}`, type: "button" }, String(score));
        button.addEventListener("click", async () => {
          box.querySelectorAll("button").forEach((item) => { item.disabled = true; });
          try {
            await api(`/duty/assignments/${assignment.id}/score`, { method: "PUT", body: { score } });
            toast(`已评分 ${score} 分并登记完成`, "success");
            renderToday();
          } catch (error) { toast(error.message, "error"); box.querySelectorAll("button").forEach((item) => { item.disabled = false; }); }
        });
        box.append(button);
      }
      return box;
    }

    host.append(el("div", { class: "card" },
      el("h3", {}, `今日值日 · ${todayStr()}`),
      el("p", { class: "muted" }, "按 0–5 分登记；评分后自动标记完成。未评分的任务会在第二天按 5 分完成。"),
      todayList.length ? dataTable({
        columns: [
          { key: "item_name", label: "项目" },
          { key: "student", label: "学生", render: (a) => byId.get(a.student_id)?.name || a.student_id },
          { key: "status", label: "状态", render: (a) => statusBadge(a.status) },
          { key: "score", label: "评分", render: scorePicker },
          { key: "actions", label: "操作", render: (a) => el("div", { class: "row-gap" },
            el("button", { class: "text-button", type: "button", onclick: () => openReplace(a) }, "临时替换")) },
        ],
        rows: todayList,
      }) : el("p", { class: "muted" }, "今天没有值日任务")));

    const byDate = new Map();
    for (const a of weekly) {
      if (!byDate.has(a.duty_date)) byDate.set(a.duty_date, []);
      byDate.get(a.duty_date).push(a);
    }
    host.append(el("div", { class: "card" },
      el("h3", {}, `本周值日 · ${weekStart} 起`),
      byDate.size ? [...byDate.entries()].map(([date, list]) => el("div", { style: { marginBottom: "10px" } },
        el("b", {}, `${fmtDate(date)} ${WEEKDAY_NAMES[weekdayOf(date)]}`),
        el("div", { class: "chips", style: { margin: "4px 0 0" } }, list.map((a) =>
          el("span", { class: "chip" }, `${a.item_name} · ${byId.get(a.student_id)?.name || "?"}${a.score !== null && a.score !== undefined ? ` · ${a.score}分` : ""} `, statusBadge(a.status))))))
        : el("p", { class: "muted" }, "本周没有值日安排")));

    async function openReplace(assignment) {
      const select = el("select", {}, students.map((s) => el("option", { value: s.id }, `${s.name}（${s.student_no}）`)));
      const noteInput = el("input", { type: "text", placeholder: "替换原因（可选）" });
      openModal({
        title: "临时替换值日学生",
        body: el("div", {},
          el("p", { class: "muted" }, `原安排：${fmtDate(assignment.duty_date)} · ${assignment.item_name} · ${byId.get(assignment.student_id)?.name || ""}`),
          field("替换为 *", select), field("备注", noteInput)),
        actions: [
          { label: "取消", kind: "secondary" },
          {
            label: "替换", kind: "primary",
            onClick: async ({ close, setSubmitting }) => {
              setSubmitting(true);
              try {
                await api(`/duty/assignments/${assignment.id}/replace`, { method: "POST", body: { replacement_student_id: select.value, note: noteInput.value.trim() || null } });
                close(); toast("已临时替换", "success"); renderToday();
              } catch (error) { toast(error.message, "error"); }
              finally { setSubmitting(false); }
            },
          },
        ],
      });
    }
  }

  /* ---------- 评分与统计 ---------- */
  async function renderScores() {
    clear(host);
    host.append(skeleton(4));
    const start = addDaysStr(todayStr(), -30);
    try {
      const stats = await api(`/duty/statistics?class_id=${state.classId}&start_date=${start}&end_date=${todayStr()}`);
      const students = await refreshStudents().catch(() => []);
      const byId = new Map(students.map((s) => [s.id, s]));
      clear(host);
      const rows = stats.students || stats.items || [];
      host.append(el("div", { class: "card" },
        el("h3", {}, `值日统计（${start} 至 ${todayStr()}）`),
        rows.length ? dataTable({
          columns: [
            { key: "student", label: "学生", render: (r) => byId.get(r.student_id)?.name || r.student_id },
            { key: "total", label: "值日次数", render: (r) => r.total ?? r.count ?? "—" },
            { key: "completed", label: "完成", render: (r) => r.completed ?? "—" },
            { key: "average_score", label: "平均得分", render: (r) => r.average_score ?? r.avg_score ?? "—" },
          ],
          rows,
        }) : el("p", { class: "muted" }, "范围内没有值日数据")));
      host.append(el("div", { class: "card" },
        el("h3", {}, "评分口径"),
        el("p", { class: "muted" }, "0 分表示未完成，5 分表示满分完成。当天可修改评分；第二天仍未评分的任务自动按 5 分完成。")));
    } catch (error) {
      clear(host);
      host.append(errorPanel(error, { onRetry: renderScores }));
    }
  }

  renderRules();
}
