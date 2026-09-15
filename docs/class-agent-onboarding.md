# 网页创建班级、专属智能体与微信绑定使用说明

## 最终工作方式

`main`/`classclaw` 是通用入口，不属于任何具体班级。每个正式班级对应一条 `class_agent_bindings` 记录、一个独立 OpenClaw agent 和一个独立 workspace；微信 channel account 是可选项。已绑定微信时，消息按 `(channel, accountId)` 路由到该班智能体，再由 ClassClaw 插件把绑定的 `class_id` 注入分析和写入预览。

新班级只能从网页 `/app/` 创建。OpenClaw 对话中没有创建班级或修改 onboarding 的工具；智能体收到创建请求时只会引导用户打开网页。

## 一次性安装

### 1. 安装依赖并升级数据库

```bash
cd /Users/wellon/classclaw
source .venv/bin/activate
pip install -r requirements.txt
alembic upgrade head
```

迁移 `0004` 会创建 `class_agent_bindings`。

### 2. 构建并重新加载 ClassClaw 插件

```bash
cd /Users/wellon/classclaw/integrations/openclaw/classclaw
npm install
npm run build
npm test
npm run plugin:validate

cd /Users/wellon/classclaw
openclaw plugins install --link ./integrations/openclaw/classclaw
openclaw plugins enable classclaw
```

插件现在只暴露既有班级的健康检查、附件、读取、交互清洗、写入预览、确认和取消工具，不再暴露 onboarding mutation。

### 3. 启用 OpenClaw 管理 RPC

ClassClaw 后端需要它来创建 agent 和写入 channel binding；二维码登录走下述微信兼容层的专用私有端点。管理接口权限很高，只应监听回环地址或可信私网，不要暴露到公网。

```bash
openclaw plugins enable admin-http-rpc
openclaw gateway restart
```

### 4. 安装腾讯微信 channel 插件

```bash
cd /Users/wellon/classclaw/integrations/openclaw/openclaw-weixin-compat
npm install --omit=peer --ignore-scripts
openclaw plugins install --link .
openclaw config set plugins.entries.openclaw-weixin.enabled true
openclaw gateway restart
```

当前固定的腾讯插件 `2.4.6` 缺少 OpenClaw 2026.7 的 provider discovery 声明，并且其 CLI 式登录在单次等待超时后删除会话、仅在终端显示刷新二维码、通过 stdin 读取数字验证码。仓库兼容层 `2.4.6-classclaw.2` 保留官方微信消息收发与 CLI 登录，另实现 ClassClaw 专用的网页登录状态机；不要只升级方法声明或直接移除兼容层。实现与测试见 [兼容层 README](../integrations/openclaw/openclaw-weixin-compat/README.md)。

每个班级的每次网页登录都有独立 `login_id`，仅在兼容层内存中存在，成功后才将微信账号标识写入班级绑定。二维码内容由后端编码为 PNG Data URL；轮询只携带登录标识，二维码刷新时立即返回新图片。出现手机数字验证时，账户设置会显示输入框，按原样输入数字（保留前导零），无需操作服务器终端。官方协议来源：<https://github.com/Tencent/openclaw-weixin>。

### 5. 配置 OpenClaw

```bash
openclaw config set plugins.entries.classclaw.config.baseUrl http://127.0.0.1:8000
openclaw config set plugins.entries.classclaw.config.apiToken '替换为-ClassClaw-API-Token'
openclaw config set tools.alsoAllow '["classclaw","classclaw_commit_write","classclaw_commit_writes"]' --strict-json
openclaw config set gateway.auth.mode token
openclaw config set gateway.auth.token '替换为-OpenClaw-Gateway-Token'
openclaw config set gateway.http.endpoints.responses.enabled true
openclaw config set session.dmScope per-account-channel-peer
openclaw config set plugins.entries.classclaw.config.timeoutMs 120000
openclaw gateway restart
```

`timeoutMs` 是班级智能体调用 `classclaw_analyze_interaction` 等工具的等待上限，必须不小于后端 TOML 的 `openclaw.timeout_seconds`；默认 30 秒会在分析较慢时把工具调用变成超时失败。

