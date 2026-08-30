你是一名资深Python后端工程师、数据库设计师和AI Agent系统架构师。请根据我提供的《班主任工作台复刻提示词-完整版.md》作为业务参考，实现一个供OpenClaw智能体和未来网页端共同使用的“班级管理系统后端”。

# 一、项目目标

系统主要通过OpenClaw进行自然语言交互。

用户可以向OpenClaw发送：

-   文字消息
-   图片
-   成绩单
-   课表
-   学校工作文件
-   学生表现记录
-   作业信息
-   调课通知
-   值日要求
-   日常安排

OpenClaw负责：

1.  理解用户意图。
2.  从文字、图片和文件中提取结构化信息。
3.  在信息不明确时向用户追问。
4.  调用本系统提供的后端接口。
5.  综合多类数据进行学生或班级分析。
6.  将后端返回的结构化结果整理成自然语言。
7.  通过定时任务发送每日早报和临期提醒。

本后端负责：

1.  业务规则校验。
2.  SQLite数据存储。
3.  附件保存。
4.  数据查询和统计。
5.  跨模块综合分析。
6.  重复消息检查。
7.  待确认操作。
8.  操作审计和撤销。
9.  为未来网页端提供REST API。

本次只实现后端，不实现网页界面，但所有功能必须通过稳定的REST API开放，供未来网页端直接复用。

# 二、技术要求

使用：

-   Python 3.12
-   FastAPI
-   SQLAlchemy 2.x
-   Pydantic 2.x
-   SQLite
-   Alembic
-   pytest
-   Uvicorn

运行环境是配置较低的服务器，因此要求：

-   不使用Docker。
-   不使用Redis。
-   不使用Celery。
-   不使用消息队列。
-   不使用向量数据库。
-   不使用微服务。
-   不在服务器本地运行大模型。
-   不实现复杂权限系统。
-   不实现前端页面。
-   默认单用户、单教师使用。
-   数据模型支持多个班级。
-   附件直接保存到本地文件目录。
-   SQLite只保存附件路径和元数据，不保存大文件BLOB。
-   默认时区为`Asia/Shanghai`。
-   使用单个Uvicorn worker运行。
-   配置应适合低内存、低并发环境。

不要让OpenClaw直接执行SQL。

# 三、参考文件使用规则

请先完整阅读我提供的：

```
班主任工作台复刻提示词-完整版.md
```

从中提取以下业务模块的字段和规则：

-   班级管理
-   学生档案
-   座位表
-   值日管理
-   作业管理
-   日常表现
-   考勤管理
-   成绩管理
-   班级课表
-   调课管理
-   日常安排
-   班级统计

参考文件原本描述的是一个单文件前端应用。以下要求不适用于本项目，必须忽略：

-   单文件HTML
-   localStorage
-   ES5
-   前端主题
-   页面样式
-   前端组件
-   浏览器数据存储
-   27个前端页面
-   演示界面
-   前端导出图片逻辑

如果参考文件与本Prompt冲突，以本Prompt为准。

# 四、系统架构

采用清晰的分层架构：

```
OpenClaw / 未来网页端
        ↓
FastAPI路由层
        ↓
Pydantic Schema层
        ↓
Service业务层
        ↓
Repository数据访问层
        ↓
SQLAlchemy ORM
        ↓
SQLite
```

各层职责如下。

## 1. API层

负责：

-   HTTP请求
-   参数解析
    -分页、筛选和排序
-   统一响应格式
-   错误码
-   OpenAPI文档

API路由中不要直接编写复杂SQL。

## 2. Schema层

使用Pydantic定义：

-   新增结构
-   修改结构
-   查询结构
-   返回结构
-   批量操作结构
-   Agent工具调用结构
-   预览与确认结构

## 3. Service层

负责：

-   业务校验
-   事务
-   跨表写入
-   调课
-   值日排班
-   座位快照
-   统计分析
-   综合分析
-   早报生成
-   撤销操作

## 4. Repository层

封装数据库访问，避免业务层散落重复查询。

## 5. Agent接口层

为OpenClaw提供颗粒度明确的工具接口。

禁止提供：

-   执行任意SQL
-   执行任意Python
-   任意删除数据库记录
-   绕过业务校验的通用修改接口

# 五、通用数据规则

所有主要业务表应包含适当的：

-   id
-   created_at
-   updated_at
-   deleted_at，可选
-   状态字段
-   外键
-   唯一约束
-   查询索引

主键可以统一使用UUID字符串。

所有日期使用：

