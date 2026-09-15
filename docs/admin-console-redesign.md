# 管理员控制台重构设计（Admin Console Redesign）

> 本文档是管理端整页重构的唯一事实来源。实现时请严格对照本文档；若实现与本文档冲突，先修改文档再动代码。
> 目标读者：执行重构的智能体/开发者。阅读本文档无需额外上下文，但建议先阅读 `AGENTS.md` 了解项目分层约定。

## 1. 背景与现状

当前管理端共 11 个页面（`web/js/pages/admin*.js`），主要问题：

| 问题 | 证据 |
|---|---|
| 系统配置整页只读 | `adminSettings.js` 只渲染 `configuration_catalog` 快照，用文字指引用户手改 TOML、手动重启 |
| OpenClaw 全局配置只能改 3 项 | `adminAgents.js` gateway 页仅 `responses_enabled / classclaw_plugin_enabled / dm_scope` |
| 同一领域配置拆散 | 系统配置、OpenClaw 连接、Gateway 全局、系统 Agent、班级 Agent 共 5 个页面 |
| 无 dirty 态 / 无差异预览 / 无来源分层交互 | 所有保存均为裸 PATCH；来源只以小字 badge 展示 |
| 用量信息只有静态卡片 | `adminUsage.js` 无趋势、无维度切换、无 Agent 对比 |
| 日志只是尾部文本 | `adminLogs.js` 无级别过滤的键盘流、无 request_id 关联、无时间范围、无实时 tail |
| 运维功能割裂 | 备份、数据库、审计 3 页各自为政 |

现有可复用资产（**不要重写**）：

- 后端：`app/services/configuration_catalog.py`（配置目录）、`app/services/admin_console.py`（overview/usage/logs/openclaw_config/openclaw_agent_catalog/...）、`app/services/openclaw_usage.py`（会话扫描）、`app/services/logs.py`（日志读取+脱敏）、`app/services/http_client.py`（`gateway_headers()`）、`app/services/agent_thinking.py`
- API：`/admin/overview`、`/admin/usage`、`/admin/usage/agents`、`/admin/settings/catalog`、`/admin/openclaw/config`、`/admin/openclaw/agents/catalog`、`/admin/logs`、`/admin/database/*`、`/admin/system/initialize`
- 前端：`attachOverlay/openModal/openDrawer`、`dataTable`、`statusBadge`、`errorPanel`、`skeleton`、`qrBindingPanel`、`openAgentModelSettings`
- 测试：`tests/helpers.py`（`teacher_for_class` / `make_binding`）、`tests/conftest.py`

## 2. 目标与非目标

**目标**
1. 管理员在一个界面内完成 ClassClaw 全部启动配置的查看与修改（UI 直接写 `config/classclaw.toml`）。
2. OpenClaw Gateway 侧配置以白名单方式安全编辑，全程走 Gateway 自带 `config.get` / `config.patch`（`baseHash` 乐观锁），永不直接读写 OpenClaw 磁盘文件。
3. 专业感：来源分层（默认/TOML/环境变量）、差异预览、dirty 管理、行内校验错误、统一键盘可达。
4. 新增设计得当的「用量」模块与「日志/Debug」模块。
5. 页面收敛：11 个 admin 页面 → 4 个一级入口。

**非目标**
- 不引入前端构建链、不引入图表/编辑器三方库（无 npm；原生 SVG 与 `<textarea>` 实现）。
- 不做自动重启按钮（systemd 托管下不可靠）；只展示重启提示与命令。
- 不通过 UI 修改任何密钥值（API Token、Gateway Token、管理员密码）。
- 不改变班主任侧页面；不改变任何业务域接口语义。

## 3. 总体信息架构

```
管理端（adminOnly）
├── 概览        /admin/overview     健康状态 + 核心指标 + 最近告警
├── 设置中心    /admin/settings     本重构核心；ClassClaw / OpenClaw 双域
├── 班级与账号  /admin/access       用户管理 + 班级管理 + 各班级 Agent 行内展开
└── 运维中心    /admin/ops          tab：用量 · 日志 · 备份与维护 · 数据库 · 审计
```