### 5.1 轻量提取智能体（默认开启，提升分析速度）

默认启用独立提取智能体（TOML 的 `openclaw.extractor_enabled=true`，无需额外设置）。后端所有 JSON 提取（interaction 清洗、onboarding 导入、课表/座位文件和事件分类）走一个专用的无工具智能体，默认名 `classclaw-extractor`（可用 `openclaw.extractor_agent_id` 修改）。服务启动和首次使用时会自动创建并校正它的独立 OpenClaw runtime：显式固定当前默认模型作为独立模型、开启 fast mode、关闭 thinking/reasoning/verbose、技能和记忆，并用 `minimal + deny session_status` 保证向模型暴露零个工具。它不会再继承 Main 的 coding 工具集或思考级别。

清洗规范自动写入其工作区 `data/openclaw-agents/_extractor/AGENTS.md`，内容哈希变化时自动覆写（手工修改会被下次规则更新覆盖），请求 prompt 只携带数据。设 `openclaw.extractor_enabled=false` 可回退主智能体与完整内联提示词。创建和运行配置校正依赖 5.3 节的 `admin-http-rpc`，未启用时自动回退。

ClassClaw 自动维护 `plugins.entries.classclaw.config.agentClasses` 和顶层 `bindings`，不要手工把多个班级指向同一个 agent 或微信 account。

## ClassClaw 静态配置与启动

Gateway 地址、Agent、班级工作区和微信 channel 配置在 `config/classclaw.toml` 的 `[openclaw]`、`[storage]` 与 `[wechat]`；`.env` 只保存与 ClassClaw 插件一致的 `CLASSCLAW_API_TOKEN` 和与 `gateway.auth.token` 一致的 `CLASSCLAW_OPENCLAW_GATEWAY_TOKEN`。字段和校验规则见 [静态配置说明](configuration.md)。

```bash
cd /Users/wellon/classclaw
source .venv/bin/activate
alembic upgrade head
python run.py
```

打开 <http://127.0.0.1:8000/app/>。

## 创建班级

1. 输入 ClassClaw API Token，点击“连接 OpenClaw”。页面必须同时通过 Gateway、ClassClaw 插件和 `admin-http-rpc` 检查。
2. 填写班级名称、年级、班主任、本班教室和学期。这些字段确定且一一对应，不经过模型重写。空白学期字段优先填入当天所在学期，寒暑假等非学期区间则填入未来最近的一学期：春季从农历元宵节至 7 月 1 日，秋季从 9 月 1 日至次年春节前 7 天；农历日期由后端离线换算，仍可人工修改。
3. 选择学生名单文件后点击“开始解析”。支持 XLSX/XLSM、DOCX、PPTX、CSV/TSV、PDF、图片、JSON/XML、RTF、Markdown 和其他文本文件；解析中按钮变为“取消解析”，取消后不会把稍后返回的结果写入草稿。
4. 在结构化名单表中核对、增删或修改行。当前步骤保存后才会解锁下一步；解析期间步骤导航保持锁定。
5. 选择课表/作息文件并手动开始解析。OpenClaw 只分析本次文件，结果完整替换旧课表；不会引用 onboarding 历史对话。
6. 核对课表矩阵。网页不再单独展示节次与节次名的对应列表；矩阵有明确名称时使用名称，否则按顺序显示“第一节、第二节、第三节”等。科目从当前课表的 `subject` 去重归纳；不设置科目满分。课程教室为空时使用本班教室。
7. 生成最终预览。后端检查班级必填项、学号、重复星期/节次、节次引用和课表格式。“创建班级”位于预览按钮右侧，仅在预览生成且检查通过后可点击，不再要求额外勾选核对框。
8. 勾选复核声明后确认创建，无需再次输入班级名称。确认后班级、学生、科目、节次和课表在同一事务中写入，同时建立 `pending_agent` 绑定记录。班级名称输入时会即时检查已有班级及旧式同名 OpenClaw agent；Agent 的资源名使用 `classclaw-{class_uuid}`，不再直接使用班级名称。
9. 页面先创建该班的独立 agent/workspace，并提供“绑定微信”和“暂不绑定微信”。创建时会覆盖 OpenClaw 默认模板，写入精简的 `AGENTS.md`、`SOUL.md`、`IDENTITY.md`、`TOOLS.md`、`USER.md` 与禁用心跳说明；展示名使用带短 ID 的 ClassClaw 助理名，不直接采用班级名。选择绑定时才显示二维码并自动轮询；检测成功便写入微信消息路由和 `agentClasses` 映射。选择暂不绑定时保留班级和智能体，之后仍可补绑。