```
YYYY-MM-DD
```

所有日期时间使用带时区的ISO 8601格式。

系统必须区分：

-   事情实际发生时间
-   用户发送消息时间
-   系统写入时间

所有跨表写操作必须使用数据库事务。

# 六、班级管理

班级表至少包含：

-   id
-   name
-   grade
-   room
-   head_teacher
-   semester_name
-   semester_start
-   semester_end
-   status
-   created_at
-   updated_at
-   deleted_at，可空

支持：

-   新增班级
-   修改班级
-   查询班级
-   班级列表
-   设置当前班级
-   停用班级
-   查询班级概览

删除班级时不能静默删除学生及历史数据。第一版采用停用或软删除。

# 七、学生档案

学生表至少包含：

-   id
-   class_id
-   student_no
-   name
-   gender
-   phone
-   boarding_status
-   duty_role
-   group_no
-   tags
-   family_status
-   father_name
-   father_phone
-   father_note
-   mother_name
-   mother_phone
-   mother_note
-   enrollment_date
-   status
-   notes
-   created_at
-   updated_at
-   deleted_at，可空

业务规则：

-   同一班级内学号唯一。
-   允许学生重名。
-   所有关联数据通过student_id关联。
-   不允许仅通过姓名确定学生。
-   如果姓名查询到多名学生，必须返回候选列表。
-   不允许OpenClaw自动选择重名学生。
-   支持按姓名、学号、班级、标签和状态搜索。
-   删除学生采用软删除。
-   历史记录必须继续可查询和审计。

学生详情接口应聚合返回：

-   基础档案
-   当前座位
-   最近值日
-   作业情况
-   考勤情况
-   日常表现
-   成绩情况
-   附件摘要
-   最近需要关注的问题

# 八、班级座位表

座位表采用“按修改时间保存完整快照”的简单设计。

不需要：

-   座位方案名称
-   版本号
-   位置表
-   座位分配表
-   desk_size
-   桌型
-   过道实体
-   考试座位方案
-   临时座位方案

只使用一张座位快照表。

## 1. 座位快照表

`seating_snapshots`至少包含：

-   id
-   class_id
-   snapshot_at
-   rows
-   cols
-   layout_json
-   change_note，可空
-   created_at

其中`layout_json`使用二维数组保存完整座位表。

示例：

```
[
  ["student_id_01", "student_id_02", null, "student_id_03"],
  ["student_id_04", "student_id_05", "student_id_06", null],
  ["student_id_07", null, "student_id_08", "student_id_09"]
]
```

数组中的：

-   行索引表示座位行。
-   列索引表示座位列。
-   学生ID表示该位置的学生。
-   `null`表示空座位。

不再单独创建座位位置和座位分配记录。

## 2. 座位历史规则

-   每次调整座位都保存一条新的完整快照。
-   不直接覆盖历史快照。
-   不需要版本号。
-   使用`snapshot_at`标识不同历史座位表。
-   当前座位表为`snapshot_at`最新的一条记录。
-   历史座位表按时间倒序查询。
-   恢复历史座位表时，复制该历史布局并创建一条新的当前快照。
-   旧快照保持不变。
-   两名学生交换座位时，同样创建一条新的完整快照。
-   移出或加入学生时创建新快照。

## 3. 座位校验

后端必须校验：

-   `layout_json`行数与`rows`一致。
-   每一行列数与`cols`一致。
-   同一个快照中一名学生只能出现一次。
-   学生必须属于对应班级。
-   已软删除或已转出的学生不能加入新座位表。
-   允许空座位。
-   历史快照中的已转出学生仍然保留，不能破坏历史。

## 4. 座位接口

至少提供：

```
GET  /api/v1/classes/{class_id}/seating/current
GET  /api/v1/classes/{class_id}/seating/history
GET  /api/v1/seating/{snapshot_id}
POST /api/v1/classes/{class_id}/seating
POST /api/v1/classes/{class_id}/seating/swap
POST /api/v1/classes/{class_id}/seating/restore/{snapshot_id}
```

修改后返回：

-   新快照ID
-   修改时间
-   完整layout
-   与上一快照相比的位置变化

# 九、值日管理

值日管理必须支持：

-   自然语言描述排班逻辑
-   结构化排班规则
-   排班预览
-   用户确认
-   正式值日安排
-   临时替换
-   完成登记
-   值日评分
-   值日统计

## 1. 自然语言排班

用户可以向OpenClaw发送：