旧路由保留重定向（`app.js` 中 alias），不删除功能链接，一季后可移除：

| 旧路由 | 新落点 |
|---|---|
| `/admin/settings` | `/admin/settings`（同名，内容替换为设置中心 ClassClaw 域） |
| `/admin/openclaw/settings` | `/admin/settings?domain=openclaw` |
| `/admin/openclaw` | `/admin/settings?domain=openclaw&section=gateway` |
| `/admin/openclaw/system-agents` | `/admin/settings?domain=openclaw&section=system-agents` |
| `/admin/openclaw/maintenance` | `/admin/ops?tab=logs&source=openclaw`（会话维护操作卡保留于此） |
| `/admin/agents` | `/admin/access?focus=agents` |
| `/admin/usage` | `/admin/ops?tab=usage` |
| `/admin/logs` | `/admin/ops?tab=logs` |
| `/admin/database` | `/admin/ops?tab=database` |
| `/admin/audit` | `/admin/ops?tab=audit` |
| `/admin/maintenance` | `/admin/ops?tab=maintenance` |
| `/admin/users` `/admin/classes` | `/admin/access` |

侧栏导航同步改为 4 项：概览、设置中心、班级与账号、运维中心。

## 4. 设置中心（核心）

### 4.1 布局

```
┌───────────────────────────────────────────────────────────────┐
│ [ClassClaw | OpenClaw] 域切换      全局搜索    [表单|源码]    │
├────────────┬──────────────────────────────────────────────────┤
│ 分组树      │  设置列表（分组卡片）                              │
│ ▸ 服务监听  │  ┌────────────────────────────────────────────┐  │
│ ▸ 存储      │  │ 监听端口  server.port                       │  │
│ ▸ 运行与日志│  │ HTTP 端口，1–65535                          │  │
│ ▸ 账号初始化│  │ [ 8000        ]  [TOML] [需重启]            │  │
│ ▸ 功能开关  │  └────────────────────────────────────────────┘  │
│ ▸ 网页      │                                                  │
│ ▸ OpenClaw  │                                                  │
│ ▸ 工作区    │                                                  │
│ ▸ 微信      │                                                  │
├────────────┴──────────────────────────────────────────────────┤
│ 已修改 3 项 · [放弃全部] [预览差异并保存]      [需重启生效]     │
└───────────────────────────────────────────────────────────────┘
```

- 分组树数据来自 `GET /admin/settings/catalog` 的 `sections`，每组显示未保存修改计数角标。
- 全局搜索匹配 `label / description / config_path / env_var`，搜索时自动展开所有匹配分组。
- 视图切换「表单 | 源码」：两视图共享同一文档模型，切换即时同步（详见 4.4）。

### 4.2 设置行（表单视图）

每行固定结构，与现有 `setting-row` 风格一致但增强：

```
标签（中文）                    控件
config_path (code 样式)         [TOML] [ENV] [默认]  [需重启]
描述（muted 小字）
env_var (code 小字) + 当前来源说明
```

控件映射（`type` 字段由 catalog 提供）：

| 类型 | 控件 | 校验 |
|---|---|---|
| boolean | switch（复用现有 checkbox 样式，加开关态 class） | — |
| integer / number | `<input type=number>`，`min/max` 来自 catalog 约束 | 输入即校验，非法红色描边 |
| string（含枚举如 ThinkingLevel、dmScope） | `<select>` 或 `<input>` | 枚举非法不可选 |
| 路径类（`storage.*`、`log_file`） | 文本框 + 提示"相对路径以配置文件目录为基准" | 非空 |
| 密钥 | 不渲染输入框，仅状态徽章"已设置/未设置" + 说明文字 | — |

