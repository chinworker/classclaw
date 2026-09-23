# AGENTS.md

面向 AI 编码代理的项目指南。阅读本文前无需任何先验知识。

## 项目概览

ClassClaw 是供 OpenClaw 智能体和网页端共用的轻量班级管理系统（班主任工作台）。核心特征：

- 确定性 REST 能力不依赖 OpenClaw 在线；只有自然语言理解、文件解析、智能体创建和微信绑定需要 OpenClaw。
- 对话式写入采用「结构化草稿 → 后端预览（WriteProposal）→ 用户复核 → 一次性原子执行」，网页结构化操作直接调用领域接口，不经过智能体。
- 请求分层固定：OpenClaw/网页端 → FastAPI 路由 → Pydantic Schema → Service → SQLAlchemy → SQLite。路由只负责 HTTP、身份和班级归属校验；跨表校验、事务、快照、排班、调课、分析都在 Service 层。

## 技术栈与运行边界

- Python 3.12+、FastAPI、SQLAlchemy 2、Pydantic 2、Alembic、SQLite、pytest、Uvicorn、httpx
- 单进程、单 Uvicorn worker，默认时区 `Asia/Shanghai`
- **不使用** Docker、Redis、Celery、消息队列、向量数据库、微服务或服务器本地大模型
- 附件保存在本地目录（默认 `data/attachments/`），SQLite 只记录路径、哈希与元数据
- 前端是 `web/` 下的原生 HTML/JS/CSS 静态文件，由 FastAPI `StaticFiles` 挂载到 `/app/`
- OpenClaw 插件为 TypeScript（`integrations/openclaw/classclaw`），另有微信兼容层 `integrations/openclaw/openclaw-weixin-compat`（纯 JS）

## 目录结构

- `app/main.py`：FastAPI 入口，注入 request_id、统一异常处理、挂载路由与 `web/` 静态目录
- `app/config.py`：基于环境变量的 frozen dataclass `Settings`（`.env` 经 python-dotenv 加载）
- `app/database.py`：读写双引擎构建（SQLite 自动启用 `foreign_keys=ON`、`busy_timeout=5000`、WAL、`synchronous=NORMAL`；写引擎为单连接并由线程锁串行化）。`get_db` 按 HTTP 方法选择会话（GET/HEAD/OPTIONS 走读连接池，其余走单写者），非请求上下文用 `writer_session()` / `reader_session()`；不要直接新建 Session
- `app/usage_database.py` / `app/models/usage.py`：独立 AI 用量库 `usage.db`，独立 metadata、读写连接与写锁，不参与业务事务；用量写入必须使用此模块的会话，不能用业务 `get_db`。迁移 `0014` 复制校验历史记录后移除业务库旧表
- `app/api/v1/`：按领域拆分的路由（`classes_students`、`seating_duty`、`academic`、`timetable`、`operations`、`analytics`、`approval`、`interactions`、`auth_admin`），由 `router.py` 聚合；除登录等公共路由外全部经过 `require_authenticated`
- `app/services/`：业务逻辑层（`class_student`、`seating`、`duty`、`academic`、`timetable`、`operations`、`approval`、`interactions`、`accounts`、`deletions`、`deletion_runtime`、`deletion_files`、`openclaw_bridge`、`openclaw_provisioning` 等）
- `app/analytics/service.py`：仅基于数据库事实生成结构化指标和证据的分析服务
- `app/models/entities.py`：全部 SQLAlchemy 实体（单文件）；`app/models/base.py` 提供 `IdMixin`（UUID 字符串主键）、`TimestampMixin`、`SoftDeleteMixin`
- `app/schemas/`：Pydantic 2 schema（`domain.py`、`auth.py`、`common.py`）
- `app/core/`：`errors.py`（`AppError`）、`responses.py`（统一 `ok()` 响应）、`security.py`（`Principal`、鉴权与班级归属隔离）
- `app/utils/time.py`：时区感知的 `now()`
- `alembic/versions/`：迁移（当前到 `0016_class_agent_memories`）
- `scripts/`：`init_db.py`、`seed_demo.py`、`backup.py`、`restore.py`、`cleanup_attachments.py`、`cleanup_deletions.py`
- `scripts/manage.py` / `deploy/`：Ubuntu 同机部署的 `classclaw` 运维命令、systemd/Nginx 和 2 核 4 GB 配置。维护命令由 root 协调，项目命令降为服务用户执行；升级只允许 Git 快进，停两项服务并完整备份后迁移，失败保留标记禁止自动启动，不能自动 reset/downgrade。生产依赖在 `requirements-runtime.txt`，开发 `requirements.txt` 引用它。详见 `docs/deployment.md`。
- `scripts/reset_admin_password.py`：仅限本地终端将现有唯一管理员密码重置为 `.env` 的 `CLASSCLAW_DEFAULT_ADMIN_PASSWORD`；不输出密码，事务性撤销旧会话并审计，不允许新增 HTTP/Agent 重置入口
- `web/`：网页前端（`index.html` 班级创建引导 + 业务页面；`test.html` 后端综合验收台）
- `web/js/agentChatStore.js`：按用户/班级隔离的内存对话列表与在途请求；新建/切换对话、站内页面卸载不能取消已发送聊天请求，原文不写入浏览器持久存储。页面 `dispose()` 只释放视图和输入设备资源；退出登录须清空对话并取消请求
- 教师网页“班级 Agent”只负责对话；创建、模型设置和 `ClassClaw Channels` 在 `web/js/pages/account.js` 的账户设置中。渠道统一入口为 `web/js/classclawChannels.js`，当前仅开放微信；微信 account alias 不代表已绑定，等待结果未返回新二维码时必须保留当前图片；二维码只存视图内存，离页清理扫码轮询不能取消聊天请求
- **ClassClaw Channels** 是外部渠道产品总称。每个班级 Agent 通过 OpenClaw Channels 与渠道兼容层接入，后续开放同 Agent 多渠道绑定。当前仍使用单个微信账号字段，不能宣称已支持多个绑定；新增渠道前须迁移为独立连接表，并同步扩展路由、登录状态、提醒目标和分阶段删除。微信重绑必须保留本 Agent 的其他渠道路由。详见 `docs/classclaw-channels.md`。
- 班级思考强度统一来自 `openclaw.class_agent_thinking` / `CLASSCLAW_OPENCLAW_CLASS_AGENT_THINKING`，网页会话可独立覆盖，禁止用修改 Agent 全局配置实现会话覆盖。网页默认 SSE，断流不得视为成功；Gateway 私有会话设置端点只允许已绑定班级网页 key 和 `thinkingLevel`，不得扩为任意 RPC
- 网页思考选项必须来自 Gateway `agents.list` 返回的本班模型 `thinkingLevels`，区分档位 ID 与显示标签（如 `low` / `on`）；发送前刷新并由后端复核，不得写死模型能力表。配置默认不兼容时使用 Gateway 有效默认并在网页提示，显式不支持的选择不得静默替换
- 网页常用思考档位只列模型支持的 `off/low/medium/high`，二态模型保留“关闭／开启”标签。思考文本与正文分开，通过插件 Gateway 鉴权的只读 `web-chat-reasoning` 订阅获取；后端同时匹配班级网页会话 key 与本次 Responses run ID，不向浏览器转发工具事件、内部 key 或凭据。折叠状态和计时只存内存，离页清理界面定时器不能取消聊天；无思考文本时明确提示，不伪造内容
- `integrations/openclaw/`：OpenClaw 原生插件与兼容层
- `tests/`：pytest 测试（内存 SQLite）
- `docs/`：详细设计文档（架构、数据库、分析、账户、onboarding、OpenClaw 集成等），改动涉及对应领域时应同步更新