```
从下周开始四个小组每组轮流值日一周。
卫生区每天4个人，讲台每天1个人。
张三和李四不要安排在同一天。
王五只安排星期一和星期三。
住宿生优先负责晚自修。
每名学生一个月值日次数尽量接近。
```

OpenClaw负责将自然语言转换为结构化JSON，后端负责校验和执行。

不允许将自然语言直接作为：

-   Python代码
-   SQL
-   可执行表达式

## 2. 值日规则

`duty_rules`至少包含：

-   id
-   class_id
-   name
-   original_text
-   rule_json
-   effective_from
-   effective_to
-   status：draft、active、disabled
-   created_at
-   updated_at

`rule_json`建议支持：

-   排班日期范围
-   工作日
-   值日项目
-   每个项目人数
-   按小组轮换
-   按学生轮换
-   固定学生
-   排除学生
-   不允许同时安排的学生
-   学生可安排星期
-   住宿生和走读生偏好
-   每名学生最大次数
-   每名学生最小次数
-   工作量均衡
-   缺席替补
-   节假日跳过

## 3. 排班流程

必须采用：

```
用户自然语言
→ OpenClaw生成rule_json
→ 后端校验
→ 后端生成预览
→ 返回冲突和工作量统计
→ 用户确认
→ 正式保存值日安排
```

预览不能写入正式值日表。

如果规则无法满足，返回：

-   冲突规则
-   涉及学生
-   涉及日期
-   缺少人数
-   建议放宽的约束
-   尽可能接近的排班结果，可空

## 4. 值日安排

`duty_schedules`至少包含：

-   id
-   class_id
-   rule_id，可空
-   name
-   start_date
-   end_date
-   status：draft、active、completed、cancelled
-   created_at
-   confirmed_at，可空

`duty_assignments`至少包含：

-   id
-   duty_schedule_id
-   duty_date
-   item_name
-   area，可空
-   student_id
-   group_name，可空
-   status：pending、completed、absent、replaced
-   replacement_for_assignment_id，可空
-   note，可空
-   completed_at，可空

支持：

-   按日查询
-   按周查询
-   按学生查询
-   临时替换
-   标记完成
-   查询空缺项目
-   查询未安排学生
-   统计每名学生的值日次数
-   判断排班是否均衡

## 5. 值日评分项目

`duty_score_items`至少包含：

-   id
-   class_id
-   name
-   max_score
-   weight
-   enabled
-   sort_order

评分项目示例：

-   地面卫生
-   桌椅整理
-   黑板清洁
-   垃圾处理
-   卫生工具摆放

## 6. 值日评价

`duty_evaluations`至少包含：

-   id
-   duty_schedule_id
-   duty_date
-   item_name
-   total_score
-   max_score
-   evaluator
-   comment
-   created_at
-   updated_at

`duty_evaluation_details`至少包含：

-   id
-   evaluation_id
-   score_item_id
-   score
-   comment，可空

业务规则：

-   得分不能小于0。
-   得分不能超过评分项目满分。
-   总分由后端按照权重计算。
-   支持按日期、周次、小组和学生统计。
-   支持平均分、总分、排名和常见扣分原因统计。
-   修改评分必须记录审计日志。
-   早报可以包含前一天值日评分。

# 十、作业管理

作业表至少包含：

-   id
-   class_id
-   title
-   subject
-   description
-   assigned_date
-   due_at
-   status：draft、published、closed、cancelled
-   source_message_id，可空
-   created_at
-   updated_at

学生作业状态至少包含：

-   id
-   homework_id
-   student_id
-   status：pending、submitted、late、missing、exempt、revision_required、revised
-   submitted_at，可空
-   checked_at，可空
-   score，可空
-   level，可空
-   comment，可空
-   attachment_id，可空
-   created_at
-   updated_at

业务规则：

-   新建作业后不立即为全班生成大量pending记录。
-   没有个人记录时默认视为pending。
-   支持批量更新提交状态。
-   学生必须属于作业对应班级。
-   截止后提交自动标记为late。
-   支持未交、迟交、需订正名单查询。
-   支持按班级、日期、科目和状态查询。
-   支持计算完成率。
-   将未交作业同步为日常表现时必须保证幂等，不能重复生成。

# 十一、学生日常表现

将作业、考勤、课堂行为等统一设计为学生事件。

`student_events`至少包含：

