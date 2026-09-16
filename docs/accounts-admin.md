# 账户、管理员与班级归属

## 初始管理员

应用首次启动时自动创建唯一管理员：

- 用户名：默认配置为 `admin`，以 `CLASSCLAW_DEFAULT_ADMIN_USERNAME` 为准
- 初始密码：以 `CLASSCLAW_DEFAULT_ADMIN_PASSWORD` 为准；`32767` 仅是部分旧部署的历史默认值
- 角色：`admin`

首次登录后应立即调用 `POST /api/v1/auth/change-password` 修改密码。用户名经过 Unicode 规范化并按不区分大小写的方式保存，例如 `Teacher01` 与 `teacher01` 视为同一用户名。

默认管理员只会在首次启动或完整初始化时创建。之后修改 `CLASSCLAW_DEFAULT_ADMIN_PASSWORD` 不会覆盖数据库中已有管理员的密码；忘记密码时使用下方本地重置命令，不需要完整初始化或删除业务数据。

`CLASSCLAW_API_TOKEN` 继续保留给 OpenClaw 插件和运维脚本使用，并被视作系统管理员凭证；普通网页用户使用 `/auth/login` 返回的会话令牌。

## 本地终端重置管理员密码

由拥有服务器终端及业务数据库读写权限的维护者执行，无需旧密码或 OpenClaw 在线。建议先停止 ClassClaw，避免重置与正在登录/改密的请求并发；重置完成后重新启动服务。

```bash
cd <项目根目录>
.venv/bin/python scripts/reset_admin_password.py
```

命令直接将现有管理员密码重置为 `.env` 中 `CLASSCLAW_DEFAULT_ADMIN_PASSWORD` 的配置值，无需旧密码、手动输入或额外确认。沿用应用配置优先级：进程环境中同名变量优先于项目 `.env`。该变量未显式配置或为空时拒绝执行，不会偷偷回退为 `32767`；配置值必须满足网页登录的 1–128 字符长度限制，不能全部为空白，建议使用长随机密码。终端只显示目标库、管理员用户名和结果，不显示密码或哈希，不接受明文密码命令行参数。不会创建数据库、初始化账号、调用 Gateway 或修改配置文件。

成功后保留管理员用户名、角色、启停状态和全部业务数据；撤销该管理员的旧登录会话，并将 `must_change_password` 设为 `true`。停用的管理员不会被自动启用。密码哈希更新、会话撤销和 `operator_type=local_cli` 的 `reset_password` 审计在同一个业务事务中提交，审计不记录密码或哈希；不触碰独立用量库。共享 `CLASSCLAW_API_TOKEN` 不属于登录会话，不会被这个命令撤销。

命令使用与应用相同的 TOML / `.env` / 环境变量配置；多部署环境可先指定 `CLASSCLAW_CONFIG_FILE`，务必核对命令显示的目标库。此恢复能力只提供本地脚本，没有新增 HTTP 接口或 Agent 工具。

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
| DELETE | `/api/v1/admin/users/{id}` | 管理员 | 删除班主任账号并注销会话；原班级保留为未分配；清理未完成的个人草稿与独占附件，失败可重试 |
| POST | `/api/v1/admin/users/{id}/reset-password` | 管理员 | 重置为 `32767` 并注销旧会话 |
| POST | `/api/v1/admin/users/{id}/assign-class` | 管理员 | 把一个历史班级分配给班主任 |

系统不提供创建第二个管理员的接口，数据库也使用唯一部分索引保证最多一个 `admin`。普通用户角色固定为 `head_teacher`。

## 一账号一班级

班主任创建 onboarding 时，后端把用户 ID 写入引导会话；最终确认时，同一事务将它写入班级 `owner_user_id`。服务层和数据库唯一索引都会拒绝第二个班级。班主任只能列出自己的班级、访问自己的学生，并且只能创建或刷新自己班级的智能体和微信绑定。

迁移前已有班级没有归属，只有管理员可见。管理员可以通过 `assign-class` 将它分配给一个尚未拥有班级的班主任。

