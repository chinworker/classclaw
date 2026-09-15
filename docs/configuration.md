# 静态配置说明

ClassClaw 的部署配置采用“非敏感 TOML + 密钥环境变量”两层结构。部署参数只在进程启动时读取；修改后应先校验，再重启 ClassClaw。OpenClaw 运行参数、各班级 Agent 的模型与提示词使用各自的网页配置入口，生效方式见下表。

## 管理端配置层级

| 一级分类 | 二级入口 | 覆盖内容 | 修改位置与生效方式 |
| --- | --- | --- | --- |
| ClassClaw 系统 | 系统配置 | 服务监听、SQLite、附件、运行时区、日志、登录有效期、缓存、初始化账号、功能开关、网页品牌与默认值 | `config/classclaw.toml` / 环境变量；校验后重启 ClassClaw |
| ClassClaw 系统 | 用户 / 班级 | 用户启停、密码重置、班级信息与负责人 | 网页保存；数据库立即生效 |
| ClassClaw 系统 | 备份与系统维护 | 服务器备份恢复说明、无引用附件检查、完整初始化 | 备份恢复用服务器脚本；完整初始化需在网页明确确认 |
| OpenClaw 配置与维护 | 连接与微信配置 | Gateway 地址与 Token 状态、超时、提取 Agent 开关与 ID、工作区、状态目录、CLI、维护间隔、微信通道与二维码参数 | ClassClaw TOML / 环境变量；校验后重启 ClassClaw |
| OpenClaw 配置与维护 | Gateway 全局配置 | Responses API、ClassClaw 插件开关、私聊会话隔离；全局模型与 Provider 摘要 | 三项运行参数可在网页保存并重启 Gateway；Provider 地址、模型目录及凭据通过 OpenClaw 自身配置维护 |
| OpenClaw 配置与维护 | 系统 Agent · Main / 提取 | 两个系统 Agent 各自的模型、思考与回复、上下文与记忆、模型参数、提示词和身份 | 网页运行参数保存请求重启 Gateway；工作区文件在后续对话轮次生效 |
| OpenClaw 配置与维护 | 会话维护 | 定时维护状态、上次结果、只读维护预览 | 网页可执行 dry-run；维护策略在 OpenClaw 侧核对，班级聊天会话长期复用 |
| 班级 Agent | 各班级 Agent 配置 | 按班级搜索、状态筛选，独立主/图片/语音模型、运行参数、提示词、微信绑定 | 使用各班级卡片的网页入口；主模型变更请求重启 Gateway，图片/语音设置在后续请求生效 |
| 可观测性 | 使用量 / 运行日志 / 数据库 / 审计 | 运行诊断、调用统计、只读数据库及变更审计 | 只读查看，不与配置或初始化混放 |

管理端 `/admin/settings` 和 `/admin/openclaw/settings` 合计覆盖当前全部 **43 项非敏感启动配置**，另显示 3 项凭据状态。每项显示实际生效值、TOML 路径、兼容环境变量和启动时来源；支持按分组及名称搜索。来源在启动时记录，修改磁盘文件或进程环境之后不会把旧运行值误标成新配置。重启后再刷新核对。

`GET /api/v1/admin/settings/catalog` 返回完整分组目录，仅管理员可访问，不依赖 Gateway 在线，禁止缓存；旧的 `GET /api/v1/admin/settings` 保留列表格式并补齐全部非敏感字段。公开 `/app-config` 的白名单没有扩大，任何网页接口均不新增凭据原文或尾号。

### OpenClaw 与班级 Agent 的配置文件边界

OpenClaw 的配置文件由 Gateway 自身管理，网页展示 `config.get` 返回的路径（若提供）。不要把 OpenClaw 原生字段写进 ClassClaw TOML：后者会拒绝未知字段。文件修改时停止 Gateway，按服务器已安装的 OpenClaw 版本校验，再重启。

| 内容 | OpenClaw 配置位置 / ClassClaw 存储位置 |
| --- | --- |
| 全局主模型 / 图片模型 | `agents.defaults.model` / `agents.defaults.imageModel` |
| Provider 地址、模型目录、凭据 | `models.providers` 及 OpenClaw 自身凭据配置；ClassClaw 网页只显示 Provider 名称 |
| Responses API / 插件 / 私聊隔离 | `gateway.http.endpoints.responses.enabled` / `plugins.entries.classclaw.enabled` / `session.dmScope` |
| 单个 Agent 运行参数 | `agents.list` 中对应 Agent 条目；网页主模型、回退模型、辅助模型、思考、上下文、预算等配置写入该条目 |
| 班级主模型 / 图片模型 / 语音模型 | ClassClaw `class_agent_bindings` 保存班级选择，主模型同时同步到 OpenClaw；请通过班级模型设置入口维护，不直接修改数据库 |
| 提示词与身份文件 | 对应 Agent 的工作区中的 `AGENTS.md`、`SOUL.md`、`IDENTITY.md`、`TOOLS.md`、`USER.md`、`HEARTBEAT.md` |

