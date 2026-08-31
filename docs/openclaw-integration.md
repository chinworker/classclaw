# OpenClaw 集成与使用

## 合作方式

ClassClaw 同时使用 OpenClaw Plugin 和 Skill：

- Plugin 提供可调用工具、API 鉴权、附件路径限制和不可篡改的班级范围注入。
- Skill 规定既有班级智能体如何解析来源、评估置信度、消除歧义并展示复核摘要；创建班级只在网页完成。
- FastAPI 后端保存草稿和预览，并强制执行版本、有效期和白名单操作校验。后端是最终写入安全边界。
- OpenClaw 只在自然语言/文件分析、智能体管理和微信绑定时是前置依赖；确定性业务 API 在 Gateway 离线时仍可用。

只用 Skill 无法保证安全，因为提示词可能被误解；只用 Plugin 又缺少稳定的对话步骤。两者配合，再由后端强制确认，才能满足“所有写入一定与用户复核”。班级专属智能体、微信二维码和 `admin-http-rpc` 的完整配置见 [专属智能体与微信使用说明](class-agent-onboarding.md)。

## 1. 先配置 OpenClaw

先准备两个不同的随机长令牌：一个作为 OpenClaw Gateway Token，一个作为 ClassClaw API Token。Gateway Token 等同 OpenClaw 管理员凭据，只能放在 OpenClaw 和 ClassClaw 后端配置中，不能放进网页代码。

构建并安装插件：

```bash
cd /Users/wellon/classclaw/integrations/openclaw/classclaw
npm install
npm run build
npm test
npm run plugin:validate

cd /Users/wellon/classclaw
openclaw plugins install --link ./integrations/openclaw/classclaw
openclaw plugins enable classclaw
openclaw config set plugins.entries.classclaw.config.baseUrl http://127.0.0.1:8000
openclaw config set plugins.entries.classclaw.config.apiToken 'ClassClaw API Token'
openclaw config set tools.alsoAllow '["classclaw","classclaw_commit_write","classclaw_commit_writes"]' --strict-json
openclaw config set gateway.auth.mode token
openclaw config set gateway.auth.token 'OpenClaw Gateway Token'
openclaw config set gateway.http.endpoints.responses.enabled true
openclaw config set plugins.entries.classclaw.config.timeoutMs 120000
openclaw gateway restart
```

`/tools/invoke` 用于验证 ClassClaw 插件确实已加载，`/v1/responses` 用于处理网页上传文件。两者都保持在本机或可信内网，不要暴露到公网。`timeoutMs` 是智能体调用 `classclaw_analyze_interaction` 等工具的等待上限，必须不小于后端的 `CLASSCLAW_OPENCLAW_TIMEOUT_SECONDS`，否则分析较慢时工具调用会先超时失败。

JSON 提取默认走自动创建的轻量提取智能体（详见 [专属智能体与微信使用说明](class-agent-onboarding.md) §5.1）：首次分析时后端会通过 admin RPC 创建 `classclaw-extractor`（无工具），清洗规范自动写入 `data/openclaw-agents/_extractor/AGENTS.md`（内容哈希变化时自动覆写，手工修改会被下次版本更新覆盖）。因此 OpenClaw 侧必须启用 `admin-http-rpc`；未启用时后端自动回退主智能体。

## 2. 配置并启动 ClassClaw 后端