来源徽章规则：
- `file` → `[TOML]`；`environment:*` → `[ENV]` 且**控件禁用**，tooltip："被环境变量 CLASSCLAW_X 覆盖，需在服务器移除该变量后界面修改才会生效"；`default` → `[默认]`。
- env 覆盖项提交时后端也必须拒绝（防并发期间 env 变化造成的"写了不生效"），返回 `CONFIG_ENV_OVERRIDDEN` 409。

### 4.3 修改与保存流程

1. 控件变更 → 该行标 dirty（左侧色条 + 分组角标 +1），底栏计数更新。
2. 点「预览差异并保存」→ Modal 展示逐键差异表：`config_path | 当前生效值 | 新值 | 来源变化`，密钥类键值脱敏为 `***`。confirmDanger 模式不适用（非危险操作），用普通 openModal + 确认按钮。
3. 确认 → `PATCH /admin/settings/document`（见 §6.2）。
4. 成功 → toast"配置已写入，需重启 ClassClaw 生效" + 顶部常驻 banner「配置已修改，重启后生效 · `sudo systemctl restart classclaw` · [复制命令] [知道了]」，banner 直到页面刷新且 `config_hash` 变化后消失。
5. 失败 → Modal 内显示后端返回的逐字段错误（见 4.5），不关闭。

### 4.4 源码视图（settings.json 风格）

- 整块 TOML 文本编辑：带行号的 `<textarea>`（行号侧栏用 CSS 计数或同步滚动的 `<pre>`，不要 canvas），等宽字体，**不做语法高亮**（避免引入解析器），保存时后端给错误行号即可。
- 双视图同步：以 TOML 文本为唯一事实。表单视图每次变更重新生成文本片段并替换对应键行；源码切回表单时先 `POST /admin/settings/check` 干跑校验，非法则停留在源码视图并提示行号。
- 源码视图同样支持 diff 预览（新旧文本逐键对比，复用同一后端校验接口）。

### 4.5 行内校验与错误

- 保存前每次 PATCH 前可选 `POST /admin/settings/check` 干跑。
- 后端校验失败返回 `VALIDATION_ERROR`，`details.errors[]` 每项含 `config_path`（或 `line`）+ `message`。表单视图按 `config_path` 内联红字；源码视图按 `line` 标红行号侧栏。
- TOML 语法错误返回 `CONFIG_TOML_INVALID` + 行号。

### 4.6 OpenClaw 域（同一界面，不同数据源）

顶层切到 OpenClaw 后，分组树变为：`Gateway 全局` / `系统 Agent（Main · 提取）` / `班级 Agent` / `微信与连接`（该组实际映射 ClassClaw TOML 的 `[openclaw]` `[wechat]`，沿用 4.2 的 TOML 行）。

**Gateway 全局**
- 表单视图：白名单字段渲染（见 §6.3 `ALLOWED_GATEWAY_PATHS`），现有 3 项（responses/plugin/dm_scope）收敛到这里，另可增补安全标量项（如 `agents.defaults.model` 只读展示，编辑仍建议走班级 Agent 页）。
- 源码视图：`config.get` 返回的**脱敏 JSON**（后端脱敏，规则见 §8），编辑后 `config.patch` + `baseHash`。冲突（409 `CONFIG_CONFLICT`）提示"配置已被其他操作修改，请刷新后重试"并自动刷新。
- 保存成功沿用现有 `restart_after_ms` 提示与延迟刷新逻辑（现有 `refreshAfterRestart` 模式）。

**系统 Agent / 班级 Agent**
- 每行一个 Agent：名称、类型徽章（main/extractor/class）、状态徽章、模型三件套（主/图片/语音）行内 `select`（数据源现有 `/admin/openclaw/agents/{identifier}/settings`），「工作区文件」按钮打开源码抽屉（复用 `openDrawer` + 现有 workspace 文件 API + sha256 乐观锁）。
- 班级 Agent 同时在「班级与账号」页的班级行内提供入口（同一组件两种入口，URL 深链）。

## 5. 用量模块（设计目标：一眼看懂"谁在花多少、花在哪"）

