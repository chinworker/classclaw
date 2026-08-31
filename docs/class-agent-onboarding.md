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

ClassClaw 后端需要它来创建 agent、启动二维码登录和写入 channel binding。该接口权限很高，只应监听回环地址或可信私网，不要暴露到公网。

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

当前固定的腾讯插件 `2.4.6` 已实现二维码登录，但没有声明 OpenClaw 2026.7 用于 provider discovery 的 `gatewayMethods`，直接安装会在网页端返回 `web login provider is not available`。仓库中的兼容层仍使用腾讯官方实现，只补充 `web.login.start` 与 `web.login.wait` 两个声明。腾讯发布包含相同声明的新版本后，可以移除兼容层并恢复官方直装。

创建每个班级时，网页会为该班生成独立 account alias，并显示对应二维码。腾讯插件返回的是待编码的二维码内容，不一定是图片 URL；ClassClaw 后端会将它编码为 PNG Data URL 后再交给浏览器，轮询时只回传原始短内容。官方说明：<https://github.com/Tencent/openclaw-weixin>。

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

`timeoutMs` 是班级智能体调用 `classclaw_analyze_interaction` 等工具的等待上限，必须不小于后端的 `CLASSCLAW_OPENCLAW_TIMEOUT_SECONDS`；默认 30 秒会在分析较慢时把工具调用变成超时失败。

### 5.1 轻量提取智能体（默认开启，提升分析速度）

默认启用独立提取智能体（`CLASSCLAW_OPENCLAW_EXTRACTOR_ENABLED=true`，无需任何设置）。后端所有 JSON 提取（interaction 清洗、onboarding 导入、课表/座位/值日/事件分析）走一个专用的无工具智能体，默认名 `classclaw-extractor`（可用 `CLASSCLAW_OPENCLAW_EXTRACTOR_AGENT_ID` 覆盖），首次使用时自动创建；清洗规范自动写入其工作区 `data/openclaw-agents/_extractor/AGENTS.md`，内容哈希变化时自动覆写（手工修改会被下次规则更新覆盖），请求 prompt 只携带数据。相比走主智能体，省去工具循环和工作区加载，单次分析耗时显著下降。设 `CLASSCLAW_OPENCLAW_EXTRACTOR_ENABLED=false` 可回退主智能体与完整内联提示词。该智能体不绑定班级，不出现在管理后台的智能体列表中；创建依赖 5.3 节的 `admin-http-rpc`，未启用时自动回退。

ClassClaw 自动维护 `plugins.entries.classclaw.config.agentClasses` 和顶层 `bindings`，不要手工把多个班级指向同一个 agent 或微信 account。

## ClassClaw 环境变量与启动

```dotenv
CLASSCLAW_API_TOKEN=与-ClassClaw-插件配置一致
CLASSCLAW_OPENCLAW_GATEWAY_URL=http://127.0.0.1:18789
CLASSCLAW_OPENCLAW_GATEWAY_TOKEN=与-gateway.auth.token-一致
CLASSCLAW_OPENCLAW_AGENT_ID=main
CLASSCLAW_OPENCLAW_CLASS_WORKSPACE_ROOT=./data/openclaw-agents
CLASSCLAW_OPENCLAW_WECHAT_CHANNEL=openclaw-weixin
```

```bash
cd /Users/wellon/classclaw
source .venv/bin/activate
alembic upgrade head
python run.py
```

打开 <http://127.0.0.1:8000/app/>。

## 创建班级