## 构建与运行命令

```bash
python3.12 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
alembic upgrade head            # 生产环境优先；也可用 python scripts/init_db.py，启动时亦会自动补建缺失表
python scripts/seed_demo.py     # 可选演示数据
python run.py                   # 或 uvicorn app.main:app --host 127.0.0.1 --port 8000 --workers 1 --no-access-log
```

服务端点：`/app/`（网页）、`/app/test.html`（验收台）、`/health`、`/docs`、`/openapi.json`。API 前缀 `/api/v1`。

数据库 schema 变更必须新增 Alembic 迁移（`alembic/versions/`），不要只改实体。

OpenClaw 插件（TypeScript）：

```bash
cd integrations/openclaw/classclaw
npm install && npm run build && npm test && npm run plugin:validate
```

## 测试

```bash
pytest        # pyproject.toml 已配置 testpaths=["tests"]、addopts="-q"、pythonpath=["."]
node --test tests/web/*.test.mjs  # 对话状态、页面交互与登录切换回归；不需要安装前端依赖
```

- 测试使用独立内存 SQLite（`StaticPool`），通过 `app.dependency_overrides[get_db]` 注入；OpenClaw 连接状态在 `tests/conftest.py` 中被 monkeypatch 掉，测试不依赖 OpenClaw 在线。
- 当前 39 个测试文件、403 个用例全部通过。覆盖：学号唯一与同名歧义、班内学号确定性解析（全角/“13号”/前导零/歧义候选/跨班）、座位快照、值日预览确认、作业幂等、考勤口径、成绩整批回滚、调课覆盖、分析、早报、统一响应、账户/管理员、onboarding 原子提交、删除分阶段重试与共享资源保护、班级 Agent 记忆（重复合并、更正冲突、临时规则到期恢复）。
- 新增业务功能应同步增加测试；测试客户端默认带 `X-ClassClaw-Surface: web` 头和 Bearer Token（若配置了 `CLASSCLAW_API_TOKEN`）。