-   id
-   class_id
-   student_id
-   event_type：homework、attendance、behavior、communication、honor、other
-   subtype
-   event_date
-   event_time，可空
-   subject，可空
-   content
-   sentiment：positive、neutral、negative
-   severity：normal、attention、serious
-   score_delta，可空
-   source_type：wechat_text、wechat_image、file、web、system
-   source_message_id，可空
-   attachment_id，可空
-   created_at
-   updated_at
-   deleted_at，可空

必须区分：

-   `event_date`：事情实际发生时间
-   `created_at`：系统登记时间

支持：

-   单条登记
-   一条消息登记多名学生
-   批量登记
-   按学生查询
-   按类型查询
-   按日期范围查询
-   按科目查询
-   撤销错误登记
-   学生表现统计
-   班级表现统计

# 十二、考勤管理

`attendance_records`至少包含：

-   id
-   class_id
-   student_id
-   attendance_date
-   period：full_day、morning、afternoon、recess、care_1、care_2
-   status：present、late、absent、leave
-   note，可空
-   source_message_id，可空
-   created_at
-   updated_at

业务规则：

-   同一学生、日期和时段只能有一条有效记录。
-   没有记录时默认视为出勤。
-   不要自动给全班全部时段创建出勤记录。
-   大课间不计入全天出勤率。
-   分时段统计不能造成出勤率超过100%。
-   支持日、周、月统计。
-   支持按学生查询迟到、缺勤和请假次数。

# 十三、成绩管理

## 1. 考试

`exams`至少包含：

-   id
-   class_id
-   name
-   exam_date
-   status
-   created_at
-   updated_at

`exam_subjects`至少包含：

-   id
-   exam_id
-   subject
-   full_score

## 2. 成绩

`scores`至少包含：

-   id
-   exam_id
-   student_id
-   subject
-   score
-   full_score
-   class_rank，可空
-   note，可空
-   created_at
-   updated_at

业务规则：

-   分数不能小于0。
-   分数不能超过满分。
-   同一考试、学生和科目只能有一条成绩。
-   批量成绩录入必须使用事务。
-   一条数据不合法时整批回滚。
-   排名、平均分、最高分、最低分和及格率由后端计算。
-   支持学生成绩趋势。
-   支持不同时间段成绩变化分析。

# 十四、班级课表

课表采用：

```
基础周课表 + 具体日期课程ID + 临时覆盖记录
```

不需要：

-   课程开始时间
-   课程结束时间
-   复杂生效期
-   复杂移动动作
-   变更来源字段
-   source_message_id
-   source_type

## 1. 节次

`class_periods`至少包含：

-   id
-   class_id
-   period_no
-   name
-   sort_order
-   enabled

同一班级内`period_no`唯一。

## 2. 基础课表

`base_timetable`至少包含：

-   id
-   class_id
-   weekday
-   period_no
-   subject
-   teacher，可空
-   room，可空
-   updated_at

同一班级、星期和节次只能有一条基础课表记录。

## 3. 课程实例标识

查询具体日期时，根据日期和节次生成：

```
YYYY-MM-DD-PNN
```

例如：

```
2026-09-07-P01
2026-09-07-P02
```

数据库唯一性使用：

```
class_id + lesson_key
```

每日课表返回：

-   lesson_key
-   class_id
-   lesson_date
-   weekday
-   period_no
-   period_name
-   subject
-   teacher
-   room
-   is_changed
-   is_cancelled
-   change_reason

不需要提前为整个学期创建课程实例。

## 4. 临时课程变更

`lesson_overrides`至少包含：

-   id
-   class_id
-   lesson_key
-   lesson_date
-   period_no
-   original_subject
-   original_teacher，可空
-   replacement_subject，可空
-   replacement_teacher，可空
-   replacement_room，可空
-   status：normal、cancelled
-   reason
-   created_at
-   updated_at

课程变更中不记录任何source相关字段。

唯一约束：

```
class_id + lesson_key
```

查询某日课表时：

1.  根据日期取得星期。
2.  查询基础周课表。
3.  为每个节次生成lesson_key。
4.  查询对应临时变更。
5.  用临时变更直接覆盖基础课程。
6.  返回最终结果。

## 5. 两节课互换

两节课互换只生成两条覆盖记录。

例如：

```
2026-09-07-P02 数学
2026-09-07-P04 体育
```

互换后：

```
2026-09-07-P02 → 体育
2026-09-07-P04 → 数学
```

两条记录必须在同一事务中写入，任意一条失败时全部回滚。

## 6. 长期调课

长期调课也不修改基础课表，而是按日期范围展开成多条具体覆盖记录。

例如：

```
从9月1日至9月30日，每周一第3节数学改成体育
```

