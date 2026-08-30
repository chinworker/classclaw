# ClassClaw 班级管理后端

ClassClaw 是供 OpenClaw 智能体和网页端共同使用的轻量班级管理系统。确定性 REST 能力不依赖 OpenClaw 在线；只有自然语言、文件分析、智能体创建和微信绑定需要 OpenClaw。对话式写入采用“结构化草稿 → 后端预览 → 用户复核 → 一次性执行”，网页结构化操作可以直接调用领域接口。

## 技术栈与运行边界

- Python 3.12、FastAPI、SQLAlchemy 2、Pydantic 2、Alembic、SQLite、pytest、Uvicorn
- 单进程、单 Uvicorn worker，默认时区 `Asia/Shanghai`
- 不使用 Docker、Redis、Celery、消息队列、向量数据库、微服务或服务器本地大模型
- 附件保存在本地目录，SQLite 只记录路径、哈希与元数据

## 安装与启动

```bash
python3.12 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
alembic upgrade head
python scripts/seed_demo.py       # 可选
python run.py
```

服务默认监听 `http://127.0.0.1:8000`，班级创建网页为 `/app/`，后端综合验收台为 `/app/test.html`，健康检查为 `/health`，Swagger 文档为 `/docs`，OpenAPI JSON 为 `/openapi.json`。生产环境建议：

```bash
uvicorn app.main:app --host 127.0.0.1 --port 8000 --workers 1 --no-access-log
```

如不使用 Alembic，也可运行 `python scripts/init_db.py`；应用启动时同样会自动补建缺失表。生产环境应优先使用 Alembic。

## 配置

配置通过环境变量读取，示例见 `.env.example`：

- `CLASSCLAW_DATABASE_URL`：默认 `sqlite:///./data/classclaw.db`
- `CLASSCLAW_ATTACHMENT_DIR`：附件根目录
- `CLASSCLAW_MAX_ATTACHMENT_BYTES`：单附件上限，默认 20 MiB
- `CLASSCLAW_TIMEZONE`：默认 `Asia/Shanghai`
- `CLASSCLAW_LOG_LEVEL`：日志等级
- `CLASSCLAW_API_TOKEN`：API Bearer Token；应替换示例值并与 OpenClaw 插件一致
- `CLASSCLAW_OPENCLAW_GATEWAY_URL`：OpenClaw Gateway 地址，默认 `http://127.0.0.1:18789`
- `CLASSCLAW_OPENCLAW_GATEWAY_TOKEN`：Gateway 管理员 Token，只由后端读取，绝不能发送到浏览器
- `CLASSCLAW_OPENCLAW_AGENT_ID`：负责文件分析的 OpenClaw 智能体，默认 `main`
- `CLASSCLAW_OPENCLAW_CLASS_WORKSPACE_ROOT`：班级专属 agent workspace 根目录
- `CLASSCLAW_OPENCLAW_WECHAT_CHANNEL`：班级微信 channel id，默认 `openclaw-weixin`

SQLite 连接自动启用 `foreign_keys=ON`、`busy_timeout=5000` 和文件数据库的 WAL 模式。所有响应带 `request_id`，数据库锁冲突返回 `DATABASE_BUSY`。

## 已实现功能

