# ClassClaw OpenClaw 插件

这个插件把 OpenClaw 和 ClassClaw 后端连接起来。OpenClaw 负责读取对话、图片、PDF、表格等来源并整理为结构化信息；插件只允许它先创建写入预览，再由用户逐次确认后提交。后端仍会再次校验预览版本和内容哈希，因此不能绕过确认直接写业务数据。

OpenClaw 是 ClassClaw 的必要连接。ClassClaw 后端会通过 Gateway `/tools/invoke` 调用 `classclaw_health` 验证插件确实可用；网页上传文件则通过启用后的 `/v1/responses` 交给 OpenClaw 智能体分析。连接失败时，后端会阻止所有业务 API。

插件同时附带 `classclaw-manager` Skill，用于约束既有班级智能体的解析、消歧、证据记录和确认流程。新班级只允许从网页创建。

## 本地构建与校验

```bash
cd integrations/openclaw/classclaw
npm install
npm run build
npm test
npm run plugin:validate
```

仓库的 `plugin:validate` 会校验清单、实际注册工具、可选提交工具、班级范围/单轮调用保护钩子、专用鉴权会话设置路由和 Skill 文件。发布前还应在隔离状态目录实际安装并执行 `openclaw plugins inspect classclaw --runtime --json`。

网页流式对话的独立思考设置需要此版本插件。`/api/v1/classclaw/web-session-thinking` 只接受 Gateway 鉴权的后端请求，只能修改绑定班级网页会话的 `thinkingLevel`；不是智能体工具，也不开放通用会话 RPC。修改后须重新构建插件，并在无进行中对话时重启 Gateway。工具返回采用显式成功 envelope，避免将 `cancelled` 等业务状态当成执行错误；完整预览和确认信息保留，重复 payload 不再进入模型上下文。

## 安装到 OpenClaw

先启动 ClassClaw 后端，再从仓库根目录运行：

```bash
openclaw plugins install --link ./integrations/openclaw/classclaw
openclaw plugins enable classclaw
openclaw config set plugins.entries.classclaw.config.baseUrl http://127.0.0.1:8000
openclaw config set tools.alsoAllow '["classclaw","classclaw_commit_write","classclaw_commit_writes"]' --strict-json
openclaw config set gateway.http.endpoints.responses.enabled true
openclaw gateway restart
```

若后端设置了 `CLASSCLAW_API_TOKEN`，应把同一个值提供给 OpenClaw 进程。推荐通过 OpenClaw 的进程环境设置该变量；也可以写入插件配置：

```bash
openclaw config set plugins.entries.classclaw.config.apiToken '替换为随机长令牌'
```

如需上传 OpenClaw 已保存到本机的附件，还要显式允许相应目录。目录列表之外的文件会被插件拒绝：

```bash
openclaw config set plugins.entries.classclaw.config.allowedUploadRoots '["/绝对路径/到/OpenClaw媒体目录","/绝对路径/到/教师导入目录"]' --strict-json
```

安装后用以下命令确认插件和 Skill 均已发现：

```bash
openclaw plugins list
openclaw skills list
openclaw doctor
```

## 使用方式

日常信息可以直接发给 OpenClaw，例如：

> 请把这张图片中的迟到记录导入 ClassClaw。先告诉我识别结果和将写入哪些学生，不要在我确认前落库。

OpenClaw 应上传原始附件，然后把微信原文、自然语言、粘贴内容或附件 ids 交给 `classclaw_analyze_interaction`。它会返回澄清问题或经过后端校验的 proposal；用户核对预览并在聊天中明确确认后，单条调用 `classclaw_commit_write`，同组多条调用 `classclaw_commit_writes`，不再弹出另一张审批卡。

请求创建班级时，主智能体只会回复打开 ClassClaw 网页：

> 请打开 http://127.0.0.1:8000/app/，连接 OpenClaw 后上传学生名单和课表。

插件不再注册 `classclaw_onboarding_*` 工具，也不允许 `class.create` proposal。网页确认班级后，后端创建专属 agent、微信二维码和 channel binding；每个专属 agent 的 `agentId` 会映射到不可由模型修改的 `class_id`。

不支持直接解析的专有文件格式应保留原附件，并请用户导出为 PDF、图片、CSV/XLSX 或可复制文本；不能凭猜测填值。

## 写入安全边界

- `classclaw_propose_write` 只写预览，不修改班级业务数据；创建班级不在它的白名单中。
- `classclaw_analyze_interaction` 是微信、自然语言、粘贴文本、OCR 和附件的默认入口，并保存可审计分析记录。
- `classclaw_commit_write` 和 `classclaw_commit_writes` 是可选写入工具，必须被工具策略显式放行。
- `before_tool_call` 只注入可信班级范围，不创建 OpenClaw 审批卡；聊天中的预览与明确肯定回复构成一次完整复核。
- 后端默认拦截旧业务写接口；只有未过期且版本、哈希均匹配的 proposal 才能执行。
- 并发修改、过期预览、缺失必填项、同名歧义和低置信度关键字段都会阻止提交或要求复核。