展开为：

```
2026-09-07-P03
2026-09-14-P03
2026-09-21-P03
2026-09-28-P03
```

保存前必须提供预览：

-   影响日期
-   lesson_key
-   原课程
-   新课程
-   冲突
-   总数量

用户确认后再批量写入。

## 7. 取消课程

取消课程时创建：

```
{
  "status": "cancelled",
  "reason": "学校活动停课"
}
```

查询结果仍返回该节次，但标记为已取消。

## 8. 撤销变更

撤销覆盖记录后，系统自动恢复显示基础课表。

所有变更和撤销应记录通用审计日志，但`lesson_overrides`表本身不保存source相关字段。

# 十五、日常安排与提醒

`arrangements`至少包含：

-   id
-   class_id，可空
-   title
-   summary
-   start_at，可空
-   due_at，可空
-   priority：high、medium、low
-   status：pending、in_progress、completed、cancelled、overdue
-   source_type，可空
-   source_message_id，可空
-   attachment_id，可空
-   created_at
-   updated_at
-   completed_at，可空

`reminders`至少包含：

-   id
-   arrangement_id
-   remind_at
-   sent_at，可空
-   status
-   retry_count
-   last_error，可空

业务规则：

-   一项安排支持多个提醒。
-   已完成和已取消事项不再提醒。
-   支持查询已经到期但尚未发送的提醒。
-   支持标记发送成功或失败。
-   后端负责计算逾期状态。
-   OpenClaw负责定时调用和消息发送。

# 十六、附件管理

附件保存到本地目录，例如：

```
data/attachments/2026/09/
```

`attachments`至少包含：

-   id
-   original_name
-   stored_name
-   stored_path
-   mime_type
-   file_size
-   sha256
-   source_message_id，可空
-   description，可空
-   created_at

使用通用关联表`attachment_links`：

-   id
-   attachment_id
-   entity_type
-   entity_id
-   created_at

要求：

-   不使用SQLite BLOB保存大文件。
-   文件名必须避免冲突。
-   计算SHA-256。
-   校验文件大小。
-   支持关联学生、作业、表现、成绩、安排和消息。
-   删除业务记录时不立即物理删除附件。
-   提供清理无引用附件的维护脚本。

# 十七、消息和待确认操作

## 1. 消息记录

`messages`至少包含：

-   id
-   external_message_id
-   channel
-   sender_id
-   content
-   message_type
-   received_at
-   processing_status
-   parsed_intent
-   result_summary
-   error_message

`external_message_id`必须唯一，防止微信重复投递造成重复写入。

状态包括：

-   received
-   processing
-   waiting_confirmation
-   completed
-   failed
-   ignored
-   revoked

## 2. 待确认操作

`pending_actions`至少包含：

-   id
-   message_id
-   intent
-   payload_json
-   missing_fields
-   question
-   status
-   expires_at
-   confirmed_at
-   created_at

以下情况必须追问或确认：

-   学生重名
-   学生不存在
-   日期不明确
-   图片无法确认学生
-   调课日期不明确
-   值日自然语言规则存在歧义
-   值日排班规则存在冲突
-   批量导入存在无法识别的数据
-   批量修改影响多名学生
-   长时间范围批量调课
-   删除、恢复或撤销数据

# 十八、审计和撤销

`audit_logs`至少包含：

-   id
-   operator_type
-   operator_id
-   action
-   entity_type
-   entity_id
-   before_json
-   after_json
-   source_message_id，可空
-   created_at

所有重要的新增、修改、删除、恢复、撤销和批量操作必须记录审计日志。

课程变更表中不保存source字段，但可以由通用审计日志记录操作。

# 十九、综合数据分析

OpenClaw必须能够综合多个模块的数据回答问题，而不是只能逐表查询。

典型问题包括：

```
张三最近表现怎么样？
张三这个月和上个月相比有什么变化？
最近哪些学生需要重点关注？
班级最近整体情况怎么样？
未交作业较多的学生是否也存在迟到问题？
成绩下降的学生最近有什么行为记录？
值日评分较低的小组主要有什么问题？
最近一周班级作业、考勤和课堂表现如何？
```

## 1. 分析原则

后端负责：

-   查询数据
-   计算指标
-   计算变化
-   识别关联
-   返回证据
-   判断数据是否充足

OpenClaw负责：

-   选择分析接口
-   组合结构化结果
-   生成自然语言说明
-   在结论中注明时间范围
-   在数据不足时说明限制

不允许OpenClaw仅依赖聊天记忆生成学生分析。