班主任和管理员可对有权限的班级调用 `DELETE /api/v1/classes/{class_id}`。该接口不是停用，执行的是可恢复的分阶段清理：先在 Gateway 中核对归属并停止该班活动（取消未完成的扫码、登出独占微信账号并清除本地凭据、确认班级智能体没有运行中任务），再移除 Agent、微信路由、`agentClasses` 映射（显式置 `null`）并回读校验，然后在一个数据库事务中删除班级、学生、关联业务数据和考试统计缓存，最后删除隔离 workspace 与 Agent 状态目录。删除成功后 `GET /auth/me` 的 `class_id` 为空，该班主任可以重新创建一个班级。

任何阶段失败都会保留 `deletion_operations` 记录并返回明确错误，不会冒充成功；已经完成的步骤可安全重试。重复调用 `DELETE /classes/{id}` 或管理员调用 `POST /api/v1/admin/deletions/{id}/retry` 会从失败阶段继续。外部资源被其他班级共享（例如微信账号被其他智能体使用）或路径越界时拒绝删除并返回冲突。删除进行中会拒绝该班级的新写入（409 `DELETION_IN_PROGRESS`），包括新写提案的创建与已有预览的确认执行；只读查询仍然可用。

## 智能体和数据库调试

班主任在“账户设置 → 班级 Agent · 绑定与设置”管理本班 Agent：未创建时可创建，已创建时可选择主模型/图片模型/语音模型，并主动生成微信二维码或重新绑定。打开账户设置不会自动启动微信登录；即使已分配微信 account alias，未完成扫码的记录也会保留二维码入口。账户资料和修改密码不依赖智能服务在线，管理员的账户设置不展示教师班级控制项。

“班级 Agent”页面只保留对话、会话列表和会话独立思考强度。切换到账户设置不会停止已发送的聊天请求；离开账户设置会停止该视图的扫码轮询、关闭模型设置弹窗，不会取消聊天。微信完成后只刷新绑定区域，不清空正在填写的密码表单。

微信需要数字验证时，绑定区域显示验证码输入框，填写手机微信显示的数字即可；输入在提交、重新生成和离页时清空，不会回显到错误详情或写入数据库。`POST /classes/{class_id}/agent-binding/verify` 只接受本班本次登录与当前验证提示的标识，遵守微信功能开关和班级隔离。

管理员可使用：

- `GET /api/v1/admin/agents`：查看班级、所有者、Agent、微信账号和错误状态。
- `POST /api/v1/admin/agents/{class_id}/wechat/start`：刷新 Agent 配置并重新生成微信二维码。
- `GET /api/v1/admin/database/overview`：查看数据库表及行数，`database` 标明 `core`（业务库）或 `usage`（用量库）；用量库不可用时仍可查看业务库。
- `GET /api/v1/admin/database/tables/{table}?offset=0&limit=50`：分页查看只读表数据。

数据库调试接口不接受 SQL，不提供写入能力，并遮蔽 `users.password_hash` 和 `user_sessions.token_hash`。

## 独立管理员控制台

管理员登录后只进入 `/app/#/admin/*` 技术控制台，不加载班主任业务导航。控制台包含：

