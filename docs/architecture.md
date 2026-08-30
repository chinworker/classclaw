# 架构

请求沿固定分层流动：OpenClaw/网页端 → FastAPI 路由 → Pydantic Schema → Service → SQLAlchemy → SQLite。网页结构化操作可直接进入领域 Service；自然语言和微信写入先经过 proposal 复核。路由负责 HTTP、身份和班级归属，跨表校验、事务、快照、排班、调课与分析位于 Service。

```text
确定性网页字段（班级名称/年级等） ───────────────→ Onboarding 草稿

网页自然语言/粘贴文本/文件 ─→ OpenClaw Responses ─┐
微信消息/附件 ─→ classclaw_analyze_interaction ───┼→ 结构化结果（不保存原文）
OpenClaw 对话式班级引导 ─→ Skill + 受限工具 ──────┘
                                                   │
                                                   ▼
                                       后端 Schema 校验 + Proposal
                                                   │
                                        用户在聊天中一次核对确认
                                                   │
                                                   ▼
                                      固定执行器 + 单事务落库
```

`app/main.py` 注入请求 ID，统一处理业务错误、参数错误和 SQLite busy/locked。`app/api/v1` 按领域拆分路由；`app/services` 实现业务；`app/analytics` 仅基于数据库事实生成结构化指标和证据；`scripts` 提供初始化、种子、备份、恢复和附件维护。

关键边界：没有任意 SQL/Python/URL 执行接口；proposal 只能选择白名单 operation；预览保存归一化 payload、revision 和有效期，确认后由固定执行器完成写入。班主任请求按唯一班级归属隔离。删除学生使用软删除；附件文件名由 UUID 生成；账户、删除和智能体绑定等关键动作写轻量审计，不保存整份前后快照。

OpenClaw 插件是传输与工具边界，Skill 是对话和分析流程边界。插件注册读、附件暂存、interaction analysis、proposal 和提交工具，不暴露创建班级/onboarding mutation；提交钩子只注入可信班级范围，不创建额外审批卡。班级专属 agent 的可信 `agentId` 映射为固定 `class_id`。

`interaction_analyses` 保留渠道、外部消息 ID、附件引用、选定班级、OpenClaw agent、结构化输出、置信度、警告、澄清问题及 proposal IDs，但不保留消息原文。只有 `awaiting_review` 状态会包含经过后端校验的 proposal，仍不表示已写入业务表。

班级 onboarding 只能由网页创建和修改。文件经无历史的 OpenClaw extraction turn 全量替换目标列表，人工修改结构化表格后不再经过模型。最终确认在一个事务中创建班级、从课表归纳的科目、学生、节次、基础课表和 agent binding 记录；随后以可重试的外部流程创建 OpenClaw agent、微信登录与 route binding。低配置部署使用一个 Uvicorn worker。SQLite 使用 WAL、外键和 5 秒 busy timeout。
