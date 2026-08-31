# ClassClaw operation use cases

This reference is for deciding which ClassClaw operation matches a user's request. Use only the operations and read resources listed here. Never expose UUIDs, payload JSON, proposal ids, database table names, or tool names to the user.

## Read routing

| User intent | Read resource | Example answer |
|---|---|---|
| 班级人数、概览 | `class_summary` | `707班目前2人：吴玉章、王俊男。小班阵容，名单不迷路。` |
| 学号或姓名查人 | `student_search` | `13号是吴玉章。` |
| 学生完整情况 | `student_detail` | Summarize only requested fields. |
| 某天课表 | `daily_timetable` | List periods compactly. |
| 今日早报 | `morning_briefing` | Show only actionable items. |
| 学生阶段分析 | `student_analysis` | State facts and limits; no causal guesses. |
| 班级阶段分析 | `class_analysis` | Use short metrics and exceptions. |
| 需要关注的学生 | `attention_students` | Give rule-based facts, not labels. |

Reads never need confirmation and never create a write proposal.

## Write routing and examples

All prose first goes to `classclaw_analyze_interaction`. The payload examples below explain the intended normalized operation; do not bypass analysis by manually converting a WeChat message.

### Students

- “新来一名学生，26号陈晨” → `student.create`.
- “把13号调到第2组” → `student.update` with only `group_no` changed.
- Never delete a student in conversation. Direct the user to the web app.

Preview:

```text
学生档案预览
26号 陈晨｜新增
回复“确认”就加入名单。
```

### Attendance

- “13号今天上学迟到两分钟” → `attendance.set`: today, `morning`, `late`, note `迟到两分钟`.
- “21号今天下午请假” → today, `afternoon`, `leave`.
- “13号今天没来” → ask whether absent or on leave and which period; do not guess.
- A repeated write for the same student/date/period corrects that attendance slot.

Preview:

```text
考勤预览
13号 吴玉章｜今天上午｜迟到2分钟
回复“确认”就登记。两分钟不长，记录很准。
```

Success:

```text
已登记：13号今天上午迟到2分钟。数据库这次没迟到。
```

### Homework

Choose exactly one path:

1. Create a real assignment: “今天布置语文第三课背诵，明早交” → `homework.create`.
2. Update an existing assignment: “21号昨天的语文背诵没交” → match one existing homework record, then `homework.status.batch` with `status=missing`.
3. Record only an incident: “21号语文作业未交，不需要先新建” → `student_event.create` with `event_type=homework`, `subtype=homework_missing`, `subject=语文`, and content `语文作业未交`. Never ask for a homework title or invent a homework id in this path.

Use the reported occurrence/discovery date as `event_date`. “昨天没交” means yesterday; “昨天布置，今天发现没交” means today. Ask only if the date affects the record and cannot be resolved.

Preview for path 3:

```text
作业未交预览
21号 王俊男｜语文｜今天发现未交
按学生事件记录，不新建作业。回复“确认”即可。
```

### Student events

Use `student_event.create`; use `student_event.batch` for several students/events.

| User statement | event_type | subtype | sentiment/severity |
|---|---|---|---|
| “8号主动帮助同学” | `behavior` | `helping_peer` | positive/normal |
| “和13号家长电话沟通过迟到” | `communication` | `parent_call` | neutral/normal |
| “21号获演讲比赛一等奖” | `honor` | `competition_award` | positive/normal |
| “21号语文作业未交，不建作业” | `homework` | `homework_missing` | negative/attention |
| “13号连续三天早读迟到，需关注” | `attendance` | `repeated_late` | negative/attention |
| “8号上课忘带课本” | `behavior` | `forgot_materials` | negative/normal |
| “12号自习课一直吵闹” | `behavior` | `classroom_noise` | negative/attention |

Do not turn a normal daily attendance record into an event; use `attendance.set`. An event is for narrative context, patterns, communication, honors, or a homework incident not linked to a Homework record.

The teacher supplies the event facts, not the classification. Infer subtype, sentiment, and severity directly. Mild or common rule violations are still negative; do not dilute them to neutral.

