# 账户、管理员与班级归属

## 初始管理员

应用首次启动时自动创建唯一管理员：

- 用户名：`admin`
- 初始密码：`32767`
- 角色：`admin`

首次登录后应立即调用 `POST /api/v1/auth/change-password` 修改密码。用户名经过 Unicode 规范化并按不区分大小写的方式保存，例如 `Teacher01` 与 `teacher01` 视为同一用户名。

`CLASSCLAW_API_TOKEN` 继续保留给 OpenClaw 插件和运维脚本使用，并被视作系统管理员凭证；普通网页用户使用 `/auth/login` 返回的会话令牌。

## 账户接口

| 方法 | 路径 | 权限 | 用途 |
|---|---|---|---|
| POST | `/api/v1/auth/login` | 公开 | 用户名和密码登录 |
| GET | `/api/v1/auth/me` | 已登录 | 查看当前账号和班级 |
| POST | `/api/v1/auth/change-password` | 已登录 | 修改自己的密码 |
| POST | `/api/v1/auth/logout` | 已登录 | 注销当前会话 |
| GET | `/api/v1/admin/users` | 管理员 | 用户列表 |
| POST | `/api/v1/admin/users` | 管理员 | 新建班主任，默认密码为 `32767` |
| PATCH | `/api/v1/admin/users/{id}` | 管理员 | 修改唯一用户名、显示名或启停账号 |
| DELETE | `/api/v1/admin/users/{id}` | 管理员 | 删除班主任账号并注销会话；原班级保留为未分配 |
| POST | `/api/v1/admin/users/{id}/reset-password` | 管理员 | 重置为 `32767` 并注销旧会话 |
| POST | `/api/v1/admin/users/{id}/assign-class` | 管理员 | 把一个历史班级分配给班主任 |

系统不提供创建第二个管理员的接口，数据库也使用唯一部分索引保证最多一个 `admin`。普通用户角色固定为 `head_teacher`。

## 一账号一班级

班主任创建 onboarding 时，后端把用户 ID 写入引导会话；最终确认时，同一事务将它写入班级 `owner_user_id`。服务层和数据库唯一索引都会拒绝第二个班级。班主任只能列出自己的班级、访问自己的学生，并且只能创建或刷新自己班级的智能体和微信绑定。

迁移前已有班级没有归属，只有管理员可见。管理员可以通过 `assign-class` 将它分配给一个尚未拥有班级的班主任。

班主任和管理员可对有权限的班级调用 `DELETE /api/v1/classes/{class_id}`。该接口不是停用：它会先从 OpenClaw 配置移除班级 Agent、微信路由和 `agentClasses` 映射，删除隔离 workspace，再在一个数据库事务中删除班级、学生及关联业务数据。删除成功后 `GET /auth/me` 的 `class_id` 为空，该班主任可以重新创建一个班级。OpenClaw 清理失败时数据库不会删除，调用方可排除 Gateway 故障后重试。

## 智能体和数据库调试

管理员可使用：

- `GET /api/v1/admin/agents`：查看班级、所有者、Agent、微信账号和错误状态。
- `POST /api/v1/admin/agents/{class_id}/wechat/start`：刷新 Agent 配置并重新生成微信二维码。
- `GET /api/v1/admin/database/overview`：查看数据库表及行数。
- `GET /api/v1/admin/database/tables/{table}?offset=0&limit=50`：分页查看只读表数据。

数据库调试接口不接受 SQL，不提供写入能力，并遮蔽 `users.password_hash` 和 `user_sessions.token_hash`。

## 独立管理员控制台

管理员登录后只进入 `/app/#/admin/*` 技术控制台，不加载班主任业务导航。控制台包含：

- `GET /api/v1/admin/overview`：用户、班级、学生、智能体、会话、SQLite/附件占用及 OpenClaw 健康状态。
- `POST /api/v1/admin/classes`、`PATCH /api/v1/admin/classes/{id}/owner`：直接创建班级、可选创建 Agent、分配或解除班主任；班级字段修改和删除复用 `/classes/{id}`。
- `GET|PATCH /api/v1/admin/openclaw/config`：读取脱敏 Gateway 摘要并修改 Responses、DM Scope、ClassClaw 插件开关。Gateway Token 只返回是否配置和末四位。
- `GET /api/v1/admin/openclaw/agents/{class_id}/settings`、`PATCH /api/v1/admin/openclaw/agents/{class_id}`：读取模型目录、单智能体 Runtime 和 workspace 摘要；修改显示名、主/回退/utility 模型、thinking effort、reasoning、verbose、fast mode、上下文注入、Prompt 字符预算、Skill 预算、memory search 及模型参数。
- `PUT /api/v1/admin/openclaw/agents/{class_id}/workspace/{filename}`、`POST .../reset`：编辑或恢复 `AGENTS.md`、`SOUL.md`、`IDENTITY.md`、`TOOLS.md`、`USER.md`、`HEARTBEAT.md`。文件名使用白名单，保存时带 SHA-256 版本检查；自定义内容不会被日常 Agent 修复流程覆盖。
- `GET /api/v1/admin/usage`：按时间窗口聚合登录、交互、写入和 OpenClaw Responses API 报告的 Token 用量。Token 从迁移 `0009` 后开始采集，不回填历史数据。
- `GET /api/v1/admin/usage/agents`：按班级 Agent 读取 OpenClaw 会话统计，包括模型调用次数、Agent 回复数、错误数、Token/费用、平均/P95/最短/最长/最近一次响应耗时、有效样本数及逐日趋势。Token 和费用来自 `usage.cost`；调用与延迟由后端只读扫描 `CLASSCLAW_OPENCLAW_STATE_DIR` 下对应 Agent 的 transcript 元数据，忽略且不保存消息正文。耗时优先使用 OpenClaw `durationMs`，缺失时使用用户消息到 Agent 回复记录的时间差。
- `GET /api/v1/admin/logs`：读取 ClassClaw 轮转 JSON 日志或 OpenClaw Gateway 日志，支持来源、级别、关键字和数量筛选。ClassClaw 日志只记录请求元数据和异常，不记录请求正文或聊天消息。
- `GET|PUT /api/v1/admin/settings/{key}`：读取和更新白名单功能开关、管理端分页和统计窗口常量。
- `POST /api/v1/admin/system/initialize`：要求正文确认值 `INITIALIZE`，保留账号、系统配置、审计和 Token 统计，删除所有班级业务数据、临时草稿、附件并逐个清理 OpenClaw 班级 Agent。响应状态为 `completed`、`partial` 或 `failed`，并包含逐班级清理报告。

功能开关不是纯界面状态：文件解析、学生事件 AI 分类、微信绑定和主动提醒的对应后端接口都会执行开关检查。环境变量和任何密钥不允许通过 `system_settings` 修改。

## 升级与启动

```bash
cd /Users/wellon/classclaw
source .venv/bin/activate
alembic upgrade head
python run.py
```

启动后打开 <http://127.0.0.1:8000/app/test.html>。验收台提供后端功能地图、管理员和班主任登录、用户管理、班级归属、智能体/微信二维码、脱敏数据库浏览、自动只读巡检及通用 API 请求调试。
