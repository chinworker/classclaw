# 写入字段参考

仅在确定性结构化流程需要具体字段时查阅。自然语言或附件走分析，直接使用已返回的预览。

| Operation | Payload shape |
|---|---|
| `student.create` | `{class_id, student_no, name, gender?, phone?, boarding_status?, group_no?, tags?, ...}` |
| `student.update` | `{class_id, student_no, changes:{...}}` |
| `student.update.batch` | `{class_id, student_nos:[班内学号,...], changes:{...}, only_if_empty?:[field,...]}`；同值变更按组，最多100人；仅补性别用 `only_if_empty:["gender"]` |
| `seating.update` | `{class_id, rows, cols, layout:[[student_no|null,...],...], change_note?}` |
| `attendance.set` | `{class_id, student_no, attendance_date, status, note?}`; once per student/date |
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
| `memory.upsert` | `{class_id, entries:[{kind:"schedule"\|"preference"\|"fact",name,memory_id?,aliases?,content?,start_time?,end_time?,weekdays?,valid_from?,valid_to?}]}`; schedules require both times and weekdays (1–7); preferences and facts require content; temporary entries require both dates |
| `memory.forget` | `{class_id, memory_ids:[...]}`; use handles from confirmed class memory, never guess |
| `classroom.broadcast.send` | `{class_id, mode:"three_part"\|"custom", student_nos?:[班内学号,...], salutation?:称呼, time_phrase?:时间, predicate?:事项, text?:自定义完整原文, merge_mode?:"combined"\|"per_student", display_seconds?, repeat_count?, gap_seconds?, volume?}`；`three_part` 必须给 `student_nos` 和 `predicate`，`custom` 必须给 `text` 且不选人 |
| `classroom.volume.set` | `{class_id, volume?:0..100, mute?, restore_after_broadcast?}`；至少给 `volume` 或 `mute` 其中一项，上限由服务端配置决定 |


学生用班内学号；日期用 YYYY-MM-DD，时间用带时区 ISO 8601。考试、作业、值日安排、预览和记忆 ID 必须来自已知数据。幂等键用消息标识与操作的稳定组合。

学生事件枚举：`event_type=homework|attendance|behavior|communication|honor|other`，`sentiment=positive|neutral|negative`，`severity=normal|attention|serious`。校内玩牌等违规用 `behavior`；不编造处理结果。
