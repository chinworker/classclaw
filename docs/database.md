# 数据库设计

主要表按领域分组：

- 班级与学生：`classes`、`system_settings`、`students`、`class_subjects`
- 座位：`seating_snapshots`，二维 JSON 保存完整布局，历史不可覆盖
- 值日：`duty_rules`、`duty_schedules`、`duty_assignments`、`duty_score_items`、`duty_evaluations`、`duty_evaluation_details`
- 教学：`homework`、`homework_student_statuses`、`student_events`、`attendance_records`、`exams`、`exam_subjects`、`scores`
- 课表：`class_periods`、`base_timetable`、`lesson_overrides`
- 工作流：`arrangements`、`reminders`、`attachments`、`attachment_links`、`interaction_analyses`、`audit_logs`、`write_proposals`、`class_onboarding_sessions`、`class_agent_bindings`

所有主键为 UUID 字符串。主要唯一约束包括：班级+学号、学生+日期+考勤时段、考试+学生+科目、班级+星期+节次、班级+`lesson_key`、外部消息 ID。学生和学生事件支持软删除；班级采用停用。历史记录通过学生 UUID 关联，不靠姓名。

SQLite 不保存附件 BLOB。`attachments.stored_path` 是相对附件父目录的路径，保存 SHA-256、大小和 MIME；`attachment_links` 以实体类型和实体 ID 建立通用关联。

`write_proposals` 保存原始和归一化 payload、面向用户的预览、revision、状态、申请/确认者、过期时间和执行结果，不复制保存消息证据。业务表只在 proposal 确认时写入。`class_onboarding_sessions` 保存网页引导草稿、文件字段置信度、警告、当前步骤和乐观并发 revision；完成后关联创建出的班级。`class_agent_bindings` 对 `class_id` 与 `openclaw_agent_id` 分别唯一，保存 workspace、channel/account、provisioning 状态和错误，不保存微信凭据或二维码。

`interaction_analyses` 只保存渠道、外部消息 ID、附件 IDs、上下文班级、OpenClaw agent、结构化输出、置信度、澄清问题、警告和 proposal IDs；不保存消息原文。`idempotency_key` 防止同一微信或网页事件重复分析；正式业务幂等仍由 proposal 和各领域唯一约束共同保证。

迁移入口为 `alembic upgrade head`。首版迁移从 SQLAlchemy metadata 创建完整 schema，后续版本应使用 Alembic revision 明确记录增量变化。