位于运维中心「用量」tab，自上而下四层：

### 5.1 汇总卡（现有数据，复用 `GET /admin/usage`）

四张指标卡：窗口内请求数 / 总 Token（含缓存输入占比）/ 估算费用（若 Gateway 提供）/ 活跃 Agent 数。窗口切换：7 / 30 / 90 天（`days` query，复用现有参数）。

### 5.2 趋势图

- 原生 SVG 折线图（≤200 行）：X 轴日期，Y 轴请求数与 Token 双序列（双折线，左轴请求、右轴 Token）。
- 数据源 `usage.daily`（现有字段 `requests/input_tokens/output_tokens/total_tokens`）。
- 无第三方库；hover 显示当日数值（`<title>` + 跟随提示行）。

### 5.3 维度分布

两列条形图（SVG 横向条形，按值排序）：
- 按操作（`by_operation`：chat / analyze / transcribe / extract…）
- 按模型（`by_model`）
- 每条显示名称、请求数、Token、占比百分比条。

### 5.4 Agent 明细表（复用 `GET /admin/usage/agents`）

dataTable：Agent（名称+班级/系统徽章）、状态、调用数、消息数、Token、估算费用、延迟 p50/p95、告警列（现有 `warnings` 数组逐条 tag-warn 展示，如"会话记录超过扫描上限"）。
行点击展开当日明细（`daily` 数组的小趋势 sparkline）。

### 5.5 空态与降级

- 无任何记录：emptyState"暂无用量数据，产生 AI 调用后自动统计"。
- 某 Agent `available=false`：该行状态列显示"统计不可用"徽章 + warning 文案，不影响其他行。
- Gateway 离线：顶部 errorPanel 重试，本地 `ai_usage_records` 部分照常展示（标注"Gateway 侧数据缺失"）。

## 6. 日志 / Debug 模块

位于运维中心「日志」tab。目标：5 秒内定位"某次请求发生了什么"。

### 6.1 布局

```
[来源: ClassClaw | OpenClaw] [级别: 全部|DEBUG|INFO|WARNING|ERROR]
[时间范围: 最近15分钟|1小时|今天|全部] [搜索关键字________]
[request_id: ________]  [实时刷新 ⏻]  [导出当前视图]
────────────────────────────────────────────────────
2026-09-15 09:21:33 INFO  classclaw.request  POST /api/v1/... 200 42ms req:ab12cd
...
```

### 6.2 数据源与行为

- 两个来源复用现有 `GET /admin/logs?source=&level=&q=&cursor=`（后端已支持 cursor 与双来源）。新增 `request_id` 过滤参数（后端在现有 query 匹配基础上增加结构化过滤，见 §6.4）。
- 表格行：时间（毫秒）、级别（色彩徽章：ERROR 红/WARNING 黄/INFO 灰/DEBUG 暗灰）、logger、消息（等宽、可展开多行）、request_id（点击自动填入过滤框）、耗时。
- 实时刷新：开启后每 3s 增量拉取（携带最后 cursor/时间戳）；切页/离页停止轮询（遵守页面 dispose 约定）。
- 导出：当前过滤视图下载为 `.log` 文本（前端拼接 Blob，不新增后端接口）。
- 点击某行展开「请求详情」抽屉：该 request_id 的全部日志行（从 ERROR 反查请求链路）。

### 6.3 错误聚焦

- 「只看错误」快捷开关 = `level=ERROR`。
- 概览页「最近告警」卡直接深链到此 tab 并预填 ERROR 过滤。

### 6.4 红线

- 脱敏完全沿用 `app/services/logs.py::redact_line`（Bearer、api_key、token、password 正则替换），**禁止在浏览器端自行补打敏感内容**。
- 日志不持久化到业务库；单次读取上限沿用现有 5000 行缓冲。

## 7. 其他页面设计

### 7.1 概览 `/admin/overview`

