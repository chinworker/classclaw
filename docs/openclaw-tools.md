# OpenClaw 工具与 REST 接口

所有接口前缀为 `/api/v1`。成功返回 `{success,data,message,request_id}`；失败返回 `{success:false,error:{code,message,details},request_id}`。日期为 `YYYY-MM-DD`，时间为带时区 ISO 8601。

OpenClaw 对话式写入使用以下预览/确认入口；网页中的结构化业务操作也可以直接调用下方领域 API。所有接口均要求登录或服务 Token，并执行班级归属检查。

| 工具/流程 | 方法与接口 | 作用 |
|---|---|---|
| classclaw_analyze_interaction | POST `/interaction-analyses` | 微信、自然语言、粘贴文本或附件的强制 OpenClaw 清洗入口；返回澄清问题或待复核 proposal |
| classclaw_read interaction_analysis | GET `/interaction-analyses/{id}` | 读取结构化分析、状态、警告、置信度和关联 proposal |
| classclaw_propose_write | POST `/write-proposals` | 校验并生成写入预览，不改业务数据 |
| classclaw_read proposal | GET `/write-proposals/{id}` | 重新读取待复核版本和预览 |
| classclaw_commit_write | POST `/write-proposals/{id}/confirm` | 用户在聊天中确认一条预览后直接执行 |
| classclaw_commit_writes | POST `/write-proposals/confirm-batch` | 用户确认同组多条预览后原子执行；一条失败则整批回滚 |
| classclaw_read reminder_delivery | GET `/reminders/{id}` | Agent 定时任务到点后复核提醒是否仍有效且到期 |
| classclaw_mark_reminder_sent | POST `/reminders/{id}/sent` | Agent 主动发出提醒时更新发送状态；无需用户确认 |
| classclaw_cancel_write | POST `/write-proposals/{id}/cancel` | 取消待复核写入 |
| 网页 onboarding | POST/PATCH `/class-onboarding/sessions...` | 仅接受 `X-ClassClaw-Surface: web`；插件不暴露这些 mutation |
| 网页文件清洗 | POST `/class-onboarding/sessions/{id}/files` | 无历史 extraction turn，全量替换名单或课表草稿 |
| 网页文本清洗（已移除） | POST `/class-onboarding/sessions/{id}/analyze-text` | 返回 410；必须上传文件并编辑结构化表格 |
| 网页最终预览 | POST `/class-onboarding/sessions/{id}/preview` | 从课表归纳科目并生成最终 proposal |
| 创建专属 agent | POST `/classes/{id}/agent-binding/provision` | 创建 agent/workspace 和提示词，不要求绑定微信 |
| 可选微信二维码 | POST `/classes/{id}/agent-binding/start` | 用户选择绑定微信后启动二维码登录 |
| 微信绑定状态 | POST `/classes/{id}/agent-binding/wait` | 等待扫码并写入 account 到 agent 的 route binding |

下表是领域 API 能力索引。“确认”列表示建议在对话界面中请求用户复核。