导航收敛为「概览」「设置中心」「班级与账号」「运维中心」四项。`/admin/settings` 统一 ClassClaw 表单 / TOML 源码、OpenClaw 白名单配置及系统 / 班级 Agent。`/admin/access` 合并用户与班级管理，班级行展开后可改派负责人及使用共用 Agent 面板。`/admin/ops?tab=usage|logs|maintenance|database|audit` 提供五个运维 tab。旧路由保留跳转，包含原 query 的班级等信息。完整分类见 [配置说明](configuration.md#管理端配置层级)。

- `GET /api/v1/admin/overview`：用户、班级、学生、智能体、会话、SQLite/附件占用及 OpenClaw 健康状态。
- `POST /api/v1/admin/classes`：已停用，固定返回 `WEB_ONBOARDING_REQUIRED`——管理员不具备任何建班入口，新班级只能由班主任账号通过 `/app/#/onboarding` 创建向导完成资料复核、专属智能体与微信绑定；管理员在班级创建后经此页分配/解除负责人。`PATCH /api/v1/admin/classes/{id}/owner`：分配或解除班主任；班级字段修改和删除复用 `/classes/{id}`。
- `GET|PATCH /api/v1/admin/openclaw/config`：读取 Gateway 摘要并修改 Responses、DM Scope、ClassClaw 插件开关。Gateway Token 仅返回是否配置，不返回原文或尾号；另返回全局主/图片模型、Provider 名称与 Gateway 提供的配置文件路径。
- `GET /api/v1/admin/openclaw/agents/catalog`：统一列出 Main、数据提取和全部班级智能体。系统智能体使用 `main`、`extractor` 作为稳定管理标识，班级智能体继续使用班级 ID。
- `GET /api/v1/admin/openclaw/agents/{identifier}/settings`、`PATCH /api/v1/admin/openclaw/agents/{identifier}`：读取模型目录、单智能体 Runtime 和 workspace 摘要；修改显示名、主/回退/utility 模型、thinking effort、reasoning、verbose、fast mode、上下文注入、Prompt 字符预算、Skill 预算、memory search 及模型参数。
- `PUT /api/v1/admin/openclaw/agents/{identifier}/workspace/{filename}`、`POST .../reset`：编辑 `AGENTS.md`、`SOUL.md`、`IDENTITY.md`、`TOOLS.md`、`USER.md`、`HEARTBEAT.md`。文件名使用白名单，保存时带 SHA-256 版本检查；班级和提取智能体可恢复 ClassClaw 默认内容，Main 只允许保存和版本校验，不提供默认内容覆盖。自定义内容不会被日常 Agent 修复流程覆盖。
- `GET /api/v1/admin/usage`：按自然日窗口聚合业务库的登录、交互、写入和独立用量库的 OpenClaw Responses Token。Token 从迁移 `0009` 后开始采集，`0014` 保留并迁移已有采集记录，不补算从未采集的历史数据。统计库不可用时返回 503 `USAGE_DATABASE_UNAVAILABLE`，不会把故障显示为零用量。
- `GET /api/v1/admin/usage/agents`：按 Main、数据提取和班级 Agent 读取 OpenClaw 会话统计，包括模型调用次数、Agent 回复数、错误数、Token/费用、平均/P95/最短/最长/最近一次响应耗时、有效样本数及逐日趋势。Token 和费用来自 `usage.cost`；调用与延迟由后端只读扫描 TOML `storage.openclaw_state_dir` 下对应 Agent 的 transcript 元数据，忽略且不保存消息正文。耗时优先使用 OpenClaw `durationMs`，缺失时使用用户消息到 Agent 回复记录的时间差。
- `GET /api/v1/admin/logs`：读取 ClassClaw 轮转 JSON 日志或 OpenClaw Gateway 日志，支持来源、级别、关键字、精确 request_id、since 时间和 cursor 增量筛选，单次最多扫描 5000 行。网页可展开请求链路、每 3 秒增量刷新并导出当前视图，离页取消日志读取与轮询。ClassClaw 日志只记录请求元数据和异常，不记录请求正文或聊天消息。
- `GET /api/v1/admin/settings`：读取全部非敏感启动配置，保留原有列表结构和旧字段 key；每项标注 TOML 字段、环境变量、实际来源及“修改后重启”。保留旧列表接口，不提供逐项 PUT；网页编辑使用下述文档接口。
- `GET /api/v1/admin/settings/catalog`：管理员专用分组目录，包含启动配置文件位置、版本、优先级及凭据配置状态；不依赖 OpenClaw 在线，不返回密钥内容，禁止缓存。
- `GET|PATCH /api/v1/admin/settings/document`、`POST /api/v1/admin/settings/check`、`POST /api/v1/admin/settings/rollback`：非敏感 TOML 文档读取、校验、原子保存和恢复；环境变量锁定、版本冲突、同目录备份、失败恢复和仅键名 / hash 审计。
- `GET|PATCH /api/v1/admin/openclaw/config/raw`：通过 Gateway RPC 读取脱敏 JSON，编辑仅接受三个白名单标量路径，必须携带 `base_hash`；冲突返回 409 并刷新配置。
- `/app/#/admin/ops?tab=maintenance`：备份、恢复、附件检查、完整初始化、未完成删除清理及 OpenClaw 会话维护；日志 tab 下也保留会话维护卡，预览仍使用 `POST /api/v1/admin/openclaw/sessions/cleanup?enforce=false`，不自动删除聊天会话。
- `GET /api/v1/admin/deletions`：列出尚未完成的班级/账号删除记录（目标、阶段、错误码、是否可重试），不返回凭据或消息内容。
- `POST /api/v1/admin/deletions/{id}/retry`：从失败阶段继续一次未完成的删除；完成状态不变，已不存在的资源视为完成。
- `POST /api/v1/admin/system/initialize`：要求正文确认值 `INITIALIZE`，执行完整出厂重置。全部旧用户、登录会话、班级业务数据、临时草稿、附件、数据库运行状态、审计日志、AI/Token 统计和班级 Agent 都会删除；随后只重新创建启动配置指定的默认管理员。OpenClaw 的 `main` 与 `classclaw-extractor` 是两个默认智能体，不删除；Main 保持 OpenClaw 基础配置，提取智能体恢复 ClassClaw 默认工作区，两者的历史会话目录清空。程序代码、数据库表结构、`classclaw.toml`、`.env` 和 OpenClaw 安装本身不变。外部智能体、文件或日志有任何一项无法清理时，接口返回错误并保留数据库，便于修复后安全重试；只有全部预清理成功后才清空数据库并返回 `completed`。发起初始化的登录会话随全部会话一起失效。

功能开关不是纯界面状态：文件解析、学生事件 AI 分类、微信绑定和主动提醒的对应后端接口都会执行开关检查。配置来自启动时读取的 `config/classclaw.toml`（环境变量可兼容覆盖），不再写入 `system_settings`；任何密钥都不能通过管理端配置接口读取或修改。详见 [静态配置说明](configuration.md)。

独立用量库与业务库不能一起原子提交。完整初始化先清空用量库，再在一个事务中清空业务库；用量库失败则保留业务数据，业务库失败则回滚业务删除并在错误中明确报告已经删除的用量条数。需要全量回退时必须恢复停机备份中的两个库及附件。

## 升级与启动

```bash
cd <项目根目录>
source .venv/bin/activate
alembic upgrade head
python run.py
```

启动后打开 <http://127.0.0.1:8000/app/test.html>。验收台提供后端功能地图、管理员和班主任登录、用户管理、班级归属、智能体/微信二维码、脱敏数据库浏览、自动只读巡检及通用 API 请求调试。


### 删除失败后的重试与维护边界

- 原班主任可在班级业务记录已删除后重复 DELETE，由删除记录中的原所有者校验权限；管理员可在系统维护页重试。
- 删除期间拒绝目标账号的新写入及管理员重新分配班级，退出登录与取消 AI 请求仍可用。不带浏览器任务 ID 的 AI 请求也参与忙碌检查。
- 每次重试在清理 Gateway、删除目录前重新核对数据库与 Gateway 资源归属。异常配置结构直接拒绝操作，不能作为空列表写回；配置更新必须读回确认映射和频道账号均已移除。
- 网页导入、聊天和班级 Agent 上传同时保存附件归属，数据库保存失败会清除新写入的文件。共享引用仍会阻止附件删除。
- 班级资料页删除单个附件时，其他班级归属、业务引用或重复文件路径都会阻止删除。文件清理失败返回非成功状态并保留附件记录供重试；文件已不存在视为完成。先安全删除文件，再提交元数据删除，中断后仍可通过原附件记录重试；不返回“文件仍在”的普通成功提示。多文件上传逐个保存，失败后只重试尚未成功的部分。
- `python scripts/cleanup_deletions.py` 默认只报告。执行前停止 ClassClaw 并备份业务库、用量库、附件及 OpenClaw 状态。
- 维护脚本禁用 `--state-dirs` / `--attachments` 批量清理。`--state-dir` 必须有对应班级删除审计且 Gateway 无配置引用或运行任务；`--wechat-account` 必须无当前引用，并有历史配置和删除审计证明归属。
- `--gateway` 仅清理已审计删除班级的失效映射；单独使用 `--wechat-account` 不会顺便清理其他映射。`--unknown-files` 仅删除 UUID 命名、无数据库记录且内容哈希匹配历史测试样例的文件。归属不明附件保持不动。
