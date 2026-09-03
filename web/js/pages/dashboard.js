// 今日仪表盘：全部数字来自真实接口，无演示数据。

import { el, clear, todayStr, fmtDateTime, WEEKDAY_NAMES, weekdayOf, periodLabel } from "../util.js";
import { api } from "../api.js";
import { state } from "../state.js";
import { navigate } from "../router.js";
import { metricCard, statusBadge, skeleton, errorPanel } from "../components.js";

export async function render(mount, ctx, helpers) {
  clear(mount);
  mount.append(skeleton(6));
  const classId = state.classId;
  const today = todayStr();

  const safe = (p) => p.catch((error) => ({ __error: error }));
  const [summary, daily, dutyToday, arrangements] = await Promise.all([
    safe(api(`/classes/${classId}/summary`)),
    safe(api(`/classes/${classId}/timetable/daily?lesson_date=${today}`)),
    safe(api(`/duty/today?class_id=${classId}&day=${today}`)),
    safe(api(`/arrangements?class_id=${classId}`)),
  ]);
  clear(mount);

  if (summary.__error) {
    mount.append(errorPanel(summary.__error, { onRetry: () => render(mount, ctx, helpers) }));
    return;
  }
  const studentCount = summary.student_count ?? 0;

  // 顶部指标卡
  const pendingCount = arrangements.__error ? null : (arrangements || []).filter((a) => ["pending", "in_progress"].includes(a.status)).length;
  const overdueCount = arrangements.__error ? null : (arrangements || []).filter((a) => a.status === "overdue").length;
  const dutyCount = dutyToday.__error ? null : (dutyToday || []).length;
  const todayLessons = daily.__error ? [] : (daily || []);

  mount.append(el("div", { class: "metric-grid" },
    metricCard("班级学生数", studentCount, `近 7 天事件 ${summary.events_last_7_days ?? 0} 条`),
    metricCard("今日课程", todayLessons.length, todayLessons.filter((l) => l.is_changed).length ? `${todayLessons.filter((l) => l.is_changed).length} 节临时变更` : "无临时变更"),
    metricCard("待办安排", pendingCount ?? "—", "待处理 + 进行中"),
    metricCard("逾期安排", overdueCount ?? "—", null, overdueCount > 0 ? "error" : ""),
    metricCard("今日值日", dutyCount ?? "—", dutyCount === 0 ? "今天没有值日安排" : null)));

  const grid = el("div", { style: { display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(320px, 1fr))", gap: "18px" } });

  // 今日课表
  const lessonCard = el("div", { class: "card", style: { margin: 0 } }, el("h3", {}, `今日课表 · ${WEEKDAY_NAMES[weekdayOf(today)]}`));
  if (daily.__error) lessonCard.append(errorPanel(daily.__error));
  else if (!todayLessons.length) lessonCard.append(el("p", { class: "muted" }, "今天没有课程安排"));
  else lessonCard.append(...todayLessons.map((lesson) => el("div", { class: `row-gap${lesson.is_changed ? " daily-changed" : ""}`, style: { padding: "6px 8px", borderRadius: "8px", justifyContent: "space-between" } },
    el("span", {}, el("b", {}, `${lesson.period_name || periodLabel(lesson.period_no)} `), lesson.is_cancelled ? el("s", {}, lesson.original_subject) : lesson.subject, lesson.teacher ? el("span", { class: "muted" }, ` ${lesson.teacher}`) : null),
    lesson.is_changed ? statusBadge("warn", lesson.is_cancelled ? `取消：${lesson.change_reason || ""}` : `变更：${lesson.change_reason || ""}`) : null)));
  lessonCard.append(el("button", { class: "text-button", type: "button", onclick: () => navigate("/timetable") }, "查看完整课表 →"));
  grid.append(lessonCard);

  // 今日值日
  const dutyCard = el("div", { class: "card", style: { margin: 0 } }, el("h3", {}, "今日值日"));
  if (dutyToday.__error) dutyCard.append(errorPanel(dutyToday.__error));
  else if (!dutyCount) dutyCard.append(el("p", { class: "muted" }, "今天没有值日任务"));
  else {
    const byId = new Map((await import("../state.js").then((m) => m.refreshStudents()).catch(() => [])).map((s) => [s.id, s]));
    dutyCard.append(...dutyToday.map((a) => el("div", { class: "row-gap", style: { justifyContent: "space-between", padding: "4px 0" } },
      el("span", {}, el("b", {}, a.item_name), el("span", { class: "muted" }, ` ${byId.get(a.student_id)?.name || a.student_id}`)),
      statusBadge(a.status))));
  }
  dutyCard.append(el("button", { class: "text-button", type: "button", onclick: () => navigate("/duty") }, "值日管理 →"));
  grid.append(dutyCard);

  // 临期安排
  const arrangeCard = el("div", { class: "card", style: { margin: 0 } }, el("h3", {}, "临期安排"));
  if (arrangements.__error) arrangeCard.append(errorPanel(arrangements.__error));
  else {
    const upcoming = (arrangements || []).filter((a) => a.status !== "completed" && a.status !== "cancelled").slice(0, 8);
    if (!upcoming.length) arrangeCard.append(el("p", { class: "muted" }, "没有待处理的安排"));
    else arrangeCard.append(...upcoming.map((a) => el("div", { class: "row-gap", style: { justifyContent: "space-between", padding: "4px 0" } },
      el("span", {}, statusBadge(a.priority), ` ${a.title}`),
      el("span", { class: "muted", style: { fontSize: "12px" } }, a.due_at ? `截止 ${fmtDateTime(a.due_at)}` : "无截止时间"))));
  }
  arrangeCard.append(el("button", { class: "text-button", type: "button", onclick: () => navigate("/arrangements") }, "全部安排 →"));
  grid.append(arrangeCard);

  mount.append(grid);
}