健康灯区（复用现有 `/admin/overview` + openclaw 连接状态）：
- ClassClaw 数据库 / 用量库 / 附件目录可写 / Gateway 连通 / 插件在线 / admin-rpc 在线，六灯；异常灯点击深链到对应修复页。
核心指标卡（学生/班级/本周 AI 调用/今日 Token），「最近告警」列表（最近 5 条 ERROR 日志摘要，深链日志 tab）。

### 7.2 班级与账号 `/admin/access`

- 上半「班主任账号」：现有 adminUsers 表格原样迁入。
- 下半「班级」：现有 adminClasses 表格迁入，每行可展开：班级基本信息 / 归属班主任（下拉改派）/ 班级 Agent（状态徽章 + 模型三件套快捷入口 + 微信绑定状态，跳设置中心对应分组）。
- 不再单独存在 `/admin/agents` 页面。

### 7.3 运维中心 `/admin/ops`

tab 化：`用量`（§5）/ `日志`（§6）/ `备份与维护`（现有 adminMaintenance 迁入，含备份、附件清理、系统初始化、OpenClaw 会话维护卡）/ `数据库`（现有 adminDatabase 迁入）/ `审计`（现有 adminAudit 迁入）。
tab 状态写入 hash query（`#/admin/ops?tab=logs`），刷新保持。

## 8. 后端设计

### 8.1 配置文档读取 `GET /admin/settings/document`

返回：

```json
{
  "toml_text": "…当前文件全文；不存在时为 null",
  "config_file": "config/classclaw.toml",
  "config_hash": "…（现有 load_settings 的 config_hash）",
  "env_locked_paths": ["runtime.timezone", "…被环境变量覆盖的路径"],
  "catalog": "…现有 /admin/settings/catalog 完整内容（sections/items/credentials）"
}
```

- `env_locked_paths` 由 `settings.config_sources` 推导（source 以 `environment:` 开头）。
- TOML 文本只读直出，**绝不包含** `.env` 内容；`CLASSCLAW_*_TOKEN/PASSWORD` 不在 TOML 内（它们本来就是 env-only）。

### 8.2 配置写入 `PATCH /admin/settings/document`

请求体二选一（不允许混用）：
- `{ "toml_text": "…", "base_hash": "…" }` —— 源码视图整文保存
- `{ "changes": { "server.port": 8001, "…": "…" }, "base_hash": "…" }` —— 表单视图按键保存（后端将 changes 应用到当前 TOML 文本后走同一流程）

流程（全部在写锁内串行）：
1. `base_hash != 当前 config_hash` → 409 `CONFIG_CONFLICT`。
2. 解析 TOML（语法错误 → 422 `CONFIG_TOML_INVALID` + 行号）。
3. 禁止键检查：任何 `*_token / *_password` 路径 → 403 `CONFIG_SECRET_FORBIDDEN`；env 覆盖路径（`env_locked_paths`）→ 409 `CONFIG_ENV_OVERRIDDEN`。
4. 干跑 `load_settings()`（临时环境）做完整 Pydantic 校验；错误映射为 `details.errors[] = [{config_path, message}]`。
5. 备份当前文件为 `<config_file>.bak-<timestamp>`（同目录），原子写新文件（临时文件 + `os.replace`，保留权限位）。
6. 重新 `load_settings()` 验证可加载；失败立即回滚（恢复 .bak）→ 500 `CONFIG_WRITE_FAILED`。
7. `audit()` 记录：键名列表 + 新旧 config_hash，**不记录值**。
8. 返回：`{ new_config_hash, applied: ["server.port", …], restart_required: true, diff: [{path, old, new}] }`（diff 中值可回显，密钥已在第 3 步被禁）。

并发：同一进程串行写（`asyncio.Lock`）；多进程部署不支持（项目约定单 worker）。

### 8.3 干跑校验 `POST /admin/settings/check`

`{ toml_text }` 或 `{ changes }`，仅执行 8.2 的第 2/3/4 步，不落盘。用于源码视图切回表单与保存前实时校验。