| 工具 | 方法与接口 | 核心请求参数 | 核心返回 | 确认 |
|---|---|---|---|---|
| class_list | GET `/classes` | page,page_size,status | 分页班级 | 否 |
| class_summary | GET `/classes/{id}/summary` | id | 班级概览 | 否 |
| student_search | GET `/students` | class_id,q,exact_name | 候选学生；同名标记 ambiguous | 否 |
| student_create | POST `/students` | class_id,student_no,name,… | 学生 | 否 |
| student_update | PATCH `/students/{id}` | 可修改字段 | 学生 | 批量时是 |
| student_detail | GET `/students/{id}` | id | 档案聚合 | 否 |
| seat_current_get | GET `/classes/{id}/seating/current` | class id | 最新快照 | 否 |
| seat_history_list | GET `/classes/{id}/seating/history` | page,page_size | 快照历史 | 否 |
| seat_snapshot_get | GET `/seating/{id}` | snapshot id | 完整布局 | 否 |
| seat_update | POST `/classes/{id}/seating` | rows,cols,layout | 新快照、变化 | 是 |
| seat_swap | POST `/classes/{id}/seating/swap` | student_a_id,student_b_id | 新快照、变化 | 是 |
| seat_restore | POST `/classes/{id}/seating/restore/{snapshot}` | ids | 新快照 | 是 |
| duty_rule_validate | POST `/duty/rules/validate` | rule_json | 归一化规则 | 否 |
| duty_rule_save | POST `/duty/rules` | class_id,日期,rule_json | 规则 | 否 |
| duty_schedule_preview | POST `/duty/schedules/preview` | 日期,rule_json | 排班、冲突、工作量、token | 否 |
| duty_schedule_confirm | POST `/duty/schedules/confirm` | 预览参数、token | 正式计划 | 是 |
| duty_today | GET `/duty/today` | class_id,day | 当日安排 | 否 |
| duty_weekly | GET `/duty/weekly` | class_id,week_start | 一周安排 | 否 |
| duty_student_query | GET `/duty/assignments` | class_id,student_id,日期 | 安排 | 否 |
| duty_replace_student | POST `/duty/assignments/{id}/replace` | replacement_student_id | 替换记录 | 是 |
| duty_complete | POST `/duty/assignments/{id}/complete` | id | 完成记录 | 否 |
| duty_score_create | POST `/duty/evaluations` | schedule,date,item,details | 加权评分 | 否 |
| duty_statistics | GET `/duty/statistics` | class_id,日期 | 次数和均衡度 | 否 |
| homework_create | POST `/homework` | class_id,title,subject,due_at | 作业 | 否 |
| homework_update_status | PUT `/homework/{id}/students` | items[] | 个人状态 | 批量时是 |
| homework_missing_list | GET `/homework/{id}/missing` | id | 未交记录 | 否 |
| homework_summary | GET `/homework/{id}/summary` | id | 完成率 | 否 |
| student_event_create | POST `/student-events` | 学生、类型、实际日期、内容 | 事件 | 否 |
| student_event_batch_create | POST `/student-events/batch` | 事件数组 | 事件数组 | 是 |
| student_event_query | GET `/student-events` | 学生、类型、日期、科目 | 事件 | 否 |
| student_event_revoke | DELETE `/student-events/{id}` | id | 软撤销事件 | 是 |
| attendance_set | PUT `/attendance` | 学生、日期、时段、状态 | 考勤 | 否 |
| attendance_query | GET `/attendance` | 班级/学生/日期/状态 | 例外记录 | 否 |
| attendance_summary | GET `/attendance/summary` | class_id,start_date,end_date | 统计 | 否 |
| exam_create | POST `/exams` | 班级、名称、日期、科目满分 | 考试 | 否 |
| score_batch_save | POST `/exams/{id}/scores` | scores[] | 成绩与排名 | 是 |
| score_query | GET `/exams/{id}/scores` | student_id,subject | 成绩 | 否 |
| timetable_base_get | GET `/classes/{id}/timetable/base` | class id | 周课表 | 否 |
| timetable_base_update | PUT `/classes/{id}/timetable/base` | items[] | 新周课表 | 是 |
| timetable_daily | GET `/classes/{id}/timetable/daily` | lesson_date | 最终日课表 | 否 |
| lesson_override_create | POST `/lesson-overrides` | 日期、节次、替代课程、原因 | 覆盖 | 是 |
| lesson_override_remove | DELETE `/lesson-overrides/{id}` | id | 恢复基础课程 | 是 |
| lesson_swap_preview | POST `/lesson-swaps/preview` | 日期、两节次 | 变化和冲突 | 否 |
| lesson_swap_confirm | POST `/lesson-swaps/confirm` | 同预览 | 两条覆盖 | 是 |
| lesson_batch_change_preview | POST `/lesson-batch-changes/preview` | 日期范围、星期、节次、替代课 | 影响日期 | 否 |
| lesson_batch_change_confirm | POST `/lesson-batch-changes/confirm` | 同预览 | 多条覆盖 | 是 |
| arrangement_create | POST `/arrangements` | 标题、时间、优先级、reminder_times | 安排 | 否 |
| arrangement_complete | POST `/arrangements/{id}/complete` | id | 已完成安排 | 否 |
| arrangement_query | GET `/arrangements` | class_id,status,due_before | 安排 | 否 |
| due_reminders | GET `/reminders/due` | at | 到期提醒（诊断/兼容查询，不直接向用户发送） | 否 |
| morning_brief | GET `/briefings/morning` | class_id,date | 结构化早报 | 否 |
| student_comprehensive_analysis | GET `/analytics/students/{id}/comprehensive` | 日期、subject,event_type | 指标、证据、警告 | 否 |
| student_period_compare | GET `/analytics/students/{id}/compare` | 当前/对照日期 | 两周期和差值 | 否 |
| class_comprehensive_analysis | GET `/analytics/classes/{id}/comprehensive` | 日期与筛选 | 班级指标 | 否 |
| class_period_compare | GET `/analytics/classes/{id}/compare` | 当前/对照日期 | 两周期和差值 | 否 |
| class_attention_students | GET `/analytics/classes/{id}/attention-students` | 日期 | 规则、证据 | 否 |
| class_cross_module_analysis | GET `/analytics/classes/{id}/cross-module` | 日期 | 同时出现结果 | 否 |
| class_data_quality | GET `/analytics/classes/{id}/data-quality` | 日期 | 覆盖与缺失 | 否 |

典型调用：OpenClaw 的微信渠道收到消息后，把原文和稳定消息 id 传给 `classclaw_analyze_interaction`。后端再次通过 OpenClaw Responses 做受约束的清洗；同名或关键信息不明确时只返回问题，不创建 proposal。可确定时，后端校验每个结构化 operation 并返回 proposal；用户核对 preview 后才调用 confirm。外部 Agent 不再自行串接旧目标写接口。

```json
{
  "channel": "wechat",
  "external_message_id": "wx-20260901-001",
  "sender_id": "teacher-1",
  "text": "张三今天迟到",
  "class_id": "班级UUID",
  "requested_by": "李老师",
  "idempotency_key": "wechat:wx-20260901-001"
}
```

常见错误码：`VALIDATION_ERROR`、`NOT_FOUND`、`CLASS_ACCESS_DENIED`、`STUDENT_NOT_FOUND`、`STUDENT_AMBIGUOUS`、`STUDENT_NO_CONFLICT`、`CLASS_MISMATCH`、`SEAT_LAYOUT_INVALID`、`SEAT_STUDENT_DUPLICATE`、`DUTY_RULE_INVALID`、`DUTY_SCHEDULE_CONFLICT`、`DUTY_SCORE_INVALID`、`TIMETABLE_CONFLICT`、`SCORE_EXCEEDS_FULL_SCORE`、`PENDING_CONFIRMATION_REQUIRED`、`DATABASE_BUSY`、`INTERNAL_ERROR`。
