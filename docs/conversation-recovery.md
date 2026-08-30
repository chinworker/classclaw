# 对话与实现恢复记录

> 2026-08-30 更新：下文第 3 条“双入口创建班级”属于早期需求，现已被后续决定取代。当前只有网页能创建班级；创建后自动 provision 班级专属 agent，微信绑定由班主任选择。名单/课表只通过文件导入，人工编辑结构化表格不会再次经过模型。详见 [专属智能体与微信使用说明](class-agent-onboarding.md)。

仓库当前没有 Git 提交，无法从 commit、branch 或 reflog 恢复原始逐字对话。本记录依据现有代码、测试、README、OpenClaw 插件清单和 Skill 指令重建此前需求与实现顺序；内容可用于后续继续开发，但不等同于原聊天原文。

## 恢复出的需求演进

1. 最初目标是按照 `classclaw_prompt.md` 与“班主任工作台复刻提示词”构建 ClassClaw。
2. 随后明确：用户可以把文字、图片、PDF、表格等班级信息交给 OpenClaw；OpenClaw 分析后调用 API，但所有正式写入必须再次与用户复核。
3. 新班级需要两个入口：与 OpenClaw 智能体对话创建，或通过网页引导创建；两端共享班级信息、学生名单、学生资料、科目、节次和课表草稿。
4. 技术方案确定为三层组合，而不是只用 Skill：
   - OpenClaw Plugin 提供受限工具、附件上传和班级范围钩子；
   - `classclaw-manager` Skill 规定解析、证据、消歧和确认流程；
   - FastAPI 后端用 proposal、版本、哈希、有效期和固定执行器强制安全边界。
5. 之后又明确 OpenClaw 是必要连接：Gateway 或插件不可用时，ClassClaw 除连接检查外的 API 都必须停止；网页 onboarding 的名单、课表等支持拖拽并交给 OpenClaw 处理。
6. 本轮补充要求：网页和微信的所有非确定性输入都必须先经 OpenClaw 分析清洗，再由 API 保存有组织的数据。

## 此前已经存在的实现

- FastAPI、SQLAlchemy、SQLite、Alembic、统一响应和审计日志。
- 班级、学生、座位、值日、作业、事件、考勤、考试成绩、课表调课、安排提醒和分析接口。
- `write_proposals` 白名单流程：结构化 payload → 后端校验预览 → 用户确认 → 固定执行器事务写入。
- `class_onboarding_sessions`：OpenClaw 与网页共享 session/revision，最终原子创建班级、科目、学生、节次和课表。
- （已移除）旧版曾用全局中间件拦截直接业务写接口和 OpenClaw 离线时的所有 API；当前仅非确定性分析与智能体/微信功能依赖 OpenClaw，结构化领域 API 保持可用。
- 网页五步创建向导、最终复核、重复输入班级名称和确认按钮。
- 文件拖拽：保存原件与 SHA-256，XLSX/DOCX 安全提取，CSV/PDF/图片/JSON/文本通过 OpenClaw Responses API 回填草稿。
- OpenClaw 原生插件、受限工具、可信班级范围钩子和 `classclaw-manager` Skill。

## 本轮完善

- 新增 `interaction_analyses`，记录网页/微信自然语言、附件 ids、OpenClaw 结构化结果、置信度、警告、澄清问题和 proposal ids。
- 新增 `POST /api/v1/interaction-analyses`，统一把自然语言、微信消息和附件交给 OpenClaw；只有通过后端 schema 校验的操作才生成待复核 proposal。
- 插件新增 `classclaw_analyze_interaction`，作为微信和自然语言的默认入口。
- 网页不再在浏览器内直接拆分科目、名单和课表文本；保存或最终预览时先调用 OpenClaw 清洗。班级名称、年级等确定性表单字段仍可直接写入 onboarding 草稿。
- 后续已改为一次确认：ClassClaw 在聊天中展示预览，用户明确肯定后直接提交；同组多条原子写入，不再使用 OpenClaw 审批卡。

如果需要恢复未来的精确变更历史，应从当前状态开始建立 Git 基线提交，并在每个功能阶段提交；当前未自动创建提交，以免替用户决定版本历史。