```bash
cd /Users/wellon/classclaw
python3.12 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

编辑 `.env`：

```dotenv
CLASSCLAW_API_TOKEN=与插件配置相同的-ClassClaw-API-Token
CLASSCLAW_OPENCLAW_GATEWAY_URL=http://127.0.0.1:18789
CLASSCLAW_OPENCLAW_GATEWAY_TOKEN=与-OpenClaw-gateway.auth.token-相同
CLASSCLAW_OPENCLAW_AGENT_ID=main
```

然后启动：

```bash
alembic upgrade head
python run.py
```

如果 OpenClaw 需要把本地附件上传到 ClassClaw，再设置允许目录：

```bash
openclaw config set plugins.entries.classclaw.config.allowedUploadRoots '["/绝对路径/到/OpenClaw媒体目录","/绝对路径/到/教师导入目录"]' --strict-json
```

不要使用过宽的根目录。插件会解析真实路径并拒绝符号链接逃逸。最后检查：

```bash
openclaw plugins list
openclaw skills list
openclaw doctor
```

## 3. 导入日常信息

把文字、图片、PDF、表格、音频转写或其他 OpenClaw 可读取的材料直接发给智能体，并明确目标。例如：

> 读取我上传的文件，把其中今天的考勤写入 ClassClaw。保留来源，列出无法确定的姓名或日期，先给我完整预览，我明确确认以后再写入。

标准流程是：

1. OpenClaw 收到原始文字或微信消息；附件先用 `classclaw_upload_file` 保存原件和哈希。
2. 调用 `classclaw_analyze_interaction`，传入未经删改的原文、`channel`、稳定消息 id、已知 `class_id` 和附件 ids。
3. 后端创建 `interaction_analysis`，并通过 Gateway Responses API 让 OpenClaw 在只读上下文中完成意图识别、姓名/班级匹配和字段清洗。
4. 如果同名、日期、科目、分数、考勤时段或批量范围不明确，返回 `needs_clarification` 和问题；此时 proposal 数量为零。
5. 如果可确定，后端用 Pydantic schema 校验每个 operation，生成一个或多个 `pending_review` proposal；此时仍未改变业务表。
6. OpenClaw 展示对象、日期、关键字段、数量和实质性警告。用户修改信息时要重新分析或重建 proposal，不能确认旧版本。
7. 用户在聊天中明确回复“确认”“可以”“写入”“都确认”等肯定意思后，单条调用 `classclaw_commit_write`，同组多条调用 `classclaw_commit_writes`。聊天复核是唯一审批，不再弹出系统审批卡。
8. 后端再次校验 revision、状态、有效期和班级归属；批量确认在一个事务中执行，任一条失败则全部回滚。

`classclaw_register_message` 和 `classclaw_propose_write` 保留给兼容流程及已经确定的结构化操作。微信、自然语言、粘贴名单、OCR 和附件不得用它们绕过 `classclaw_analyze_interaction`。

### 微信接入

ClassClaw 不直接连接微信；微信连接器属于 OpenClaw。只要微信消息被路由到启用了 `classclaw-manager` Skill 的 OpenClaw agent，Skill 会走统一分析工具。建议给该 agent 固定以下系统业务约束：

> 所有来自微信的 ClassClaw 写入请求必须保留原始消息 id，并调用 classclaw_analyze_interaction。需要澄清时先提问；返回 proposal 后展示简短完整预览；用户在聊天中明确确认后立即提交，不再请求第二次审批。

可用以下消息做验收：

1. 发“张三今天上午迟到”。预期：如果未指定班级或有同名，OpenClaw 先追问。
2. 补充班级或学号。预期：返回考勤 proposal，数据库考勤表尚未变化。
3. 回复“确认”。预期：智能体直接调用 `classclaw_commit_write`，成功后新增考勤记录；不出现审批卡。
4. 重放同一个微信消息 id。预期：返回原 analysis/proposal，不重复生成业务记录。

用户修改任一字段时，不应确认旧 proposal，而应取消并重新生成预览。

## 4. OpenClaw 对话不能创建班级

`main`/`classclaw` 是系统通用智能体，班级专属智能体只负责自己的既有班级。ClassClaw 插件不再暴露 onboarding mutation，也不接受 `class.create` proposal。用户在任何 OpenClaw 对话中提出创建班级时，智能体必须引导其打开 `http://127.0.0.1:8000/app/`。

## 5. 通过网页端创建班级