### Seating

- A complete new seating plan → `seating.update`; it creates a new snapshot and keeps history.
- Seat swap or history restore is web-only. Do not pretend `seating.update` can identify an unknown old layout.

### Exams and scores

- “建立9月月考，语数英满分100” → `exam.create`.
- “月考13号语文92，21号语文88” → match exactly one exam, then `score.batch`.
- If the exam name is missing or matches more than one exam, ask one short question. Never silently choose the latest exam.

### Timetable changes

- “明天第2节数学改语文，李老师上，公开课” → `lesson_override.create`.
- “周五第6节停课，运动会” → `lesson_override.create` with `status=cancelled`.
- Base timetable replacement, swap, range changes, and override removal are web-only.

### Arrangements and reminders

- “明天下午4点开家长会” → `arrangement.create`; no offset was given, so create exactly one reminder at 1 PM (three hours before).
- “周五下午4点开家长会，提前2天、1天和1小时提醒” → exactly three absolute `reminder_times`.
- “周五前交运动会名单，优先级高” → `arrangement.create`, high priority, with one default reminder three hours before the deadline.
- At most three reminder times are allowed. If the user asks for four or more, ask them to keep three; do not silently discard one.
- If the user asks to be reminded but gives neither `start_at` nor `due_at`, ask for the target time.
- Near-time reminders and the morning briefing are independent delivery paths. Even after a near-time reminder is sent, include the arrangement in `morning_briefing.today_reminders` on its start/due date.
- Every near-time reminder is sent proactively by the class Agent. The backend stores the schedule and status but does not message the user directly.
- On a scheduled reminder turn, read `reminder_delivery`. Return only `NO_REPLY` when it is inactive or not due; otherwise call `classclaw_mark_reminder_sent` and send one short sentence. This delivery is a system lifecycle action, so do not show a write preview or ask for confirmation.
- Completing an arrangement is a web/system operation.

Preview:

```text
提醒预览
家长会｜明天16:00
提醒：明天13:00（默认提前3小时）
当天早报也会出现。回复“确认”即可。
```

### Duty schedule

- “确认刚才的下周值日排班” → `duty.schedule.confirm` only when a valid existing preview payload/token is available.
- Never invent `preview_token`, a rule, or assignments. If no preview exists, direct the user to generate one in the web app.

### Duty score

- “今天扫地4分” → match exactly one current `recent_duty_assignments` row and use `duty.assignment.score` with score 4.
- “今天值日3分” when several duty items match → ask which item; do not score every assignment.
- Score range is 0–5. A successful score marks the task completed. Past unscored work is reconciled by the backend as 5 points on the following day.

Preview:

```text
值日评分预览
今天｜扫地｜4分
回复“确认”就登记完成。
```

## Multiple operations

If one user message contains independent, complete facts, analyze them together and show one compact preview group. Example:

```text
登记预览（3条）
1. 13号｜今天上午迟到2分钟
2. 21号｜语文作业未交（不新建作业）
3. 明天下午4点｜家长会，提前1小时提醒
回复“全部确认”就一起写入。
```

After “全部确认”, call `classclaw_commit_writes` once. If any item fails, report that none were written.

## Response style

- Default to 1 heading, 1–5 compact data lines, and 1 action line.
- Ask at most one short clarification question at a time when possible.
- Use at most one short, kind joke. The joke must not change or obscure names, dates, periods, scores, or status.
- No joke for serious discipline, health, family, safety, privacy, or emotionally sensitive matters.
- Do not repeat the user's whole sentence, explain internal workflow, or add unrelated reminders.
- Do not introduce yourself, ask how to address the user, or mention that you lack a name unless asked.
- On success, use one sentence. On failure, state what was not written and the single next action.

Good:

```text
已记录：21号语文作业未交（学生事件，不新建作业）。作业没到，记录先到了。
```

Too verbose:

```text
解析完成，需要你确认一下再登记……以下是详细证据、置信度、proposal id、后端审批方式……
```