座位表不属于 onboarding 必填内容，也不在创建草稿和最终 proposal 中。班级创建完成后，班主任可在独立的“座位表”页面手动设置或上传文件生成预览。

绑定成功时，后端还会为该 agent 限制为 ClassClaw 所需工具和单个 Skill，关闭记忆搜索、思考/推理输出，并限制 bootstrap 大小；新会话仍注入必要规则，连续会话跳过重复上下文，以减少 token 与响应时间。

微信凭据保存后，后端在同一次 `config.patch` 中写入班级消息路由并更新腾讯插件的 `channels.openclaw-weixin.channelConfigUpdatedAt`，触发微信监听重载。仅更新 `bindings` 不会启动新账号的消息监听，会等待后台健康检查补启动，表现为刚绑定后数分钟没有回复。重载标记不包含凭据，不替换其他账号或渠道设置。

若第 9 步失败，班级不会被删除。修复 OpenClaw/微信插件后点击“重新生成二维码”即可重试；后端会按 workspace 识别并接管已经创建成功但尚未来得及写回本地 ID 的 agent，不会重复创建。

## 彻底删除班级

网页危险区调用 `DELETE /api/v1/classes/{class_id}`。后端先移除 OpenClaw `agents.list` 中的班级 Agent、微信 `bindings` 路由和 ClassClaw `agentClasses` 映射，并安全删除该班独立 workspace；随后在一个数据库事务中删除班级全部业务数据。若 Gateway 配置清理失败，数据库班级保持不变，修复连接后重试即可。删除成功后原班主任账号恢复为无班级状态，可以重新进入 onboarding。

## 文件重新上传与人工修改规则

- 文件解析使用随机的新 OpenClaw session key，提示词不包含旧草稿列表。
- 文件选择与解析分开：选择后点击“开始解析”，进行中可取消。网页为每次任务发送随机 `X-ClassClaw-AI-Task-ID`，取消按钮另行调用取消端点；后端主动取消 HTTPX/OpenClaw 请求并释放写会话，不再只依赖连接断开检测，也不会应用稍后返回的结果。
- 每次 AI 解析必须返回 0 到 1 的置信度和非空原因项，统一通过门槛为 0.75。学生名单要求学号与姓名逐行清晰且唯一；课表要求星期、节次和科目明确。低置信度结果不修改草稿，网页弹窗逐项说明文件问题并要求重新上传。
- `students` 上传完整替换学生列表；`timetable` 上传完整替换 `periods` 与 `base_timetable`。
- 解析后只在 HTML 表格里编辑。PATCH 使用 `replace_lists=true`，删除一行就会从草稿删除。
- 人工结构化编辑不会再次经过模型。只有重新上传文件时 OpenClaw 才重新介入。
- “信任文件”表示允许解析和预填，不表示跳过最终复核。

## 日常使用