1. 输入 ClassClaw API Token，点击“连接 OpenClaw”。页面必须同时通过 Gateway、ClassClaw 插件和 `admin-http-rpc` 检查。
2. 填写班级名称、年级、班主任、本班教室和学期。这些字段确定且一一对应，不经过模型重写。
3. 上传学生名单文件。支持 XLSX/XLSM、DOCX、PPTX、CSV/TSV、PDF、图片、JSON/XML、RTF、Markdown 和其他文本文件；原件与哈希会保留。
4. 在结构化名单表中核对、增删或修改行。此后保存直接使用表格值，不再调用 OpenClaw。
5. 上传课表/作息文件。OpenClaw 只分析本次文件，结果完整替换旧课表；不会引用 onboarding 历史对话。
6. 核对节次和课表表格。节次自定义名称可留空；留空时网页按顺序显示“第一节、第二节、第三节”等名称。科目从当前课表的 `subject` 去重归纳；不设置科目满分。课程教室为空时使用本班教室。
7. 生成最终预览。后端检查班级必填项、学号、重复星期/节次、节次引用和课表格式。
8. 勾选复核声明并再次输入班级名称。确认后班级、学生、科目、节次和课表在同一事务中写入，同时建立 `pending_agent` 绑定记录。班级名称输入时会即时检查已有班级及旧式同名 OpenClaw agent；Agent 的资源名使用 `classclaw-{class_uuid}`，不再直接使用班级名称。
9. 页面先创建该班的独立 agent/workspace，并提供“绑定微信”和“暂不绑定微信”。创建时会覆盖 OpenClaw 默认模板，写入精简的 `AGENTS.md`、`SOUL.md`、`IDENTITY.md`、`TOOLS.md`、`USER.md` 与禁用心跳说明；展示名使用带短 ID 的 ClassClaw 助理名，不直接采用班级名。选择绑定时才显示二维码并自动轮询；检测成功便写入微信消息路由和 `agentClasses` 映射。选择暂不绑定时保留班级和智能体，之后仍可补绑。

座位表不属于 onboarding 必填内容，也不在创建草稿和最终 proposal 中。班级创建完成后，班主任可在独立的“座位表”页面手动设置或上传文件生成预览。

绑定成功时，后端还会为该 agent 限制为 ClassClaw 所需工具和单个 Skill，关闭记忆搜索、思考/推理输出，并限制 bootstrap 大小；新会话仍注入必要规则，连续会话跳过重复上下文，以减少 token 与响应时间。

若第 9 步失败，班级不会被删除。修复 OpenClaw/微信插件后点击“重新生成二维码”即可重试；后端会按 workspace 识别并接管已经创建成功但尚未来得及写回本地 ID 的 agent，不会重复创建。

## 彻底删除班级

网页危险区调用 `DELETE /api/v1/classes/{class_id}`。后端先移除 OpenClaw `agents.list` 中的班级 Agent、微信 `bindings` 路由和 ClassClaw `agentClasses` 映射，并安全删除该班独立 workspace；随后在一个数据库事务中删除班级全部业务数据。若 Gateway 配置清理失败，数据库班级保持不变，修复连接后重试即可。删除成功后原班主任账号恢复为无班级状态，可以重新进入 onboarding。

## 文件重新上传与人工修改规则

- 文件解析使用随机的新 OpenClaw session key，提示词不包含旧草稿列表。
- `students` 上传完整替换学生列表；`timetable` 上传完整替换 `periods` 与 `base_timetable`。
- 解析后只在 HTML 表格里编辑。PATCH 使用 `replace_lists=true`，删除一行就会从草稿删除。
- 人工结构化编辑不会再次经过模型。只有重新上传文件时 OpenClaw 才重新介入。
- “信任文件”表示允许解析和预填，不表示跳过最终复核。

## 日常使用

1. 微信 account binding 把消息送到班级专属 agent。
2. Skill 要求附件先留证，非确定性输入调用 `classclaw_analyze_interaction`。
3. 插件从可信 `agentId` 查到固定 `class_id`，覆盖模型提供的班级参数。
4. 后端返回澄清问题或待复核 proposal；此时没有业务写入。
5. 用户在聊天中明确确认后，后端立即校验 proposal 的班级、revision、状态和有效期并执行；同组多条使用单事务批量写入，不再出现系统审批卡。

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
- 修改后旧内容回来：确认页面没有 textarea；重新上传必须完整替换列表。
- `CLASS_SCOPE_VIOLATION`：当前智能体尝试确认不属于本班的 proposal，应取消并重新分析。
- Gateway 断开：先恢复 OpenClaw，不要关闭门禁绕过。

## 安全边界

- Gateway 管理 token 只存后端 `.env`，浏览器只接触 ClassClaw API token。
- ClassClaw 不保存微信登录凭据或二维码内容；凭据由微信 channel 插件保存在 OpenClaw。
- `admin-http-rpc` 只放在回环地址/可信私网。
- workspace/session 隔离不是数据库授权的替代；业务实体仍以 `class_id` 建索引/外键，proposal 确认还会校验班级归属。