不允许模型编造：

-   未发生的行为
-   不存在的成绩
-   虚假的趋势
-   没有数据支持的因果关系

“相关性”不能描述为“因果关系”。

## 2. 学生综合分析

学生综合分析至少整合：

-   作业提交率
-   未交次数
-   迟交次数
-   需订正次数
-   出勤率
-   迟到次数
-   缺勤次数
-   请假次数
-   正向行为次数
-   负向行为次数
-   严重事件
-   最近考试成绩
-   成绩变化
-   值日次数
-   值日完成情况
-   值日评分
-   当前座位
-   日常表现原始记录
-   数据覆盖范围

支持时间范围：

-   最近7天
-   最近14天
-   最近30天
-   本周
-   本月
-   本学期
-   自定义日期范围

返回结构至少包含：

```
{
  "student": {},
  "period": {},
  "data_coverage": {},
  "metrics": {
    "homework": {},
    "attendance": {},
    "behavior": {},
    "scores": {},
    "duty": {}
  },
  "trends": [],
  "attention_items": [],
  "positive_items": [],
  "evidence": [],
  "warnings": []
}
```

`evidence`应包含支持结论的原始记录ID、日期、类型和摘要。

## 3. 班级综合分析

班级综合分析至少包含：

-   学生总数
-   作业完成率
-   未交作业人数和次数
-   出勤率
-   迟到和缺勤人数
-   高频负向行为
-   高频正向行为
-   需要关注的学生
-   各科平均分
-   成绩上升和下降人数
-   值日完成率
-   值日平均评分
-   值日安排均衡程度
-   数据缺失情况
-   与上一时间段的比较

## 4. 跨模块关联分析

至少支持：

-   未交作业次数与成绩变化对照
-   迟到次数与成绩变化对照
-   负向行为次数与作业完成率对照
-   值日完成情况与日常表现对照
-   本周期和上一周期对比

分析只能描述：

-   同时出现
-   可能相关
-   值得关注

不能直接得出因果结论。

## 5. 重点关注学生

后端提供可配置的关注规则，例如：

-   14天内未交作业不少于3次
-   30天内迟到不少于3次
-   存在严重负向事件
-   最近考试下降超过指定比例
-   连续两次考试下降
-   值日连续未完成
-   多个维度同时异常

返回每名学生：

-   触发规则
-   对应指标
-   证据记录
-   建议关注方向

不要直接给学生贴永久标签。

## 6. 综合分析接口

至少提供：

```
GET /api/v1/analytics/students/{student_id}/comprehensive
GET /api/v1/analytics/students/{student_id}/compare
GET /api/v1/analytics/classes/{class_id}/comprehensive
GET /api/v1/analytics/classes/{class_id}/compare
GET /api/v1/analytics/classes/{class_id}/attention-students
GET /api/v1/analytics/classes/{class_id}/cross-module
GET /api/v1/analytics/classes/{class_id}/data-quality
```

所有分析接口支持：

-   start_date
-   end_date
-   compare_start_date
-   compare_end_date
-   subject，可空
-   event_type，可空

# 二十、每日早报

每天7点由OpenClaw Cron调用早报接口。

后端只返回结构化数据，不负责微信发送。

早报至少包含：

-   今日最终课表
-   临时调课
-   今日值日安排
-   值日空缺
-   昨日值日评分
-   今日到期安排
-   未来3天事项
-   已逾期事项
-   未交作业摘要
-   最近需要关注的学生
-   待确认消息
-   数据异常

接口：

```
GET /api/v1/briefings/morning?class_id=...&date=...
```

# 二十一、OpenClaw工具

至少为OpenClaw定义以下工具，并在文档中说明对应REST接口。

## 班级与学生

```
class_list
class_summary
student_search
student_create
student_update
student_detail
```

## 座位

```
seat_current_get
seat_history_list
seat_snapshot_get
seat_update
seat_swap
seat_restore
```

## 值日

```
duty_rule_validate
duty_rule_save
duty_schedule_preview
duty_schedule_confirm
duty_today
duty_weekly
duty_student_query
duty_replace_student
duty_complete
duty_score_create
duty_statistics
```

## 作业与表现

```
homework_create
homework_update_status
homework_missing_list
homework_summary

student_event_create
student_event_batch_create
student_event_query
student_event_revoke
```

## 考勤和成绩

```
attendance_set
attendance_query
attendance_summary

exam_create
score_batch_save
score_query
```

## 课表