班级主模型同时保存在 ClassClaw 和 OpenClaw；仅手改 OpenClaw 的班级模型可能在后续 Agent 修复时被 ClassClaw 中的选择覆盖。班级图片理解与语音识别目前是网页对话功能，不将它们描述为微信端模型切换功能。Main 和提取 Agent 没有班级专用的图片/语音设置项。

提示词优先用网页保存，以执行 SHA-256 冲突校验并维护自定义标记。直接编辑托管工作区文件时，还需在同目录 `.classclaw-admin-customized.json` 的文件名数组中标记该文件，否则默认工作区生成流程可能覆盖它；备份时连同标记文件保存。网页“恢复系统默认”会移除对应标记，Main 不提供恢复系统默认。

单班配置的业务作用域仅限该班，但运行参数/主模型保存所请求的 **Gateway 重启会短暂影响所有班级 AI 服务**。图片/语音配置和提示词文件保存不触发该重启。运行参数表单只提交实际改动，保留未修改的继承设置和暂时不在模型目录中的现有值。

## 文件与优先级

- 非敏感配置：复制 `config/classclaw.example.toml` 为 `config/classclaw.toml`。
- 密钥：复制 `.env.example` 为 `.env`，只填写 Token 和初始管理员密码。
- 外置配置：设置 `CLASSCLAW_CONFIG_FILE=/etc/classclaw/classclaw.toml`。一旦显式指定，文件不存在或内容无效都会阻止启动。
- 优先级：环境变量覆盖 TOML，TOML 覆盖程序默认值。已有部署的 `CLASSCLAW_*` 非敏感环境变量仍然兼容，但建议只作临时应急覆盖。
- 相对路径：TOML 中的相对路径以 TOML 文件所在目录为基准；环境变量中的相对路径以项目根目录为基准。生产环境建议使用绝对路径。

`config/classclaw.toml`、`.env`、数据库、附件与 OpenClaw 状态目录均已被 Git 忽略，不应提交或外发。

## 当前可配置项目

只列出项目已经具备的能力，不包含微信群聊、多班主任账号绑定、消息群发策略等尚未实现的功能。

### 存储 `[storage]`

| 字段 | 用途 | 修改注意事项 |
| --- | --- | --- |
| `database_url` | SQLite 数据库位置 | 只支持 `sqlite:///`；迁移路径前应停机并复制数据库及 `-wal`/`-shm` 状态，推荐先执行备份 |
| `usage_database_url` | 独立 AI 用量库，默认 `data/usage.db` | 只支持 `sqlite:///`，不可与业务库使用同一文件；环境变量为 `CLASSCLAW_USAGE_DATABASE_URL`。首次拆分会迁移业务库中的旧用量表，之后仅修改路径不会自动搬迁已有独立库，应停机备份后搬迁 |
| `attachment_dir` | 网页上传附件目录 | 修改路径不会自动搬迁旧附件，应连同数据库记录指向的文件整体迁移 |
| `max_attachment_bytes` | 单附件大小上限 | 1 字节至 1 GiB |
| `class_workspace_root` | 班级 OpenClaw Agent 工作区根目录 | 修改路径不会自动搬迁已创建的工作区，应停机整体迁移 |
| `openclaw_state_dir` | OpenClaw 状态目录 | 管理端 Agent 调用统计和会话清理需要读取；应与 Gateway 使用同一系统用户和状态目录 |

附件、班级工作区和 OpenClaw 状态目录不得互相包含；附件或工作区不得包含数据库、日志或配置文件。危险的系统根目录、用户主目录和项目根目录会被拒绝。

### 运行 `[runtime]`

| 字段 | 用途 |
| --- | --- |
| `timezone` | 业务时区，必须是有效 IANA 时区，如 `Asia/Shanghai` |
| `log_level` | `DEBUG`、`INFO`、`WARNING`、`ERROR` 或 `CRITICAL` |
| `log_file` | 后端轮转 JSON 日志位置 |
| `auth_session_hours` | 网页登录会话有效小时数 |
| `analysis_cache_max_entries` | 确定性分析缓存的最大条数，超过后清理最旧记录 |

