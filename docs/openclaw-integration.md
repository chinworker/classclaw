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
cd integrations/openclaw/classclaw
npm install
npm run build
npm test
npm run plugin:validate

cd <项目根目录>
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

`/tools/invoke` 用于验证 ClassClaw 插件确实已加载，`/v1/responses` 用于处理网页上传文件。两者都保持在本机或可信内网，不要暴露到公网。`timeoutMs` 是智能体调用 `classclaw_analyze_interaction` 等工具的等待上限，默认 120000 毫秒，必须不小于 ClassClaw TOML 的 `openclaw.timeout_seconds`，否则分析较慢时工具调用会先超时失败。网页整轮另有 120 秒上限，分析需要的读操作应保持最少；分析超时或返回进行中时结束本轮，不反复请求或轮询。

网页“班级 Agent”同样由 ClassClaw 后端代理到 `/v1/responses`，后端根据班级绑定选择 `openclaw/<class-agent-id>`，Gateway Token 永不发送到浏览器。每个“新对话”使用独立稳定的会话键，连续消息保留上下文。语音由浏览器先转成可编辑文字；文件先保存为本班附件，再以已保存的 attachment ids 和文件内容交给 Agent，禁止重复上传。

`GET /api/v1/classes/{class_id}/agent-chat/thinking` 经登录和班级归属校验后，只投影 Gateway `agents.list` 中本班 Agent 的当前模型、`thinkingLevels` 和有效默认档位，并禁止缓存。网页据此生成本会话思考选项，二态模型的 `on` 标签显示为“开启”，请求仍发送原 ID。发送前网页刷新、后端复核，防止模型切换后继续提交旧档位；能力读取失败不会回退到写死的档位表。私有会话写端点仍只接收已绑定班级网页 `key` 和 `thinkingLevel`，无新增 Gateway 写入口或全局配置修改。

“账户设置 → 班级 Agent · 绑定与设置”提供班级独立模型设置，“账户设置 → ClassClaw Channels”展示内置网页 Chat 并管理外部渠道连接（当前开放微信），对话页仅保留会话级思考设置。主模型写入该班 `agents.list` runtime，网页 Chat 和外部渠道共用；图片模型只在该班网页图片消息上通过受控 `x-openclaw-model` 覆盖生效，避免修改全局 `agents.defaults.imageModel` 而影响其他班；语音识别模型使用 `provider/model` 调用 `openclaw infer audio transcribe`。浏览器录音只写入临时文件，转写结束后立即删除，文字仍需在输入框复核后发送。未选择服务端 STT 时继续使用 Web Speech API，不上传录音。候选来自 Gateway `models.list` 和 OpenClaw 音频 Provider 目录；不可用的主/图片模型及未配置凭据的 STT Provider 会被后端拒绝。

JSON 提取默认走自动创建的轻量提取智能体（详见 [专属智能体与微信使用说明](class-agent-onboarding.md) §5.1）：服务启动和首次分析时，后端会通过 admin RPC 创建并校正 `classclaw-extractor` 的独立 runtime。该 runtime 显式保存模型、开启 fast mode，按 `openclaw.extractor_thinking` 设置思考档位（默认 `off`，仅在配置变化时重启 Gateway），并关闭推理、记忆与技能，以 `minimal` profile 加 `deny: [session_status]` 将可调用工具降为零；不会继承 Main 的 coding profile。清洗规范自动写入 `data/openclaw-agents/_extractor/AGENTS.md`。管理员在 Agent Studio 保存的文件会记录为自定义内容，后续自动检查不会覆盖；需要跟随 ClassClaw 新默认规则时，可在管理端恢复系统默认。因此 OpenClaw 侧必须启用 `admin-http-rpc`；未启用时后端自动回退主智能体。

回合守卫以 Agent、会话和 run ID 隔离，在进程内跨插件注册实例共享有时效的计数和参数哈希，防止嵌套提取加载插件后丢失状态。单条/批量提交失败会返回不可自动重试的结构化错误，本轮不允许重新分析、替换预览或重复提交；新预览仍需重新复核。班级默认工作区要求省略工具前自述，工具结束后再给结果。批量提取的主要等待在模型生成，优化方向（如短学生引用、更低延迟模型）需先做协议与质量验证，不得静默替换用户选择的模型或思考档位。

## 2. 配置并启动 ClassClaw 后端