代码风格：Ruff（`pyproject.toml` 配置，line-length 140，target py312）；代码普遍使用 `from __future__ import annotations`、现代类型标注（`str | None` 等）。

## 关键约定（改动时必须遵守）

- **统一响应**：成功用 `app.core.responses.ok()`，返回 `{success, data, message, request_id}`；业务错误抛 `AppError(code, message, status_code, details)`；参数校验失败统一返回 `VALIDATION_ERROR`；SQLite 锁冲突返回 503 `DATABASE_BUSY`。
- **鉴权与隔离**：角色只有唯一 `admin` 和 `head_teacher`；共享 Bearer Token（`CLASSCLAW_API_TOKEN`）仅供 OpenClaw 服务调用并视为 admin。班主任账号只能访问其绑定的唯一班级，路由层必须使用 `app/core/security.py` 中的 `require_owned_class` / `require_owned_student` / `scoped_class_id` / `require_owned_record` 做归属校验。
- **写入安全边界**：proposal 只接受 `app/services/approval.py` 中 `SUPPORTED_OPERATIONS` 白名单内的 operation；确认接口调用固定执行器，禁止任意 URL、SQL 或代码执行接口。预览保存归一化 payload、revision 和有效期，确认时需校验版本/过期。
- **删除与残留**：班级/账号删除走 `app/services/deletions.py` 的持久化阶段化清理（归属预检 → 停止活动与 Gateway 清理 → 业务数据删除 → 文件清理 → 完成校验），失败保留 `deletion_operations` 记录并返回非成功状态，重复 DELETE 或 `POST /admin/deletions/{id}/retry` 从失败阶段继续。Gateway 删除 `agentClasses` 和频道账号必须显式置 `null`（`config.patch` 的 `replacePaths` 只替换数组）；删除前必须取消未完成扫码、用 `channels.logout` 清理微信凭据、用 `tasks.list` 确认无运行任务，并拒绝共享账号与越界路径。删除进行中由 `app/core/security.py` 拒绝该班级的新写入，写提案的创建与确认执行都会返回 409 `DELETION_IN_PROGRESS`，读不受影响。
- **学号引用**：对话式写入中智能体一律用班内学号（`student_no`/`student_nos`，座位 `layout` 同）引用学生，不输出学生 UUID；`approval.create_proposal` 在归一化前经 `app/services/student_refs.py` 在绑定班级内确定性解析为 UUID（支持全角、“13号”、前导零，歧义报 `STUDENT_AMBIGUOUS` 并给候选，跨班/已删报 403），预览展示学号与姓名。网页结构化 API 与执行器仍用 UUID；`homework_id`/`exam_id`/`assignment_id`/`proposal_id`/`attachment_id` 保持不透明句柄。
- **学生排序**：无明确业务排序时按学号自然升序，不能按字符串将 10 排在 2 前面；SQL 使用 `student_order_by()`，Python 使用 `student_no_key()`，网页使用 `compareStudents()`。分页前排序，学生下拉框禁止按使用频率重排；成绩排名、座位位置、日期分组和值日公平性优先级保留。
- **不保存消息原文**：`interaction_analyses` 只保存结构化结果、置信度、澄清问题与 proposal 关联。
- **班级 Agent 记忆**：长期作息、偏好和约定经 `memory.upsert`/`memory.forget` 提案确认后保存在 `class_agent_memories`（`app/services/agent_memory.py`，每班上限 100 条），只存简短事实，不存原文、凭据或学生敏感档案。作息必须带起止时间与适用星期；临时调整另存带 `valid_from`/`valid_to` 的同名条目，有效期内覆盖长期规则、到期自动恢复，不能用 `memory_id` 改写长期规则的有效期。名称/别名重叠且范围冲突报 `MEMORY_CONFLICT`，内容未变报 `MEMORY_UNCHANGED`，预览后状态变化报 `MEMORY_STALE`。插件 `before_prompt_build` 钩子每轮注入本班已确认记忆快照，读取失败时必须说明而不是沿用旧值。
- **软删除**：学生、班级、学生事件使用 `SoftDeleteMixin`，查询需过滤 `deleted_at`。
- **时间**：一律使用 `app.utils.time.now()`（时区感知），不要直接用 `datetime.now()`。
- **幂等与事务**：批量写入（成绩、作业状态等）要求整批事务校验、失败回滚；未交事件等同步操作要求幂等。
- **附件**：文件名由 UUID 生成，单附件上限默认 20 MiB（`CLASSCLAW_MAX_ATTACHMENT_BYTES`）。
- **审计**：账户、删除、智能体绑定等关键动作通过 `app/services/common.py` 的 `audit()` 写轻量审计，不保存整份前后快照。
- **并发规模**：实现面向低并发、几十名学生规模；班级级分析使用批量查询，避免按学生逐条访问数据库。