### 服务监听 `[server]`

| 字段 | 用途 |
| --- | --- |
| `host` | `python run.py` 的监听地址；同机反向代理建议 `127.0.0.1`，直接局域网访问可使用 `0.0.0.0` |
| `port` | HTTP 监听端口，范围 1–65535 |
| `access_log` | 是否启用 Uvicorn 访问日志；ClassClaw 自身仍会记录脱敏请求元数据 |

必须通过 `python run.py` 启动才能自动使用这三个字段；若直接执行 Uvicorn CLI，应在命令参数中保持相同取值。worker 数固定为 1，不作为可配置项。

### 首次初始化 `[bootstrap]`

`default_admin_username` 只影响首次创建管理员或“完整初始化”后重新创建的管理员，不会重命名现有账号。初始密码只放在 `.env` 的 `CLASSCLAW_DEFAULT_ADMIN_PASSWORD` 中，并应在首次登录后修改。

忘记管理员密码时，在项目目录执行 `.venv/bin/python scripts/reset_admin_password.py`，即可将现有管理员密码重置为该配置值，并撤销其旧登录会话；仅修改 `.env` 不会自动覆盖现有密码。命令不要求旧密码，不显示新密码，不清空业务数据；建议先停服，详情见 [本地密码重置](accounts-admin.md#本地终端重置管理员密码)。

### OpenClaw `[openclaw]`

| 字段 | 用途 |
| --- | --- |
| `gateway_url` | Gateway 的完整 HTTP/HTTPS 地址 |
| `agent_id` | 默认 Main Agent ID |
| `extractor_agent_id` | 一次性结构化提取 Agent ID |
| `extractor_enabled` | 是否使用独立提取 Agent；关闭后回退 Main Agent |
| `class_agent_thinking` | 班级 Agent 在所有场景下的统一默认思考强度；默认 `off`，可选 `off/minimal/low/medium/high/xhigh/adaptive/max`，实际模型须支持所选档位。环境覆盖：`CLASSCLAW_OPENCLAW_CLASS_AGENT_THINKING` |
| `timeout_seconds` | 后端请求 Gateway 的超时，上限 120 秒 |
| `session_cleanup_hours` | 一次性会话清理间隔；`0` 关闭 |
| `cli` | 后端执行会话清理时使用的 OpenClaw CLI 命令 |

Gateway 管理 Token 只放在 `.env` 的 `CLASSCLAW_OPENCLAW_GATEWAY_TOKEN` 中。ClassClaw API Token 同样只放在 `.env`，并与 OpenClaw ClassClaw 插件配置保持一致。

班级默认思考强度不按查询、写入、图片等任务自动改变；网页、微信、提醒等班级 Agent 入口均继承该默认。独立提取 Agent 仍使用其自己的配置。ClassClaw 启动时批量同步已绑定班级的 Agent 默认值，创建新班级时也应用；只有值发生变化才请求 Gateway 配置重启，已有 OpenClaw 会话的显式覆盖不被清除。请在无进行中对话时重启部署。

管理端班级运行参数面板中的思考强度显示为只读配置值，避免在此保存后又被默认配置同步覆盖；Main/提取 Agent 原有思考设置入口不变。班级模型、回复风格和其他参数仍可按原流程配置。

网页对话栏提供“本会话思考强度”：选项来自 Gateway `agents.list` 为本班 Agent 当前模型及 runtime 解析的 `thinkingLevels`，不按模型名称猜测，也不固定显示全部档位。保留选项 ID 与显示标签的区别，例如 Kimi 的 `low` 显示为“开启”，不误显示为强度“低”。页面加载、重新获得焦点及发送前刷新选项；切换模型后，仍受支持的会话选择保留，失效选择恢复为“跟随默认”并显示提示。如果发送前才发现失效，保留草稿，待用户核对新选项后再次发送。

“跟随默认”优先使用 `class_agent_thinking`；配置值不受当前模型支持时，使用 Gateway 返回的有效 `thinkingDefault`，并在网页明确显示采用的档位。不修改配置文件或 Agent 全局设置；Gateway 未提供可用默认值时必须选择其支持的档位。后端在设置会话前再次校验当前模型，显式请求不支持的档位仍返回错误，不悄悄替换。Gateway 缺少能力元数据或连接失败时不猜测选项，发送前须重新读取成功。

设置只影响当前账号、班级和对话 UUID 的后续消息，不改变其他会话、其他渠道或 Agent 默认配置；正在回复时不能修改，新对话不会继承上一对话的覆盖值。模型变更不会取消或改写已发送的请求，进行中的旧选择在回复结束后才重新校验。网页会话设置随对话保留在标签页内存，刷新/关闭后的恢复边界不变。

### 功能开关 `[features]`

| 字段 | 当前影响范围 |
| --- | --- |
| `file_analysis` | 名单、课表和座位文件的 OpenClaw 解析入口；关闭后仍可使用已有结构化数据和手工操作 |
| `event_ai` | 学生日常事件的 AI 分类接口 |
| `wechat_binding` | 班级 Agent 微信二维码启动、等待与绑定入口 |
| `reminders` | 提醒查询和发送接口；关闭不会删除已有提醒 |

开关同时在网页和后端接口生效，不能仅靠隐藏按钮绕过。管理端“系统配置”页只读显示实际启动值、TOML 路径及环境变量来源，不再写数据库或在线修改部署参数。

### 网页 `[web]` 与 `[web.brand]`

`[web]` 可配置 AI 请求等待时间、管理员默认统计窗口和只读数据库默认分页数。`[web.brand]` 可配置浏览器标题、品牌名称、字符标记、普通/管理员副标题和登录页说明。

后端只通过公开的 `/api/v1/app-config` 返回浏览器确实需要的品牌、功能开关、显示时区、网页默认值和二维码轮询参数；该响应不含 Token、密码、数据库路径、附件路径、工作区或微信 channel ID，并禁用缓存。

### 微信绑定 `[wechat]`

| 字段 | 当前影响范围 |
| --- | --- |
| `channel` | OpenClaw 微信兼容层的 channel ID，默认 `openclaw-weixin` |
| `qr_binding_timeout_seconds` | 二维码绑定总等待时间 |
| `qr_initial_poll_ms`、`qr_poll_ms`、`qr_retry_ms` | 网页查询二维码状态的节奏 |
| `gateway_start_timeout_seconds`、`gateway_wait_timeout_seconds` | 后端启动和等待微信登录的单次 Gateway 超时 |

轮询间隔必须小于二维码总等待时间，总等待时间必须大于单次 Gateway 超时；微信单次超时还不能大于 `openclaw.timeout_seconds`。这里仅配置当前已有的“一班一专属 Agent、可选绑定一个微信账号”流程，不涉及群聊或未实现的微信能力。

微信兼容层 `2.4.6-classclaw.2` 将单次等待与总登录有效期分离：默认 15 秒短轮询超时只返回继续等待，不删除默认 300 秒的登录会话。过期二维码最多自动刷新 3 次，手机微信显示的数字验证码在网页中提交（每次登录最多 5 次）；验证码不持久化。后端请求额外保留 15 秒传输/刷新余量。此流程只适用于仓库兼容层的 ClassClaw 私有登录端点，官方 CLI 登录不变。

## 修改与重启流程

```bash
cd /opt/classclaw
source .venv/bin/activate

# 1. 停止服务并备份（涉及数据/工作区路径时必须执行）
sudo systemctl stop classclaw
python scripts/backup.py ./backups

# 2. 修改非敏感配置和密钥
${EDITOR:-vi} config/classclaw.toml
${EDITOR:-vi} .env

# 3. 只读校验；不创建目录、不连接数据库、不修改 OpenClaw
python scripts/check_config.py --config config/classclaw.toml

# 4. 启动并验收
sudo systemctl start classclaw
curl --fail http://127.0.0.1:8000/health
```

备份脚本覆盖业务 SQLite、独立用量 SQLite 与附件，并保存双库清单；各库快照单独一致，不是跨库原子快照。班级 Agent 工作区和 OpenClaw 状态目录应另外使用服务器文件备份工具保存。如果只改品牌、功能开关或超时，也仍需完整重启进程。不要使用管理端页面替代配置文件修改。

## 校验行为

`scripts/check_config.py` 会检查 TOML 语法、未知字段、类型和取值范围、时区、Gateway URL、超时关系、危险或重叠路径，以及各目标最近的现有父目录是否可写。它还会拒绝未配置、过短或仍为示例值的 API Token、Gateway Token 和初始管理员密码，但绝不输出密钥内容。

需要机器读取时使用：

```bash
python scripts/check_config.py --config config/classclaw.toml --json
```

应用启动时执行相同的结构和交叉校验；校验失败会直接退出，不会带着部分配置运行。