1. 班主任可在网页“班级 Agent”直接发送文字、使用浏览器语音转文字或上传文件，不需要先绑定微信。微信 account binding 则把微信消息送到同一个班级专属 agent。创建 Agent、微信绑定及重新绑定统一在“账户设置 → 班级 Agent · 绑定与设置”；对话页仅提供跳转入口。
2. 账户设置中的“模型设置”允许为本班选择主模型、网页图片理解模型和网页录音转写模型。主模型同时作用于网页与微信；图片和录音模型按网页入口隔离，避免一个班级改动 OpenClaw 全局媒体设置。只有 Gateway 当前可用的模型和已配置凭据的语音 Provider 才能保存。会话思考强度仍在每个对话内独立选择，不随管理入口迁移；选项由 Gateway 按本班当前模型解析，切换模型后自动刷新，仅保留受支持的会话选择，详情见[配置说明](configuration.md)。
3. Skill 要求附件先留证，非确定性输入调用 `classclaw_analyze_interaction`。网页附件已由后端保存并提供 attachment ids，Agent 直接使用这些 ids，不重复上传。
4. 插件从可信 `agentId` 查到固定 `class_id`，覆盖模型提供的班级参数。
5. 后端返回澄清问题或待复核 proposal；此时没有业务写入。
6. 用户在聊天中明确确认后，后端立即校验 proposal 的班级、revision、状态和有效期并执行；同组多条使用单事务批量写入，不再出现系统审批卡。

## 验证与排错

```bash
openclaw --version
openclaw plugins list
openclaw agents list --bindings
openclaw channels status --probe
curl -sS http://127.0.0.1:8000/health
```

- `admin-http-rpc 尚未启用`：启用 bundled plugin 并重启 Gateway。
- `web login provider is not available` 且插件显示 enabled：不要重复创建班级；按第 4 节链接 `openclaw-weixin-compat`，重启 Gateway 后在原页面点击“创建智能体并生成二维码”。
- `WECHAT_QR_UNAVAILABLE`：微信插件没有返回二维码内容，点击“重新生成二维码”；若持续出现，运行 `openclaw channels status --probe` 检查插件登录状态。
- 二维码过期：点击“重新生成二维码”，不要重复创建班级。
- 有微信 account alias 不代表已经扫码成功：只有 `status=linked` 且有账号标识才显示已绑定。账户设置保留绑定/重新生成按钮，不因 `awaiting_qr`、`failed` 或已分配 alias 而隐藏。
- 短轮询超时不会删除登录会话；总有效期由 `wechat.qr_binding_timeout_seconds` 控制，二维码最多自动刷新 3 次，数字验证最多尝试 5 次。过期/失败状态明确返回 `restart_required=true`，不再从供应商提示语猜测状态。
- `WECHAT_PLUGIN_UPDATE_REQUIRED`：后端找不到新版私有端点。等待现有对话结束后加载/重启 Gateway，再重启 ClassClaw 并强制刷新网页；旧登录标识失效后重新生成二维码。
- `WECHAT_LOGIN_STALE`：另一页或后续操作已经重新生成二维码。旧请求和旧验证码不能更新新登录；使用最新二维码，不要恢复旧页面的轮询。
- 修改后旧内容回来：确认页面没有 textarea；重新上传必须完整替换列表。
- `CLASS_SCOPE_VIOLATION`：当前智能体尝试确认不属于本班的 proposal，应取消并重新分析。
- Gateway 断开：先恢复 OpenClaw，不要关闭门禁绕过。

## 安全边界

- Gateway 管理 token 只存后端 `.env`，浏览器只接触 ClassClaw API token。
- ClassClaw 不保存微信登录凭据或二维码内容；凭据由微信 channel 插件保存在 OpenClaw。
- 数字验证码只在提交请求的内存中处理，不写业务库、浏览器持久存储或日志；验证码校验错误响应也不回显提交内容。私有 `/api/v1/classclaw/wechat-login` 必须通过 Gateway 认证，只接受已绑定班级的 start/wait/verify，不接受 URL、路径或任意 RPC。API 的 `/agent-binding/verify` 同样执行班级归属与微信功能开关校验。
- 账号凭据保存失败不报告成功；不会删除其他账号的凭据，也不能覆盖其他班级的微信路由。微信网络请求仅访问 HTTPS 微信域名，禁止跟随任意重定向。
- `admin-http-rpc` 只放在回环地址/可信私网。
- workspace/session 隔离不是数据库授权的替代；业务实体仍以 `class_id` 建索引/外键，proposal 确认还会校验班级归属。