启动后端后打开 [http://127.0.0.1:8000/app/](http://127.0.0.1:8000/app/)。输入 ClassClaw API Token，第一步点击“连接 OpenClaw”。页面会同时验证 Gateway 在线、Gateway Token 正确且 ClassClaw 插件工具可用；全部通过后才解锁“开始新建”和后续表单。

学生名单和课表步骤以文件上传为默认且唯一的非确定性输入方式。文件先保存原件与 SHA-256，再通过 OpenClaw Responses API 分析；支持 Markdown、XLSX/XLSM、DOCX、PPTX、CSV/TSV、PDF、图片、JSON/XML、RTF 和文本。每次文件解析使用全新 OpenClaw session key，不发送旧列表，并以 `replace_lists=true` 完整替换目标草稿。

网页输入按确定性分流：

- 班级名称、年级、班主任、教室、学期和日期是与字段一一对应的确定性表单值，可以直接保存到 onboarding 草稿。
- 页面不再提供 textarea。解析结果显示为学生、节次和课表 HTML 表格；人工修改格式合规后直接保存，不再交给 OpenClaw 二次改写。
- 科目从当前基础课表自动归纳，不设置 onboarding 科目满分；空教室默认本班教室。
- 最终必须生成预览、勾选复核声明、再次输入班级名称并确认，才会落库。

页面显示网页创建 session ID，但 OpenClaw 智能体不能修改这个 onboarding session。

网页端复用同一 API，而不是建立第二套草稿：

```text
POST  /api/v1/class-onboarding/sessions
GET   /api/v1/class-onboarding/sessions/{session_id}
PATCH /api/v1/class-onboarding/sessions/{session_id}
POST  /api/v1/class-onboarding/sessions/{session_id}/files
POST  /api/v1/class-onboarding/sessions/{session_id}/preview
POST  /api/v1/write-proposals/{proposal_id}/confirm
POST  /api/v1/classes/{class_id}/agent-binding/start
POST  /api/v1/classes/{class_id}/agent-binding/provision
POST  /api/v1/classes/{class_id}/agent-binding/wait
```

连接检查为 `GET /api/v1/openclaw/status?refresh=true`。这是唯一不受 OpenClaw 连接门禁影响的 API；其他读写请求在连接失效时返回 `OPENCLAW_CONNECTION_REQUIRED`。

每次 PATCH 都带上当前 `expected_revision`。收到 revision 冲突时先重新 GET。班级确认后，网页先创建独立 OpenClaw agent/workspace；班主任可跳过微信，或选择显示 QR，并在扫码成功后自动写入 `(openclaw-weixin, accountId) -> agentId` binding。完整安装与排错见 [专属智能体与微信使用说明](class-agent-onboarding.md)。

## 支持范围与原则

当前 proposal 白名单覆盖班级/学生、座位、考勤、作业、学生事件、考试/成绩、调课、安排和值日确认。后续新写操作必须同时增加 Pydantic 校验、预览摘要、固定执行器、测试和 Skill 文档，不能增加“任意接口调用”工具。

OpenClaw 能可靠读取的任何类型都可以进入流程。对于加密文件、损坏文件或缺少解析器的专有格式，正确行为是保留原附件并请用户提供密码或导出为 PDF、图片、CSV/XLSX、音频或文本，而不是猜测内容。

管理员控制台通过 OpenClaw 原生 `models.list`、`usage.cost`、`logs.tail` 和白名单配置写入接口提供专业运维能力。当前 `admin-http-rpc` 不放行 `sessions.usage`，因此后端从 `CLASSCLAW_OPENCLAW_STATE_DIR` 只读提取各 Agent transcript 的时间、role、duration、usage 和 stopReason 元数据，用于调用次数与响应耗时统计；消息正文被忽略，也不会写入 ClassClaw 数据库或日志。没有有效耗时字段时保留为空，不用零值代替。Gateway Token 始终只由后端读取。

## 6. 统一分析 API（供其他网页页面或渠道使用）

其他网页模块如果以后增加“随手记”“粘贴通知”“批量导入”等自由输入，不应直接调用业务写接口，而应调用：

```http
POST /api/v1/interaction-analyses
Authorization: Bearer <ClassClaw API Token>
Content-Type: application/json
```

```json
{
  "channel": "web",
  "external_message_id": "web-生成的稳定UUID",
  "sender_id": "teacher-1",
  "text": "把张三今天上午记为迟到",
  "attachment_ids": [],
  "class_id": "班级UUID",
  "requested_by": "李老师",
  "idempotency_key": "web:稳定UUID"
}
```

返回状态：

- `needs_clarification`：显示 `questions_json`，收集回答后以新 idempotency key 再分析；不得自行补值。
- `no_action`：只是查询/闲聊或没有写入意图，不产生 proposal。
- `awaiting_review`：显示返回的 `proposals[].preview_json`；用户确认单条时调用 confirm，确认同组多条时调用 `/write-proposals/confirm-batch`。
- `failed`：显示安全错误并保留原输入；不要无提示自动提交或降级直写。

可以用 `GET /api/v1/interaction-analyses/{id}` 查看结构化分析结果。`interaction_analyses` 不保存消息原文，只保存分析状态、摘要、置信度、问题、警告和 proposal 关联。

## 7. 故障排查

- 网页一直“未连接”：检查 Gateway `/health`、Gateway Token、插件是否启用，以及 `classclaw_health` 是否在工具 allowlist。
- 文件/文本返回 404：通常是 Responses API 未启用，确认 `gateway.http.endpoints.responses.enabled=true` 并重启 Gateway。
- 返回 `OPENCLAW_CONNECTION_REQUIRED`：这是预期的强制门禁；不要关闭门禁绕过，先恢复 OpenClaw。
- 返回 `needs_clarification`：不是失败。把问题展示给用户，补充信息后重新分析。
- proposal 无法确认：检查是否过期、revision 是否变化、onboarding 草稿是否在预览后修改；重新生成预览。
- 微信重复执行：确保传稳定 `external_message_id` 和 `idempotency_key`，并在新澄清轮次使用新的 key。
- session 越积越多：见下节「Session 治理」。

## 8. Session 治理（一次性分析会话的清理）

ClassClaw 在 OpenClaw 中产生三类 session：

1. **提取会话（一次性，会累积）**：每次后端分析调用都用唯一 `user` 键（`classclaw-onboarding-import-*`、`classclaw-timetable-import-*`、`classclaw-seating-import-*`、`classclaw-duty-rule-*`、`classclaw-event-*`、`classclaw-interaction-*`），在 Responses 端点各生成一个新 session。这是刻意的「无记忆提取」设计：不复用历史可避免跨文件污染，也避免把学生名单等原始文本累积进会话存储。
2. **聊天会话（长期复用，不要清理）**：微信/网页对话按 `session.dmScope: per-account-channel-peer` 每个会话长期保留，是智能体上下文的来源。
3. **提醒定时任务**：插件用 `--session isolated --delete-after-run` 创建，执行完自动删除。

提取会话**不应复用**：复用会把原始输入累积进会话历史（违背「不保存消息原文」原则），并让每次调用的上下文随历史增长而变慢。前缀缓存收益不依赖会话复用——静态规范已冻结在 extractor 的 `AGENTS.md`，provider 端前缀缓存跨会话即可命中。

清理依赖 OpenClaw 内建机制（`admin-http-rpc` 白名单不含 `sessions.*`，后端无法代删）：

```bash
# 预览将被清理的会话
openclaw sessions cleanup --dry-run
# 按容量上限立即归档淘汰（超出的最旧会话压缩归档后删除）
openclaw sessions cleanup --enforce
```

建议为主智能体与 `classclaw-extractor` 配置容量上限（如 `maxDiskBytes` 调到 `1gb`、`maxEntries` 设数百条），并让空闲提取会话按 `lastInteractionAt` 的空闲/每日重置策略自然退役；已归档和置顶（pinned）的会话不受自动清理影响。需要立即回收磁盘时，用主机 cron 每日执行一次 `openclaw sessions cleanup --enforce`。聊天会话与归档会话不会被上述策略误删。