- 多班级、当前班级、停用与学生软删除；同班学号唯一、同名歧义候选
- 座位完整时间快照、交换、历史恢复与位置变化
- 结构化值日规则校验、无写入预览、均衡排班、确认、替换、完成、加权评分
- 作业默认 pending、批量状态、迟交判断、未交事件幂等同步
- 学生事件、考勤例外记录与不超过 100% 的半天统计口径
- 考试、科目、整批成绩事务校验、排名与统计
- 基础周课表、稳定 `lesson_key`、临时覆盖、取消、互换、长期批量预览/确认
- 安排、多提醒、逾期计算、附件哈希，以及关键账户/智能体操作的轻量审计
- 学生/班级综合分析、周期对比、关注规则、跨模块同时出现分析、数据质量与每日早报
- 对话式写入 proposal、版本/过期校验、逐次用户确认和原子事务执行
- 网页专属的可恢复班级创建引导；确认后为每个班创建隔离 OpenClaw agent，班主任可选择是否通过二维码绑定独立微信账号
- 唯一管理员与班主任账户；用户名唯一、密码哈希、可撤销会话，以及班主任一账号一班级归属约束
- 管理员用户管理、历史班级分配、智能体状态管理和脱敏只读数据库调试 API
- OpenClaw 原生插件与 `classclaw-manager` Skill，支持任意来源先解析、预览，再请求批准
- 统一 `interaction_analyses`：网页自由文本、微信消息和附件经 OpenClaw 清洗，只保存结构化结果、置信度、澄清问题与关联 proposal，不保存消息原文
- 文件优先 onboarding：名单和课表支持 Markdown、Office、CSV/TSV、PDF、图片、JSON/XML、RTF 与文本；解析后在结构化表格中直接核对编辑

详细设计见 [账户与管理员](docs/accounts-admin.md)、[专属智能体与微信使用说明](docs/class-agent-onboarding.md)、[架构](docs/architecture.md)、[数据库](docs/database.md)、[分析](docs/analytics.md)、[OpenClaw 集成](docs/openclaw-integration.md)、[REST 接口映射](docs/openclaw-tools.md) 和 [对话与实现恢复记录](docs/conversation-recovery.md)。OpenClaw 2026.7 与腾讯微信插件 2.4.6 的网页二维码 provider discovery 兼容层位于 `integrations/openclaw/openclaw-weixin-compat`。

## 测试

```bash
pytest
```

测试使用独立内存 SQLite，覆盖学号唯一与同名、关键审计、座位快照、值日预览确认、作业幂等、考勤口径、成绩整批回滚、调课覆盖、分析、早报、统一响应和班级 onboarding 原子提交。

## 备份与恢复

```bash
python scripts/backup.py ./backups
# 停止服务并核对目录后：
python scripts/restore.py ./backups/classclaw-backup-YYYYMMDD-HHMMSS
python scripts/cleanup_attachments.py
```

备份使用 SQLite Backup API 获得一致性数据库副本，并复制附件目录。恢复会覆盖当前数据库和附件目录，必须先停止服务并先备份现状。无引用附件脚本默认只报告，不自动删除。

## OpenClaw 调用

仓库已包含原生插件 [integrations/openclaw/classclaw](integrations/openclaw/classclaw)。后端用 Gateway `/tools/invoke` 验证 OpenClaw 与插件连接，用 `/v1/responses` 处理网页上传文件。OpenClaw 对话写入先生成预览；用户在聊天中明确确认后，单条或同组批量直接落库，不再弹出额外审批卡。网页结构化业务接口直接执行，不经过智能体或 proposal。完整安装命令见 [OpenClaw 集成与使用](docs/openclaw-integration.md)。

## 保守默认与尚未实现

- 账户角色只有唯一管理员和班主任；班主任只能访问自己绑定的一个班级，共享 Bearer Token 仅供 OpenClaw 服务调用。
- 自然语言、微信、图片和表格解析由 OpenClaw 完成；后端不保存消息原文，只让通过 schema 校验的结构化结果进入待复核 proposal。
- “任意种类”表示 OpenClaw 能读取的类型都可进入相同流程；无法可靠解析的专有格式会保留原件并请用户导出，不会猜测写入。
- 节假日数据不在本地维护，值日跳过日期由规则 `skip_dates` 显式传入。
- 写入预览只接受白名单操作；确认接口在后端调用固定执行器，不接受任意 URL、SQL 或代码。
- 当前实现面向低并发与几十名学生规模；班级级分析使用批量查询，避免按学生重复访问数据库。
