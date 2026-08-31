// 每日早报：GET /briefings/morning。

import { el, clear, todayStr, addDaysStr, fmtDateTime, WEEKDAY_NAMES, weekdayOf, periodLabel } from "../util.js";
import { api } from "../api.js";
import { state } from "../state.js";
import { pageHeader, field, errorPanel, skeleton, statusBadge, emptyState } from "../components.js";

export async function render(mount) {
  const dateInput = el("input", { type: "date", value: todayStr() });
  const host = el("div");
  mount.append(
    pageHeader("每日早报", "汇总今日课表、值日、提醒与需要关注的学生。"),
    el("div", { class: "filter-bar" },
      field("日期", dateInput),
      el("button", { class: "secondary", type: "button", onclick: () => { dateInput.value = addDaysStr(dateInput.value, -1); load(); } }, "前一天"),
      el("button", { class: "primary", type: "button", onclick: load }, "查看"),
      el("button", { class: "secondary", type: "button", onclick: () => { dateInput.value = addDaysStr(dateInput.value, 1); load(); } }, "后一天")),
    host);

  async function load() {
    clear(host);
    host.append(skeleton(6));
    let data;
    try {
      data = await api(`/briefings/morning?class_id=${state.classId}&date=${dateInput.value}`);
    } catch (error) {
      clear(host);
      host.append(errorPanel(error, { onRetry: load }));
      return;
    }
    clear(host);
    host.append(el("div", { class: "card" },
      el("h3", {}, `${data.date} · ${WEEKDAY_NAMES[weekdayOf(data.date)]}`)));

    const grid = el("div", { style: { display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(320px, 1fr))", gap: "18px" } });
    host.append(grid);

    // 今日课表
    const lessonCard = card("今日课表");
    if (!data.timetable?.length) lessonCard.append(el("p", { class: "muted" }, "当天没有课程"));
    else data.timetable.forEach((l) => lessonCard.append(el("div", { class: `row-gap${l.is_changed ? " daily-changed" : ""}`, style: { justifyContent: "space-between", padding: "5px 8px", borderRadius: "8px" } },
      el("span", {}, el("b", {}, `${l.period_name || periodLabel(l.period_no)} `), l.is_cancelled ? el("s", {}, l.original_subject) : l.subject, l.teacher ? el("span", { class: "muted" }, ` ${l.teacher}`) : null),
      l.is_changed ? statusBadge("warn", l.is_cancelled ? "已取消" : "临时变更") : null)));
    grid.append(lessonCard);

    // 值日
    const dutyCard = card("值日");
    if (!data.duty_assignments?.length) dutyCard.append(el("p", { class: "muted" }, "当天没有值日安排"));
    else data.duty_assignments.forEach((a) => dutyCard.append(el("p", {}, el("b", {}, a.item_name), el("span", { class: "muted" }, ` ${a.area || ""}`))));
    if (data.duty_vacancies?.length) dutyCard.append(el("p", { class: "field-error" }, `值日空缺：${data.duty_vacancies.join("、")}`));
    grid.append(dutyCard);

    // 今日提醒与安排
    const reminderCard = card("今日提醒");
    if (!data.today_reminders?.length) reminderCard.append(el("p", { class: "muted" }, "今天没有提醒事项"));
    else data.today_reminders.forEach((r) => reminderCard.append(el("div", { style: { padding: "4px 0" } },
      el("div", { class: "row-gap" }, statusBadge(r.priority), el("b", {}, r.title), statusBadge(r.status)),
      el("div", { class: "muted", style: { fontSize: "12px" } },
        r.due_at ? `截止 ${fmtDateTime(r.due_at)}` : "",
        r.reminder_times?.length ? ` · 提醒：${r.reminder_times.map(fmtDateTime).join("、")}` : ""))));
    grid.append(reminderCard);

    // 未交作业
    const missingCard = card("未交作业");
    if (!data.missing_homework?.length) missingCard.append(el("p", { class: "muted" }, "没有未交作业记录"));
    else data.missing_homework.forEach((m) => missingCard.append(el("p", {}, el("b", {}, m.title), el("span", { class: "tag tag-error", style: { marginLeft: "8px" } }, `${m.count} 人未交`))));
    grid.append(missingCard);

    // 临期与逾期
    const dueCard = card("临期与逾期安排");
    const sections = [["今天截止", data.due_today], ["未来 3 天", data.next_3_days], ["已逾期", data.overdue]];
    let any = false;
    for (const [label, list] of sections) {
      if (!list?.length) continue;
      any = true;
      dueCard.append(el("b", { style: { display: "block", marginTop: "6px" } }, label));
      list.forEach((a) => dueCard.append(el("p", { class: "muted", style: { fontSize: "13px" } }, `${a.title}${a.due_at ? ` · ${fmtDateTime(a.due_at)}` : ""}`)));
    }
    if (!any) dueCard.append(el("p", { class: "muted" }, "没有临期或逾期安排"));
    grid.append(dueCard);

    // 关注学生
    const attentionCard = card("关注学生（近 14 天）");
    if (!data.attention_students?.length) attentionCard.append(el("p", { class: "muted" }, "没有触发关注规则的学生"));
    else data.attention_students.forEach((s) => attentionCard.append(el("p", {}, el("b", {}, s.student?.name || "—"), el("span", { class: "muted" }, ` ${(s.reasons || []).map((r) => r.message || r).join("；")}`))));
    grid.append(attentionCard);

    // 数据质量
    if (data.data_quality) {
      const qualityCard = card("数据质量");
      qualityCard.append(el("p", { class: "muted", style: { fontSize: "13px" } },
        `覆盖：学生 ${data.data_quality.counts?.students ?? 0} · 作业 ${data.data_quality.counts?.homework ?? 0} · 事件 ${data.data_quality.counts?.events ?? 0} · 考勤例外 ${data.data_quality.counts?.attendance_exception_records ?? 0}`));
      (data.data_quality.warnings || []).forEach((w) => qualityCard.append(el("p", { class: "field-error", style: { fontSize: "13px" } }, w)));
      if (data.data_quality.missing_dimensions?.length) qualityCard.append(el("p", { class: "muted", style: { fontSize: "13px" } }, `缺口维度：${data.data_quality.missing_dimensions.join("、")}`));
      grid.append(qualityCard);
    }
  }

  function card(title) {
    const node = el("div", { class: "card", style: { margin: 0 } }, el("h3", {}, title));
    return node;
  }

  await load();
}