### 8.4 回滚 `POST /admin/settings/rollback`

恢复到最近一次 `.bak-*`（按时间戳取最新），走 8.2 的 6/7/8 步校验与审计（`action=config_rollback`）。

### 8.5 Gateway 白名单 `ALLOWED_GATEWAY_PATHS`

`app/services/admin_console.py` 新增常量（顶层，便于 review）：

```python
ALLOWED_GATEWAY_PATHS = {
    "session.dmScope",
    "gateway.http.endpoints.responses.enabled",  # 已核对现有 config.get / config.patch 契约
    "plugins.entries.classclaw.enabled",
    # …实现时按 config.get 实际结构补充，仅允许标量（str/bool/int）叶子
}
```

新端点 `PATCH /admin/openclaw/config/raw`：
- 请求 `{ patch: {path: value}, base_hash }`；路径必须在白名单、值必须是标量 → 否则 422。
- 调用 `admin_rpc("config.patch", {…, baseHash})`；Gateway 侧冲突/失败透传为 409/502。
- 复用现有 `update_openclaw_config` 的审计与错误模式。

`GET /admin/openclaw/config/raw`：返回 `config.get` 全文的**脱敏**版本（脱敏规则：键名含 `token|key|secret|password|credential`（不区分大小写）的值替换为 `"***"`）+ `hash`。

### 8.6 日志接口扩展

`GET /admin/logs` 增加 query：`request_id: str | None`（在现有过滤基础上精确匹配 `item.request_id`）。其余不变。

### 8.7 路由层约束（遵守项目分层）

- 所有新端点放 `app/api/v1/admin_console.py`，只做 HTTP/鉴权/参数；逻辑进 `admin_console.py`（配置写入可新建 `app/services/config_document.py`，保持 admin_console 不继续膨胀）。
- 全部 `require_authenticated` + admin 校验（沿用现有 admin 路由的依赖）。
- 统一 `ok()` / `AppError`，错误码前缀：`CONFIG_TOML_INVALID` / `CONFIG_CONFLICT` / `CONFIG_ENV_OVERRIDDEN` / `CONFIG_SECRET_FORBIDDEN` / `CONFIG_WRITE_FAILED`。

## 9. 前端实现要点

### 9.1 新组件（`web/js/components.js` 或新文件 `web/js/settingsEditor.js`）

| 组件 | 职责 |
|---|---|
| `settingsEditor()` | 设置中心主体：分组树、搜索、表单行、dirty 管理、保存流程 |
| `sourceEditor(text, {onChange, errors})` | 行号 textarea；`errors:[{line,message}]` 渲染行号红标 |
| `diffPreviewModal(rows)` | 保存前差异预览（复用 openModal） |
| `trendChart(series, {xKey, yKeys})` | SVG 双折线，≤200 行，无依赖 |
| `barList(items, {labelKey, valueKey, pct})` | SVG 横向条形 |
| `logViewer({source})` | §6 全部交互；dispose 时停止轮询（遵守页面资源约定） |

### 9.2 页面约定（必须与现有页面一致）

- 每个 render 模块记录 `activeView` 并导出 `dispose()`；轮询/在途请求在 dispose 时取消（`AbortController`），**但已发送的聊天类请求不取消**（沿用现有约定）。
- 离开页面前有 dirty 配置 → `beforeunload`/路由拦截 toast 确认。
- 所有动态文本经 `textContent` 插入（components.js 头部注释已有此约定）。
- Gateway 重启等待复用现有 `refreshAfterRestart` 思路（延迟 1600ms 轮询连接状态）。

### 9.3 路由与导航

- `web/app.js`：NAV_ADMIN 改 4 项；routes 表新增 4 个页面 loader（`adminSettings.js` 重写为设置中心；新建 `adminAccess.js`、`adminOps.js`）；旧路由保留 alias 指向新页面并带 query 参数。
- `web/styles.css`：新增样式分区 `/* ---------- 设置中心 ---------- */`，复用现有 design token（`--border/--muted/--primary` 等），不引入新色彩体系。

