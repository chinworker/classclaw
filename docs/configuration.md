# 静态配置说明

ClassClaw 的部署配置采用“非敏感 TOML + 密钥环境变量”两层结构。所有配置都只在进程启动时读取，不支持在线热修改；修改后应先校验，再重启服务。这样可以让网页端、后端与微信绑定流程使用同一份确定配置，同时避免把 Gateway Token 等密钥返回给浏览器。

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

### 服务监听 `[server]`

| 字段 | 用途 |
| --- | --- |
| `host` | `python run.py` 的监听地址；同机反向代理建议 `127.0.0.1`，直接局域网访问可使用 `0.0.0.0` |
| `port` | HTTP 监听端口，范围 1–65535 |
| `access_log` | 是否启用 Uvicorn 访问日志；ClassClaw 自身仍会记录脱敏请求元数据 |

必须通过 `python run.py` 启动才能自动使用这三个字段；若直接执行 Uvicorn CLI，应在命令参数中保持相同取值。worker 数固定为 1，不作为可配置项。

### 首次初始化 `[bootstrap]`

`default_admin_username` 只影响首次创建管理员或“完整初始化”后重新创建的管理员，不会重命名现有账号。初始密码只放在 `.env` 的 `CLASSCLAW_DEFAULT_ADMIN_PASSWORD` 中，并应在首次登录后修改。

### OpenClaw `[openclaw]`

| 字段 | 用途 |
| --- | --- |
| `gateway_url` | Gateway 的完整 HTTP/HTTPS 地址 |
| `agent_id` | 默认 Main Agent ID |
| `extractor_agent_id` | 一次性结构化提取 Agent ID |
| `extractor_enabled` | 是否使用独立提取 Agent；关闭后回退 Main Agent |
| `timeout_seconds` | 后端请求 Gateway 的超时，上限 120 秒 |
| `session_cleanup_hours` | 一次性会话清理间隔；`0` 关闭 |
| `cli` | 后端执行会话清理时使用的 OpenClaw CLI 命令 |

Gateway 管理 Token 只放在 `.env` 的 `CLASSCLAW_OPENCLAW_GATEWAY_TOKEN` 中。ClassClaw API Token 同样只放在 `.env`，并与 OpenClaw ClassClaw 插件配置保持一致。

### 功能开关 `[features]`

| 字段 | 当前影响范围 |
| --- | --- |
| `file_analysis` | 名单、课表和座位文件的 OpenClaw 解析入口；关闭后仍可使用已有结构化数据和手工操作 |
| `event_ai` | 学生日常事件的 AI 分类接口 |
| `wechat_binding` | 班级 Agent 微信二维码启动、等待与绑定入口 |
| `reminders` | 提醒查询和发送接口；关闭不会删除已有提醒 |

开关同时在网页和后端接口生效，不能仅靠隐藏按钮绕过。管理端“静态配置”页只读显示实际启动值和 TOML 路径，不再写数据库或在线修改。

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

备份脚本覆盖 SQLite 与附件；班级 Agent 工作区和 OpenClaw 状态目录应另外使用服务器文件备份工具保存。如果只改品牌、功能开关或超时，也仍需完整重启进程。不要使用管理端页面替代配置文件修改。

## 校验行为

`scripts/check_config.py` 会检查 TOML 语法、未知字段、类型和取值范围、时区、Gateway URL、超时关系、危险或重叠路径，以及各目标最近的现有父目录是否可写。它还会拒绝未配置、过短或仍为示例值的 API Token、Gateway Token 和初始管理员密码，但绝不输出密钥内容。

需要机器读取时使用：

```bash
python scripts/check_config.py --config config/classclaw.toml --json
```

应用启动时执行相同的结构和交叉校验；校验失败会直接退出，不会带着部分配置运行。