```bash
cd <项目根目录>
python3.12 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp config/classclaw.example.toml config/classclaw.toml
cp .env.example .env
```

在 `config/classclaw.toml` 的 `[openclaw]`、`[storage]` 和 `[wechat]` 中配置 Gateway 地址、Agent ID、工作区/状态目录及微信 channel；在 `.env` 中只填写密钥：

```dotenv
CLASSCLAW_API_TOKEN=与插件配置相同的-ClassClaw-API-Token
CLASSCLAW_OPENCLAW_GATEWAY_TOKEN=与-OpenClaw-gateway.auth.token-相同
CLASSCLAW_DEFAULT_ADMIN_PASSWORD=首次管理员强密码
```

然后启动：

```bash
python scripts/check_config.py --config config/classclaw.toml
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
3. 后端创建 `interaction_analysis`，并通过 Gateway Responses API 让 OpenClaw 在只读上下文中完成意图识别、学生按班内学号匹配和字段清洗。上下文名单只含学号、姓名、性别和状态，不携带学生 UUID；分析输出的学生引用（`student_no`/`student_nos`，座位 layout 同）由后端在绑定班级内确定性解析为内部 UUID，跨班或不存在的引用直接拒绝。
4. 所有分析顶层及每项数据都必须返回 `confidence` 和非空 `reasons`，统一门槛为 0.75。低于门槛，或同名、日期、科目、分数、考勤时段、批量范围等不明确时，返回 `needs_clarification`、具体 `rejected_reasons` 和问题；低置信度数据不会生成 proposal。
5. 如果可确定，后端用 Pydantic schema 校验每个 operation，生成一个或多个 `pending_review` proposal；此时仍未改变业务表。
6. OpenClaw 展示对象、日期、关键字段、数量和实质性警告。用户修改信息时要重新分析或重建 proposal，不能确认旧版本。
7. 用户在聊天中明确回复“确认”“可以”“写入”“都确认”等肯定意思后，单条调用 `classclaw_commit_write`，同组多条调用 `classclaw_commit_writes`。聊天复核是唯一审批，不再弹出系统审批卡。
8. 后端再次校验 revision、状态、有效期和班级归属；批量确认在一个事务中执行，任一条失败则全部回滚。

`classclaw_register_message` 和 `classclaw_propose_write` 保留给兼容流程及已经确定的结构化操作。微信、自然语言、粘贴名单、OCR 和附件不得用它们绕过 `classclaw_analyze_interaction`。

班级档案批量补充直接分析原文，上下文提供完整名单及现有 `gender`、`status`（按学号排序，不含内部 UUID）。相同变更用 `student.update.batch` 分组，按 `student_nos` 引用学生，例如按学号数值分别生成男女两组，仅补空值须带 `only_if_empty:["gender"]`。预览包含完整名单和变更字段旧值，确认时重新校验；任一组档案已变更则整批回滚。这样避免逐人生成大量 operation 导致输出截断或超过单次 10 个预览的限制。

`classclaw_read` 的名单分页参数为 `page`、`page_size`，必须传到 REST 接口；分析查询返回 `{analysis, proposals}`，归属从 `analysis.class_id` 校验。分析取消会标记 `failed`，不会遗留永久 `analyzing`。工具分析错误保留真实 `analysis_id`（若已返回），并明确禁止本轮自动重试和绕过分析写入。

会话思考档位被模型拒绝时返回 `CHAT_THINKING_UNSUPPORTED` 和过滤后的 `supported_levels`，提示用户选择可用档位；其他会话设置失败返回 `CHAT_THINKING_UNAVAILABLE`。错误日志仅记录档位、错误分类和 Gateway 状态码，不记录消息、Token 或完整会话内容。

Gateway 拒绝信息中的可用档位可能是显示标签：例如本地 Kimi provider 2026.7.1 将 `low` 显示为 `on`，只支持 `off/low`（关闭/开启），不支持 `minimal`。错误解析必须把 `on` 还原为 `low` 并保留“开启”标签，不能丢掉这一可用选项；具体档位以安装版本的 provider profile 为准。

### ClassClaw Channels 接入

ClassClaw Channels 是网页 Chat 与外部渠道的统一入口。网页 Chat 默认启用，通过现有 Chat API 和 OpenClaw Responses/SSE 接入；微信通过 OpenClaw Channels 与兼容层接入同一班级 Agent。当前两者可同时使用，其他外部渠道及多个外部账号绑定后续开放。当前单外部账号存储的迁移、连接隔离、路由保留与删除要求见 [ClassClaw Channels](classclaw-channels.md)。

ClassClaw 不直接连接微信；微信连接器属于 OpenClaw。只要微信消息被路由到启用了 `classclaw-manager` Skill 的 OpenClaw agent，Skill 会走统一分析工具。建议给该 agent 固定以下系统业务约束：

> 所有来自微信的 ClassClaw 写入请求必须保留原始消息 id，并调用 classclaw_analyze_interaction。需要澄清时先提问；返回 proposal 后展示简短完整预览；用户在聊天中明确确认后立即提交，不再请求第二次审批。

可用以下消息做验收：

1. 发“张三今天上午迟到”。预期：如果未指定班级或有同名，OpenClaw 先追问。
2. 补充班级或学号。预期：返回考勤 proposal，数据库考勤表尚未变化。
3. 回复“确认”。预期：智能体直接调用 `classclaw_commit_write`，成功后新增考勤记录；不出现审批卡。
4. 重放同一个微信消息 id。预期：返回原 analysis/proposal，不重复生成业务记录。

用户修改任一字段时，不应确认旧 proposal，而应取消并重新生成预览。

### 班级删除与微信清理

兼容层 `.4` 的取消响应包含 `accountIds`：覆盖已完成扫码保存、但 ClassClaw 尚未写入路由的账号。后端先将这些 ID 存入删除记录，再核对归属并登出；缺少该字段时拒绝继续删除。原始微信账号别名与规范化 ID 按同一账号检查共享引用。

班级删除会先停止该班的活动再删业务数据，依赖兼容层与 Gateway 的以下能力：

1. 兼容层 `2.4.6-classclaw.4` 提供私有 `POST /api/v1/classclaw/wechat-login` 的 `cancel` 动作（按班级取消未完成扫码，并阻止该班再次发起登录）和 `channels.logout` 的 `gateway.logoutAccount` 实现（停用账号监控并删除 `accounts.json` 注册、账号 JSON、同步状态、上下文令牌和 `allowFrom` 文件）。
2. 后端在 `preflight` 阶段读取 `config.get` 的 `hash` 并核对归属，`gateway` 阶段依次执行 `cancel`、`channels.logout`、`tasks.list` 空闲校验，然后用 `baseHash` 提交 `config.patch` 并回读校验。
3. `agentClasses` 与频道账号必须用显式 `null` 删除；把它们放进 `replacePaths` 不会生效（该字段只替换数组）。
4. 共享微信账号、越界路径、配置版本冲突和未结束的 `tasks.list` 任务都会拒绝删除并保留可重试记录；升级后端前必须先更新兼容层，否则删除会停在 `WECHAT_PLUGIN_UPDATE_REQUIRED`，业务数据尚未删除。

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

网页中所有会调用 OpenClaw 做文件识别、自然语言分析或结构化生成的入口统一使用“AI 按钮”。任务运行时按钮显示跑马灯边框和精确到 0.1 秒的耗时；再次点击可取消请求，取消或完成后立即停止计时。普通数据库查询、保存和确定性分析不使用该样式。

每次 AI 请求都带随机 `X-ClassClaw-AI-Task-ID`。按钮取消或前端超时时，网页会在终止原请求之外调用 `POST /api/v1/ai/tasks/{task_id}/cancel`；后端以当前登录主体校验任务归属，设置进程内取消事件，并取消正在执行的 HTTPX 请求。OpenClaw Responses API 会在上游连接关闭时中止对应 run。取消端点只改变进程内任务状态，特意使用只读数据库会话，避免被原 AI POST 长时间占用的单写者锁阻塞。这个机制依赖项目规定的单进程、单 Uvicorn worker；改为多 worker 前必须换成跨进程任务注册表。

班级对话页的“停止”按钮也使用同一取消机制，只停止选中对话的当前请求。新建对话会立即加入列表，可查看处理中、已回复、失败和未读回复状态。每个对话保留独立 UUID、消息、输入草稿、待发送附件与请求控制器；同一对话同时只允许一个请求，不同对话可以分别等待，乱序回复只写回原对话。

对话列表每项提供“删除”，确认后移除本标签页内存中的消息、草稿与待发送附件，并取消该对话的在途请求；迟到的能力检查、流式片段和最终回复不能恢复已删除内容。删除当前对话后切到相邻对话，删除最后一项后自动新建空白对话；其他对话不受影响。离开页面或退出登录会关闭尚未确认的删除弹窗。此操作不撤销已保存的业务数据、不删除已上传附件，也不删除 Gateway 保存的会话历史。

网页聊天 POST 支持 multipart 字段 `stream=true`（网页默认使用）及可选 `thinking_level`。省略思考强度时使用 `openclaw.class_agent_thinking`；显式设置仅属于本会话，详见 [配置说明](configuration.md)。后端生成 `agent:{agent_id}:openresponses-user:classclaw-web-chat:{class_id}:{sender_id}:{conversation_id}`，通过 `x-openclaw-session-key` 精确关联会话；客户端不能自行指定 Gateway key。

当前 Gateway 的 Responses `reasoning` 字段未传给执行器，管理员 HTTP RPC 也不放行 `sessions.patch`。因此 ClassClaw 插件提供固定、Gateway 鉴权的私有 `POST /api/v1/classclaw/web-session-thinking`：仅接受 `key` 和 `thinkingLevel`，校验 key 是已绑定班级的网页会话后固定分派 `sessions.patch`；不开放会话删除、任意 RPC 或任意 Agent 配置。浏览器只调用 ClassClaw，绝不获取 Gateway Token。更新版本后须重新构建插件，并在无进行中对话时重启 Gateway 和 ClassClaw；缺少新插件端点会返回 `CHAT_PLUGIN_UPDATE_REQUIRED`。

流式响应为 SSE：`delta` 帧使用统一成功 envelope，`data.text` 为回复正文片段；`thinking` 帧的 `data` 为 `{state:"started",level}` 或 `{state:"delta",text}`，与正文分开显示；`done` 的 `data` 与旧 JSON 回复一致；`error` 使用统一错误 envelope 和 `request_id`。心跳仅使用 SSE 注释，不转发工具参数。仅收到完整 `response.completed` 才算完成，断流/超时保留并标记部分回复与思考内容，不能据此声称业务已执行。旧客户端不传 `stream` 仍返回 JSON。取消和超时覆盖整个响应体读取周期，任务归属与站内切页行为不变。流式总耗时与首片段耗时日志不含消息或思考原文。

[Gateway 标准 Responses 流](https://docs.openclaw.ai/gateway/openresponses-http-api) 不提供独立思考事件。ClassClaw 插件增加 Gateway 鉴权的只读 `POST /api/v1/classclaw/web-chat-reasoning`，只接受后端生成的已绑定班级网页会话 `key`；订阅 `runtime.events.onAgentEvent`，仅投影该会话的 `thinking` 文本和生命周期结束信号。后端在发起 Responses 前建立订阅，再用 `response.created` 的响应 ID 匹配具体 run，排除上一轮/其他运行；浏览器拿不到 Gateway key、run ID、工具事件或管理 Token。该端点不修改 `reasoningDefault`、会话或 Agent 配置，也不注册模型工具。

思考订阅不落库、不写日志；只维护连接期间的文本边界，限制体积、背压和最长 180 秒生命周期。主请求完成/取消时关闭订阅，慢消费者或思考流中断不影响正文完成判定。缺少端点、旧 Gateway 不提供事件或模型不返回思考文本时，网页保留计时并明确显示无可展示内容。部署此功能须重新构建 ClassClaw 插件并在无进行中对话时重启 Gateway、ClassClaw。

插件将工具执行结果与业务状态分离：`details={success:true,data:...}`，读取/取消一个 `cancelled` 预览不再被误判为执行失败。发送给模型的结果删除预览中重复的原始/归一化 payload，并保留完整预览、ID、revision、有效期及澄清证据；原数据库记录不变，不自动清除历史会话或未确认预览。

工具读取按 resource 校验必填 ID。可信 runId 内通过参数摘要去重，最多允许 24 次不同的 ClassClaw 工具调用；新用户轮次独立，成功写入/取消后允许重新读取。明确提交失败或需要澄清的分析禁止在同轮重建并绕过复核提交。失败/进行中的同消息幂等分析返回 409，不当作成功重复返回，也不重新调用模型。规则仍保留后端的整批事务、班级隔离和一次性确认边界。

班级运行配置同时启用 Gateway 原生 `tools.loopDetection`，对无进展重复和工具来回调用进行告警/阻断，避免模型不断消耗被插件拒绝的调用轮次。安全检测阈值为 warning=3、critical=6、globalCircuitBreaker=12（不是总工具调用次数），不影响正常批量领域接口。

对话请求由 `web/js/agentChatStore.js` 按登录用户和班级隔离管理，与页面 DOM 生命周期分离。新建/切换对话或前往站内其他页面不会取消已发送请求；返回后恢复原对话的消息、草稿与处理中状态，不重复发送请求。页面卸载只清理订阅、麦克风、尚未提交为消息的语音转写和二维码轮询；后台完成不会抢占其他页面的输入焦点。退出登录/切换身份会清空内存对话并取消未完成请求，取消请求使用原任务所属凭据，迟到的旧身份 401 不会退出新身份。

此处“离开页面”指同一浏览器标签页内的站内路由切换，不包括刷新、关闭标签页、跳转外站或断网。主动停止、退出登录、请求超时和连接断开仍会走取消机制；没有引入脱离浏览器的后台任务或消息队列。ClassClaw 数据库不保存网页聊天原文；只保存用户明确上传的附件，以及 Agent 按既有流程产生的结构化 analysis/proposal。对话列表和消息只保留在当前标签页内存，不写入 localStorage/sessionStorage，刷新后不从数据库恢复。

聊天、文件/文本提取和语音转写在外部 I/O 期间会归还 SQLite 单写者锁与连接，Agent 工具回调可以正常执行分析、预览和用户确认后的提交；响应落库前会重新取得写锁，并重新读取可能变化的草稿版本。微信二维码生成与等待同样不跨轮询持有数据库写锁。

Responses 读取/连接超时返回 `504 OPENCLAW_TIMEOUT`，连接失败返回 `502 OPENCLAW_CONNECTION_FAILED`，响应无法解析返回 `502 OPENCLAW_INVALID_RESPONSE`。日志记录异常类型、耗时和请求编号，不记录请求正文或凭据；网页聊天显示可定位日志的问题编号。超时不能被当成“业务肯定未执行”，涉及写入时应先核对结果，不自动重试有副作用的聊天请求。语音转写被取消时会回收子进程和临时录音。

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

当前 proposal 白名单覆盖班级/学生、座位、考勤、作业、学生事件、考试/成绩、调课、安排、值日确认、班级 Agent 记忆（`memory.upsert`/`memory.forget`）以及教室终端（`classroom.broadcast.send`/`classroom.volume.set`）。后续新写操作必须同时增加 Pydantic 校验、预览摘要、固定执行器、测试和 Skill 文档，不能增加“任意接口调用”工具。

教室终端类操作有额外的物理副作用约束：预览阶段就冻结完整句子与顺序，确认后由 `approval._dispatch_classroom()` 在事务提交之后才推送命令，并把投递结果写进 `result_json.delivery`。因此 `completed` 只代表已登记下发，显示与播报结果只能依据终端回执；终端离线时确认整批回滚并返回 409 `DEVICE_OFFLINE`，不补播。点名广播不写考勤、值日或任务完成记录。详见 `docs/classroom-monitoring.md`。

### 班级 Agent 记忆

Agent 从对话中提炼可重复使用的作息（`schedule`，必须含起止时间和适用星期）、固定偏好（`preference`）和其他长期约定（`fact`），统一走分析 → 预览（展示记住/更正/忘记的具体变化）→ 确认流程，确认前不写入。`class_agent_memories` 按班隔离、上限 100 条，只保存简短事实，不保存消息原文、凭据或学生敏感档案；名称与别名做归一化匹配，重复事实合并到原条目（别名并入），完全相同则返回 `MEMORY_UNCHANGED` 不重复保存。“今天/本周临时调整”必须另存带 `valid_from`/`valid_to` 的同名条目：有效期内覆盖同名长期规则，到期后自动恢复长期规则，且不得用 `memory_id` 改写长期规则的有效期（`MEMORY_SCOPE_CONFLICT`）。更正需用 `memory_id` 明确目标，名称/别名与多条记忆范围重叠时拒绝并要求澄清（`MEMORY_CONFLICT`）；批量确认前冻结全部记忆预览，预览后记忆发生变化返回 `MEMORY_STALE`。

插件通过 `before_prompt_build` 钩子（`agentMemory.ts`，需启用 `hooks.allowConversationAccess`/`allowPromptInjection`，配置运行时已自动写入）在每轮对话前注入本班已确认记忆快照；快照不缓存，更正和忘记下一轮即生效，读取失败时提示改用 `classclaw_read` 的 `agent_memory` 复核，不沿用历史旧值。教师可在账户设置中查看本班已保存的记忆（含已过期标记），更正或忘记仍在对话中完成。

OpenClaw 能可靠读取的任何类型都可以进入流程。对于加密文件、损坏文件或缺少解析器的专有格式，正确行为是保留原附件并请用户提供密码或导出为 PDF、图片、CSV/XLSX、音频或文本，而不是猜测内容。

管理员控制台通过 OpenClaw 原生 `models.list`、`usage.cost`、`logs.tail` 和白名单配置写入接口提供专业运维能力。当前 `admin-http-rpc` 不放行 `sessions.usage`，因此后端从 TOML 的 `storage.openclaw_state_dir` 只读提取各 Agent transcript 的时间、role、duration、usage 和 stopReason 元数据，用于调用次数与响应耗时统计；消息正文被忽略，也不会写入 ClassClaw 数据库或日志。没有有效耗时字段时保留为空，不用零值代替。Gateway Token 始终只由后端读取。

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

- `needs_clarification`：先用自然语言逐项告知 `structured_json.rejected_reasons`，再显示 `questions_json`；收集回答后以新 idempotency key 再分析，不得自行补值。
- `no_action`：只是查询/闲聊或没有写入意图，不产生 proposal。
- `awaiting_review`：显示返回的高置信度 `proposals[].preview_json`；若同时存在 `rejected_reasons`，需告知哪些低置信度数据被排除及原因。用户确认单条时调用 confirm，确认同组多条时调用 `/write-proposals/confirm-batch`。
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

1. **提取会话（一次性，会累积）**：每次后端分析调用都用唯一 `user` 键（`classclaw-onboarding-import-*`、`classclaw-timetable-import-*`、`classclaw-seating-import-*`、`classclaw-event-*`、`classclaw-interaction-*`），在 Responses 端点各生成一个新 session。这是刻意的「无记忆提取」设计：不复用历史可避免跨文件污染，也避免把学生名单等原始文本累积进会话存储。
2. **聊天会话（长期复用，不要清理）**：微信按 channel/peer 隔离；网页按“账号 + 班级 + 新对话 ID”隔离。它们是智能体上下文的来源。
3. **提醒定时任务**：插件用 `--session isolated --delete-after-run` 创建，执行完自动删除。

提取会话**不应复用**：复用会把原始输入累积进会话历史（违背「不保存消息原文」原则），并让每次调用的上下文随历史增长而变慢。前缀缓存收益不依赖会话复用——静态规范已冻结在 extractor 的 `AGENTS.md`，provider 端前缀缓存跨会话即可命中。

清理依赖 OpenClaw 内建机制（`admin-http-rpc` 白名单不含 `sessions.*`，后端无法代删）：

```bash
# 预览将被清理的会话
openclaw sessions cleanup --dry-run
# 按容量上限立即归档淘汰（超出的最旧会话压缩归档后删除）
openclaw sessions cleanup --enforce
```

建议为主智能体与 `classclaw-extractor` 配置容量上限（如 `maxDiskBytes` 调到 `1gb`、`maxEntries` 设数百条），并让空闲提取会话按 `lastInteractionAt` 的空闲/每日重置策略自然退役；已归档和置顶（pinned）的会话不受自动清理影响。

### 8.1 后端自动清理（已内置）

由于 `admin-http-rpc` 白名单不含 `sessions.*`，后端通过 OpenClaw CLI 触发同一套内建清理机制：

- 启动后每 5 分钟检查一次节流窗口，按 TOML 的 `openclaw.session_cleanup_hours`（默认 24 小时）自动执行一次 `openclaw sessions cleanup --enforce`；Gateway 不在线时跳过且不计入窗口，`0` 可关闭；
- 管理员控制台可随时操作：
  - `GET /api/v1/admin/openclaw/sessions/cleanup`：查看开关、间隔与上次执行结果；
  - `POST /api/v1/admin/openclaw/sessions/cleanup?enforce=false`：预览（等价 `--dry-run`）；不带参数或 `enforce=true` 立即执行；
- 清理只影响超出容量/空闲策略的会话，聊天会话与归档会话不会被误删；CLI 二进制由 TOML 的 `openclaw.cli` 指定（默认 `openclaw`，需与 Gateway 同一主机/用户，能读取 `storage.openclaw_state_dir` 配置）。