```
timetable_base_get
timetable_base_update
timetable_daily
lesson_override_create
lesson_override_remove
lesson_swap_preview
lesson_swap_confirm
lesson_batch_change_preview
lesson_batch_change_confirm
```

## 安排与提醒

```
arrangement_create
arrangement_complete
arrangement_query
due_reminders
morning_brief
```

## 综合分析

```
student_comprehensive_analysis
student_period_compare
class_comprehensive_analysis
class_period_compare
class_attention_students
class_cross_module_analysis
class_data_quality
```

## 消息与确认

```
message_register
pending_action_create
pending_action_confirm
pending_action_cancel
operation_undo
```

每个工具必须在`docs/openclaw-tools.md`中说明：

-   工具名称
-   功能
-   对应接口
-   请求参数
-   返回参数
-   是否需要确认
-   错误码
-   调用示例

# 二十二、REST API规范

统一前缀：

```
/api/v1
```

成功响应：

```
{
  "success": true,
  "data": {},
  "message": "操作成功",
  "request_id": "..."
}
```

错误响应：

```
{
  "success": false,
  "error": {
    "code": "STUDENT_AMBIGUOUS",
    "message": "找到多名同名学生",
    "details": {}
  },
  "request_id": "..."
}
```

至少定义：

-   VALIDATION_ERROR
-   NOT_FOUND
-   DUPLICATE_MESSAGE
-   STUDENT_NOT_FOUND
-   STUDENT_AMBIGUOUS
-   STUDENT_NO_CONFLICT
-   CLASS_MISMATCH
-   SEAT_LAYOUT_INVALID
-   SEAT_STUDENT_DUPLICATE
-   DUTY_RULE_INVALID
-   DUTY_SCHEDULE_CONFLICT
-   DUTY_SCORE_INVALID
-   TIMETABLE_CONFLICT
-   SCORE_EXCEEDS_FULL_SCORE
-   PENDING_CONFIRMATION_REQUIRED
-   OPERATION_NOT_REVERSIBLE
-   DATABASE_BUSY
-   INTERNAL_ERROR

要求：

-   自动生成OpenAPI文档。
-   列表接口支持分页。
-   查询接口支持筛选和排序。
-   POST接口支持幂等键或消息ID。
-   SQLite锁冲突返回明确错误。
-   不将异常堆栈返回调用方。
-   为每个请求生成request_id。

# 二十三、SQLite要求

初始化时启用：

```
PRAGMA journal_mode = WAL;
PRAGMA foreign_keys = ON;
PRAGMA busy_timeout = 5000;
```

要求：

-   跨表操作使用事务。
-   建立必要索引。
-   不产生孤立外键。
-   使用单一后端服务作为主要写入入口。
-   配置适合单进程低并发。
-   提供初始化和迁移命令。
-   提供数据库备份脚本。
-   使用SQLite backup API进行一致性备份。
-   备份附件目录。
-   提供恢复说明。

# 二十四、项目结构

建议采用：

```
class-manager-backend/
├── app/
│   ├── main.py
│   ├── config.py
│   ├── database.py
│   ├── models/
│   ├── schemas/
│   ├── repositories/
│   ├── services/
│   ├── analytics/
│   ├── api/
│   │   └── v1/
│   ├── core/
│   └── utils/
├── alembic/
├── tests/
├── scripts/
│   ├── init_db.py
│   ├── seed_demo.py
│   ├── backup.py
│   └── restore.py
├── data/
├── attachments/
├── docs/
│   ├── architecture.md
│   ├── database.md
│   ├── analytics.md
│   └── openclaw-tools.md
├── alembic.ini
├── requirements.txt
├── .env.example
├── README.md
└── run.py
```

不要把所有代码放在一个文件。

# 二十五、测试要求

使用pytest和独立临时SQLite数据库，至少覆盖：

## 学生

1.  同一班级学号不能重复。
2.  同名学生能够存在。
3.  同名查询返回歧义。
4.  软删除学生后历史数据仍可审计。

## 座位

1.  第一次保存座位表生成快照。
2.  修改后生成新快照。
3.  当前座位表为时间最新的快照。
4.  历史快照保持不变。
5.  同一快照中学生不能重复。
6.  其他班级学生不能加入。
7.  座位交换生成新快照。
8.  恢复历史布局生成新快照。
9.  layout行列不一致时拒绝保存。

## 值日