## 10. 安全红线（实现时必须逐条自检）

1. 密钥永不回显、永不入 UI 输入框、审计不记值；`GET document` 不返回 `.env` 内容。
2. TOML 写入禁止 `*_token/*_password` 键；env 覆盖键禁止写入。
3. 配置文件写入限定解析后的配置目录内，拒绝符号链接逃逸，`os.replace` 原子写。
4. Gateway raw patch 仅白名单标量路径；不暴露任意 JSONPath。
5. 审计：`config_update` / `config_rollback` / `gateway_config_patch` 三种 action，含操作者、键名、新旧 hash。
6. 失败安全：写入后重新加载失败必须自动回滚 `.bak` 并返回明确错误；绝不留下半写状态文件。
7. 所有页面 adminOnly；班主任访问返回 403（沿用现有依赖）。

## 11. 分阶段实施（每阶段独立可上线、全部测试通过后再进下一阶段）

| 阶段 | 内容 | 验收 |
|---|---|---|
| P0 | 后端：`config_document.py`（读/check/patch/rollback/备份/审计/白名单）+ `GET document` + 日志 `request_id` 过滤 + Gateway raw 白名单端点 | pytest 新增 ≥12 用例全绿 |
| P1 | 设置中心表单视图（ClassClaw 域全量：分组树/搜索/控件/dirty/diff/保存/banner） | web 测试覆盖 dirty 流与 env 锁定渲染；pytest 全绿 |
| P2 | 源码视图 + 双视图同步 + 行级错误 | web 测试：非法 TOML 停留源码视图并标行号 |
| P3 | OpenClaw 域（Gateway 白名单表单 + 脱敏源码 + 系统/班级 Agent 迁入）+ 旧 openclaw 页面下线 | 既有 `tests/web` 回归全绿；插件契约不变 |
| P4 | 运维中心（用量 §5、日志 §6、备份/数据库/审计迁入）+ 班级与账号合并 + 概览增强 + 旧路由 alias | 全量回归；手工走查 §12 |

## 12. 测试计划

**pytest（新增，放 `tests/test_admin_settings_document.py` 等）**
- PATCH：正常写入生成新 hash、`.bak` 存在；base_hash 冲突 409；TOML 语法错 422+行号；密钥键 403；env 覆盖键 409；非法值按 `config_path` 报错；写入后不可加载自动回滚；rollback 恢复上一版；审计记录无值只有键名与 hash。
- Gateway raw：非白名单路径 422；非标量值 422；baseHash 冲突透传 409；脱敏后响应不含 token 字样值。
- 日志 `request_id` 过滤精确性；脱敏依旧生效。
- 权限：班主任访问全部新端点 403。

**web 回归（`tests/web/*.test.mjs`，node:test 风格沿用现有 dom.mjs）**
- 表单 dirty 计数/放弃/分组角标；env 锁定行禁用+tooltip；diff 预览脱敏；banner 显示与消失条件。
- 源码视图：行号渲染、错误行标红、切回表单时校验拦截。
- logViewer：过滤参数拼接、实时刷新在 dispose 后停止、request_id 点击回填。
- trendChart/barList：空数据空态、单点数据不抛错。

**兼容性验证**
- Gateway 重启期间保存配置：前端按 `restart_after_ms` 延迟重试，错误可重放。
- `config.patch` baseHash 过期（其他终端同时修改）：409 → 自动刷新 + 提示。

## 13. 手工验收清单（上线前）

