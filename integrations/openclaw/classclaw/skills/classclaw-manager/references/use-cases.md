# ClassClaw 场景参考

仅在不确定资源或行为时查阅；预览、确认和失败处理以 Skill 为准。

| 要做什么 | 读资源 |
| --- | --- |
| 班级上下文、班级概览 | `classes`、`class_summary` |
| 查学生、查学生档案 | `student_search`、`student_detail`；优先班内学号 |
| 某日课表、今日早报 | `daily_timetable`、`morning_briefing`；早报保留 `today_reminders`，昨日值日读 `yesterday_duty_assignment_scores` |
| 学生、班级分析或需关注学生 | `student_analysis`、`class_analysis`、`attention_students`；只陈述事实，不推断因果 |
| 已确认的作息、偏好或约定 | `agent_memory`；查时段传目标 `date` 和名称 `q` |
| 教室终端、摄像头连通性 | `classroom_status`、`classroom_camera`；这两项不采集画面 |
| 现场概况、查询刚才的播报结果 | `classroom_observation`（明确请求才读一次）、`classroom_broadcast`（用返回的播报 ID） |
| 查看已有预览、已有分析 | `write_proposal`、`interaction_analysis`；使用返回的对应 ID |
| 定时提醒核验 | `reminder_delivery`；仅有效且到期才标记并发送 |

常见写入边界：

- **作业**：已指明唯一作业的未交，更新该作业状态；老师明确不新建或不关联作业时，仅记未交事件。不要编造作业。
- **值日**：0–5 分必须对应一个实际安排；排班确认必须有现成的有效排班预览。评分与安排是两件事。
- **记忆**：临时调整另存起止日期，长期规则到期恢复。纠正或忘记已有记忆要展示具体变更，经确认才写。
- **教室广播**：“通知某人下课后去办公室”现在播报；“下课后再播这句”是定时播报，目前不支持。完整原句原样播。离线不补播；未收到回执不能说已经显示或听到。
- **现场概况**：仅描述单帧可见信息和限制，不推断身份、注意力、音频或考勤；直播和设备配置去网页。
- **提醒**：有开始或截止时，没指定偏移默认提前三小时一次，最多三次；提醒送达不影响当天早报。