1.  结构化规则能够校验。
2.  预览不会写入正式数据。
3.  用户确认后才生成安排。
4.  无法满足的规则返回冲突。
5.  排班尽量保持次数均衡。
6.  临时替换保留原记录关系。
7.  评分不能超过满分。
8.  权重总分计算正确。
9.  修改评分产生审计记录。

## 作业、表现与考勤

1.  作业批量状态更新正确。
2.  未交作业事件不会重复生成。
3.  表现记录能按日期和类型查询。
4.  同日同时段考勤不能重复。
5.  大课间不计入全天出勤率。
6.  出勤率不能超过100%。

## 成绩

1.  分数不能超过满分。
2.  批量成绩错误时整批回滚。
3.  学生成绩趋势计算正确。

## 课表

1.  日期和节次生成稳定lesson_key。
2.  临时变更只影响对应lesson_key。
3.  撤销变更后恢复基础课表。
4.  两节课互换生成两条覆盖记录。
5.  互换操作事务回滚正确。
6.  批量调课预览不写数据库。
7.  确认后生成多个具体日期变更。
8.  同一lesson_key不能有两个有效覆盖。
9.  不同班级允许相同lesson_key。
10.  取消课程后仍返回对应节次。
11.  课程变更不保存source相关字段。
12.  课表不保存课程开始和结束时间。

## 综合分析

1.  学生分析能够整合作业、考勤、行为、成绩和值日数据。
2.  班级分析返回正确统计。
3.  时间段对比结果正确。
4.  数据不足时返回明确警告。
5.  重点关注学生包含触发规则和证据。
6.  关联分析不输出未经计算的数据。
7.  分析结果包含原始证据ID。
8.  早报正确整合课表、值日、作业、安排和关注学生。

## 通用

1.  重复消息不会重复写入。
2.  待确认操作能够继续完成。
3.  外键约束生效。
4.  API响应格式正确。
5.  错误码符合约定。

# 二十六、文档要求

README必须包含：

-   项目简介
-   技术栈
-   安装方法
-   初始化数据库
-   Alembic迁移
-   启动方式
-   配置说明
-   API文档地址
-   测试方式
-   数据备份和恢复
-   OpenClaw调用方式
-   低配置服务器部署建议
-   已实现功能
-   尚未实现功能

同时编写：

```
docs/architecture.md
docs/database.md
docs/analytics.md
docs/openclaw-tools.md
```

`analytics.md`必须说明：

-   学生综合分析指标
-   班级综合分析指标
-   时间范围
-   对比算法
-   重点关注规则
-   跨模块关联原则
-   数据不足处理
-   证据返回格式

# 二十七、实施顺序

请按照以下顺序执行：

1.  完整阅读参考文件。
2.  检查当前工作目录。
3.  输出简短实施计划。
4.  建立项目结构。
5.  实现配置、数据库和Alembic。
6.  实现班级和学生档案。
7.  实现座位快照。
8.  实现值日规则、排班和评分。
9.  实现作业、表现和考勤。
10.  实现成绩。
11.  实现基础课表和课程覆盖。
12.  实现日常安排和提醒。
13.  实现附件、消息、待确认和审计。
14.  实现综合分析服务。
15.  实现OpenClaw工具接口。
16.  实现早报接口。
17.  编写测试。
18.  运行全部测试。
19.  修复所有失败。
20.  检查OpenAPI文档。
21.  完善README和设计文档。
22.  给出最终实现总结。

除非遇到无法合理判断的关键矛盾，否则不要中途询问需求。次要问题采用保守、简单、可扩展的默认方案，并记录在README中。

# 二十八、最终验收标准

完成后必须确保：

-   后端能够实际启动。
-   SQLite能够自动初始化。
-   Alembic迁移正常。
-   OpenAPI文档可访问。
-   所有核心模块具有可调用API。
-   座位表使用时间快照和单表layout_json设计。
-   值日支持自然语言规则、预览、确认和评分。
-   调课使用lesson_key和简单覆盖记录。
-   课程变更不保存source相关信息。
-   课程不保存开始和结束时间。
-   临时调课不影响其他日期。
-   OpenClaw能够综合多类数据分析学生和班级。
-   分析结果包含数据范围、指标、证据和警告。
-   重复消息不会重复写入。
-   模糊消息能够进入待确认状态。
-   所有重要修改具有审计日志。
-   早报返回真实数据库数据。
-   全部自动化测试通过。
-   不实现网页界面。
-   不使用Docker、Redis、Celery和消息队列。
-   项目结构清晰，未来网页端可以直接复用REST API。

现在开始：先阅读参考文件并检查工作目录，然后按照本Prompt实现完整后端。