## 配置与安全注意事项

配置全部经环境变量（`CLASSCLAW_*` 前缀），示例见 `.env.example`：

- `CLASSCLAW_API_TOKEN`：必须替换示例值为长随机令牌，并与 OpenClaw 插件配置一致。
- `CLASSCLAW_OPENCLAW_GATEWAY_TOKEN`：Gateway 管理员 Token，**只由后端读取，绝不能发送到浏览器**。
- 默认管理员账号由启动时创建（`CLASSCLAW_DEFAULT_ADMIN_USERNAME` / `CLASSCLAW_DEFAULT_ADMIN_PASSWORD`，示例 `admin`/`32767`，`must_change_password` 默认强制改密）。
- 会话以 SHA-256 哈希存储（`user_sessions.token_hash`），可撤销；密码哈希存储。
- `.env`、`data/`（含数据库与附件）属于敏感内容，不要提交或外发；`.gitignore` 已覆盖。

## 备份与恢复

```bash
python scripts/backup.py ./backups
# 停止服务并核对目录后：
python scripts/restore.py ./backups/classclaw-backup-YYYYMMDD-HHMMSS
python scripts/cleanup_attachments.py   # 默认只报告无引用附件，不自动删除
python scripts/cleanup_deletions.py     # 定向清理删除残留；默认只检查，--apply 才清理
```

备份用 SQLite Backup API 分别保存业务库和独立用量库，复制附件并写入清单；各库单独一致而非跨库原子快照。恢复会覆盖两个库和附件，必须先停服务；旧单库备份恢复后会重新迁移旧用量表。详见 `docs/database-splitting.md`。

## OpenClaw 集成

- 仓库自带插件 `integrations/openclaw/classclaw`（工具边界）与 `classclaw-manager` Skill（对话/分析流程边界）。插件只注册读、附件暂存、interaction analysis、proposal 和提交工具，不暴露创建班级的 mutation；新班级只允许从网页 onboarding 创建。
- 轻量提取 agent 默认启用（`CLASSCLAW_OPENCLAW_EXTRACTOR_ENABLED=true`，无需设置）：JSON 提取走自动创建的 `classclaw-extractor`（无工具、清洗规范在其工作区 AGENTS.md），创建失败自动回退主智能体与完整内联提示词；`CLASSCLAW_OPENCLAW_EXTRACTOR_ENABLED=false` 可关闭。
- 后端通过 Gateway `/tools/invoke` 验证插件可用（`classclaw_health`），用 `/v1/responses` 处理网页上传文件；连接失败时后端会阻止业务 API。
- 微信二维码 provider discovery 兼容层位于 `integrations/openclaw/openclaw-weixin-compat`。
- 微信兼容层 `.2` 为 ClassClaw 提供 Gateway 认证的私有网页登录端点：短轮询不销毁会话，新二维码同步返回网页，数字验证码通过本班 `/agent-binding/verify` 提交。`login_id` / `challenge_id` 隔离旧请求；二维码和验证码仅在内存中处理，禁止日志或持久存储。运行 `cd integrations/openclaw/openclaw-weixin-compat && npm test` 验证，官方消息收发与 CLI 登录不变
- 提取会话（后端分析调用）是一次性设计：唯一 `user` 键、无记忆、不复用（避免原文累积与污染），治理用 OpenClaw session 容量上限与 `openclaw sessions cleanup`（后端按 `CLASSCLAW_OPENCLAW_SESSION_CLEANUP_HOURS` 定时经 CLI 触发，另有 `/api/v1/admin/openclaw/sessions/cleanup` 手动端点）；聊天会话长期复用，不要清理。详见 `docs/openclaw-integration.md` §8。
- 安装与详细命令见 `docs/openclaw-integration.md` 和插件 README。

## 主要文档索引

- `docs/architecture.md`：分层与关键边界
- `docs/database.md`、`docs/database-data-architecture.md`：数据库设计
- `docs/database-splitting.md`：业务库与独立用量库拆分
- `docs/accounts-admin.md`：账户与管理员
- `docs/class-agent-onboarding.md`：班级专属智能体与微信绑定
- `docs/classclaw-channels.md`：统一渠道入口、OpenClaw 兼容层与多渠道扩展边界
- `docs/analytics.md`：分析口径
- `docs/configuration.md`：TOML 与环境变量配置分层
- `docs/deployment.md`：Ubuntu 同机部署与运维
- `docs/admin-console-redesign.md`：管理端信息架构与交互
- `docs/openclaw-integration.md`、`docs/openclaw-tools.md`：集成与 REST 映射
