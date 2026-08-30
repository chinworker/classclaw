# Write operations

Use `classclaw_propose_write` with one supported `operation_type`. Payloads use the same field names as the ClassClaw REST schemas.

For user prose, WeChat messages, pasted lists, OCR, or attachments, do not construct these operations directly. Call `classclaw_analyze_interaction`; the backend asks OpenClaw for a structured plan, validates the payloads, and returns proposal records. Use the table below when reviewing validation failures or handling a deterministic guided operation.

| Operation | Payload shape |
|---|---|
| `student.create` | `{class_id, student_no, name, gender?, phone?, boarding_status?, group_no?, tags?, ...}` |
| `student.update` | `{student_id, changes:{...}}` |
| `seating.update` | `{class_id, rows, cols, layout, change_note?}` |
| `attendance.set` | `{class_id, student_id, attendance_date, period, status, note?}` |
| `homework.create` | `{class_id, title, subject, description?, assigned_date, due_at?, status?}` |
| `homework.status.batch` | `{homework_id, items:[{student_id,status,submitted_at?,score?,comment?}]}` |
| `student_event.create` | `{class_id,student_id,event_type,subtype,event_date,content,sentiment?,severity?,...}` |
| `student_event.batch` | `{items:[student event payloads...]}` |
| `exam.create` | `{class_id,name,exam_date,subjects:[{subject,full_score}]}` |
| `score.batch` | `{exam_id,scores:[{student_id,subject,score,note?}]}` |
| `lesson_override.create` | `{class_id,lesson_date,period_no,replacement_subject?,replacement_teacher?,replacement_room?,status,reason}` |
| `arrangement.create` | `{class_id?,title,summary?,start_at?,due_at?,priority?,reminder_times?}`; at most 3 reminders, default once at target minus 3 hours |
| `duty.schedule.confirm` | Confirmed structured duty preview payload |

Dates are `YYYY-MM-DD`; datetimes are ISO 8601 with timezone. Use a stable idempotency key derived from the external message id plus the intended operation, for example `wechat-message-id:attendance:student-id:date:period`.

Evidence items use:

```json
{
  "source_type": "file",
  "source_id": "external-message-id",
  "attachment_id": "attachment-uuid",
  "location": "成绩表.xlsx / Sheet1 / row 8",
  "summary": "张三数学 92 分",
  "confidence": 0.98
}
```

If a preview returns `ready=false`, do not call the commit tool.

After showing one preview, an explicit affirmative reply authorizes `classclaw_commit_write` directly. After showing several previews together, an affirmative covering all of them authorizes one atomic `classclaw_commit_writes` call. There is no separate OpenClaw approval card.

When a user says an unsubmitted homework item should not be linked to or create a homework record, represent it as `student_event.create` with `event_type="homework"`, `subtype="homework_missing"`, and content such as “语文作业未交”.