1. 管理员改 `server.port` 保存 → 文件更新、`.bak` 生成、banner 提示重启、重启后生效值正确。
2. 用 `CLASSCLAW_TIMEZONE` 环境变量启动 → 时区行显示 `[ENV]` 且不可编辑，PATCH 被拒 409。
3. 源码视图删掉一行造成语法错误 → 保存被拦，行号标红，文件未被修改。
4. 连续两人同时编辑 → 后提交者收到 409 并看到刷新后的新值。
5. Gateway 配置改 `session.dmScope` → Gateway 重启提示正常，班级聊天在重启后正常。
6. 用量 tab：造 2 天数据 → 趋势/分布/Agent 明细正确；清空 usage.db → 空态正常。
7. 日志 tab：制造一次 500 → ERROR 过滤可见；点 request_id 能看到该请求全部行；开启实时刷新后切走页面，确认轮询停止（无残留请求）。
8. 密钥全链路：界面、diff、审计、日志中均不出现任何 token/password 值。

## 14. 实施细节与现有契约对齐

- `config_hash` 保持加载器的生效配置 hash；文档接口另返回 `document_hash`（全文 SHA-256），写入和回滚要求当前 `base_hash`，并可携带 `base_document_hash`，避免仅注释或来源变化被并发覆盖。`active_config_hash` 表示当前进程启动版本，用于准确显示待重启状态。
- 环境变量锁定禁止**更改**对应 TOML 键（包括删除）；整文校验允许原样保留已有的锁定键。表单显式提交锁定键仍拒绝。
- 干跑复用 `load_settings(toml_text=...)`，不改进程环境、不创建临时配置文件。返回各键的编辑值、文本替换范围和差异，供双视图共用；替换范围由后端 TOML 解析确认，前端无需 TOML 解析器。
- 当前系统 Agent 接口只支持主模型与辅助模型；图片/语音按现有能力只读说明。班级 Agent 的主/图片/语音选择复用现有模型接口，保留高级运行参数、工作区和微信绑定功能。
- 日志时间过滤新增可选 `since`，在有界读取内过滤；ClassClaw 返回文件字节 cursor 和轮转标识，避免重复拉取。仍最多扫描 5000 行，界面明确这是有界日志视图。
- 审计仅为三种配置动作保存 `paths/old_config_hash/new_config_hash`，沿用现有审计表，不记录配置值；其他动作保持原有轻量审计行为。
- 用量 `daily.requests` 统一计数 `ai_usage_records`，与请求汇总口径一致；Agent 的 p50/p95 按同一最近秩算法计算。Gateway 无费用时显示“未提供”，不填零。
- Gateway patch 未附带新 hash 时，在重启前限时回读；若已进入重启、无法回读，审计的新 hash 显式记为 null，保留旧 hash 与已提交键名，网页重连后读取真实版本，不把已成功提交误报为失败。

## 15. 实施与验证状态（2026-09-15）

P0–P4 的代码实现已完成：四个一级入口及旧路由兼容、ClassClaw 配置双视图与安全写入、Gateway 白名单配置、共享 Agent 面板、用量图表、日志排查和运维页面迁入。

- 后端全量回归：`python -m pytest -o addopts='' -q --tb=short`，322 项通过；5 条现有依赖弃用警告。
- 收尾修正：旧版 `web/js/pages/adminAgents.js` 已删除（路由全部为 alias，文件无引用）；`PATCH /admin/openclaw/config` 与 raw 端点共用审计（`gateway_config_patch`，仅键名与 hash）和 409/502 错误模式；数据库概览在用量库抛出非 AppError 故障时仍返回业务库表。
- 前端全量回归：`node --test tests/web/*.test.mjs`，75 项通过。覆盖配置差异与冲突、环境变量锁定、源码校验、路由离开确认、日志增量读取与离页清理、Gateway 离线降级，以及原有聊天和微信绑定生命周期。
- `web/` 全部 JavaScript 文件语法检查通过；配置文档服务、配置目录、日志服务、管理员 schema 及新增配置测试的 Ruff 检查通过；`git diff --check` 通过。

前端测试使用现有 DOM 测试替身，不覆盖浏览器视觉布局。§13 的真实浏览器走查、systemd 重启、Gateway 配置重启与扫码验收仍需在部署环境完成；本次未对真实运行配置或服务执行这些操作。
