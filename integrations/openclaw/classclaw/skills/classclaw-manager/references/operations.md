# Write operations

Use `classclaw_propose_write` with one supported `operation_type`. Payloads use the same field names as the ClassClaw REST schemas.

For user prose, WeChat messages, pasted lists, OCR, or attachments, do not construct these operations directly. Call `classclaw_analyze_interaction`; the backend asks OpenClaw for a structured plan, validates the payloads, and returns proposal records. Use the table below when reviewing validation failures or handling a deterministic guided operation.

| Operation | Payload shape |
|---|---|
| `student.create` | `{class_id, student_no, name, gender?, phone?, boarding_status?, group_no?, tags?, ...}` |
| `student.update` | `{class_id, student_no, changes:{...}}` |
| `student.update.batch` | `{class_id, student_nos:[班内学号,...], changes:{...}, only_if_empty?:[field,...]}`；同值变更按组，最多100人；仅补性别用 `only_if_empty:["gender"]` |
| `seating.update` | `{class_id, rows, cols, layout:[[student_no|null,...],...], change_note?}` |
| `attendance.set` | `{class_id, student_no, attendance_date, period, status, note?}` |
| `homework.create` | `{class_id, title, subject, description?, assigned_date, due_at?, status?}` |
| `homework.status.batch` | `{homework_id, items:[{student_no,status,submitted_at?,score?,comment?}]}` |
| `student_event.create` | `{class_id,student_no,event_type,subtype,event_date,content,sentiment?,severity?,...}` |
| `student_event.batch` | `{items:[student event payloads...]}` |
| `exam.create` | `{class_id,name,exam_date,subjects:[{subject,full_score}]}` |
| `score.batch` | `{exam_id,scores:[{student_no,subject,score,note?}]}` |
| `lesson_override.create` | `{class_id,lesson_date,period_no,replacement_subject?,replacement_teacher?,replacement_room?,status,reason}` |
| `arrangement.create` | `{class_id?,title,summary?,start_at?,due_at?,priority?,reminder_times?}`; at most 3 reminders, default once at target minus 3 hours |
| `duty.schedule.confirm` | Confirmed structured duty preview payload |
| `duty.assignment.score` | `{assignment_id,score:0..5,note?}`; assignment must come from current read context |
| `classroom.broadcast.send` | `{class_id, mode:"three_part"\|"custom", student_nos?:[班内学号,...], salutation?:称呼, time_phrase?:时间, predicate?:事项, text?:自定义完整原文, merge_mode?:"combined"\|"per_student", display_seconds?, repeat_count?, gap_seconds?, volume?}`；`three_part` 必须给 `student_nos` 和 `predicate`，`custom` 必须给 `text` 且不选人 |
| `classroom.volume.set` | `{class_id, volume?:0..100, mute?, restore_after_broadcast?}`；至少给 `volume` 或 `mute` 其中一项，上限由服务端配置决定 |

Students are always referenced by class-internal `student_no` (seating layouts included); the backend resolves them to internal UUIDs within the bound class. `homework_id`, `exam_id`, `assignment_id`, `proposal_id`, `attachment_id` and `broadcast_id` remain opaque handles returned by tools.

`classroom.broadcast.send` and `classroom.volume.set` drive physical devices, so their result has two layers. The backend freezes the exact sentences (default template `请{称谓}{时间}{谓词}。`, one sentence per student when `merge_mode="per_student"`) and registers a terminal command; the commit result reports registration and dispatch only. Display and speech outcomes come from the terminal receipt, and `expired` or `unknown` means the result is genuinely unknown — never claim the class heard it, and never re-send to “make sure”.

Dates are `YYYY-MM-DD`; datetimes are ISO 8601 with timezone. Use a stable idempotency key derived from the external message id plus the intended operation, for example `wechat-message-id:attendance:student-no:date:period`.

Evidence items use:

```json
{
  "source_type": "file",
  "source_id": "external-message-id",
  "attachment_id": "attachment-uuid",
  "location": "成绩表.xlsx / Sheet1 / row 8",
  "summary": "张三数学 92 分",
  "confidence": 0.98,
  "reasons": ["学生、日期、时段和考勤状态均已明确且唯一匹配"]
}
```

If a preview returns `ready=false`, do not call the commit tool.

After showing one preview, an explicit affirmative reply authorizes `classclaw_commit_write` directly. After showing several previews together, an affirmative covering all of them authorizes one atomic `classclaw_commit_writes` call. There is no separate OpenClaw approval card.

When a user says an unsubmitted homework item should not be linked to or create a homework record, represent it as `student_event.create` with `event_type="homework"`, `subtype="homework_missing"`, and content such as “语文作业未交”.

For student events, infer subtype/sentiment/severity from content. The following are always negative unless the user is explicitly negating that they happened: homework missing, forgotten materials, late arrival, absence, sleeping in class, noise, disruption, fighting, rule violations, and unfinished tasks. Neutral is reserved for factual communication or information without praise or a problem.
