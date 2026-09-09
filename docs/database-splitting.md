# 数据库拆分：业务库与 AI 用量库

已实现：核心业务继续保存在 `data/classclaw.db`，AI 调用/Token 记录拆入 `data/usage.db`。二者有独立连接池和写锁。网页 Agent 曾出现的回调死锁已通过无业务写锁等待解决；拆库进一步隔离统计写入，但不会缩短上游模型自身的推理时间。

## 当前依赖

业务库 `Base.metadata` 包含 33 张表、41 条外键；用量库 `UsageBase.metadata` 仅包含无外键的 `ai_usage_records`。班级属于用户，学生、考勤、值日、成绩和作业属于班级或学生；建班草稿同时关联用户和班级，最终确认需要原子创建多个领域的数据。proposal 的归一化 payload 和执行结果中还有跨表业务 ID，即使没有 SQL 外键，也不能视为完全独立。

`app/database.py` 管理业务库，`app/usage_database.py` 管理用量库，各自维护读池和单写连接。用量 SQLite busy、连接池等待、Python 写锁等待各限 250ms，AI 调用在释放业务写锁的区间通过线程池保存统计。统计写入失败只告警并放弃本条，不影响已经成功的 AI 响应，也不会提交/回滚业务会话；没有隐式队列或无限重试。

## 拆分边界

| 数据 | 建议 | 原因 |
| --- | --- | --- |
| 账户、会话、班级及全部教学业务 | 留在核心库 | 存在外键、班级隔离和跨表原子操作；按领域或每班拆库会引入跨库路由及一致性问题。 |
| interaction analyses、proposals、建班草稿 | 留在核心库 | 与幂等、一次性执行、版本校验和业务事务关联。 |
| 关键操作审计 | 留在核心库 | 审计与业务写入共用事务；单看没有外键不足以证明可以独立提交。 |
| `ai_usage_records` | 已拆到独立用量库 | 无外键；记录采用尽力保存语义，失败不应影响业务成功。 |
| `analysis_cache` | 第二候选，可放入可重建缓存库 | 无外键且可从事实重算；仍需保留指纹失效、数量限制及删除后的缓存处理。当前考试统计 GET 会尝试写缓存并在锁冲突时放弃，应单独衡量收益。 |
| 附件正文、运行日志、OpenClaw 会话 | 已在数据库外 | 附件是本地文件，SQLite 保存元数据/引用；日志与 OpenClaw 会话各自保存，拆库不会加速模型推理。 |

SQLite 的外键不能跨 schema；使用 `ATTACH` 也不会让跨库外键成立。当前使用 WAL，涉及多个数据库的提交只能保证每个库自身的原子性，无法保证所有库一起提交。依据：[SQLite 外键限制](https://www.sqlite.org/foreignkeys.html#limits)、[WAL 限制](https://www.sqlite.org/wal.html)、[ATTACH 事务说明](https://www.sqlite.org/lang_attach.html)。因此不能直接把原子建班、整批成绩或 proposal 执行分配到多个 WAL 数据库。

登录次数、业务操作次数和交互分析次数仍由业务库的 `user_sessions`、`audit_logs`、`interaction_analyses` 等事实汇总；用量页面在 Service 层合并展示，响应结构兼容原页面。OpenClaw 原生费用/会话统计继续读取 Gateway 或会话文件，不复制消息正文。管理员数据库浏览按表白名单分别路由到 `core` / `usage`；用量库不可用不会阻止查看业务库。

## 配置与升级

默认无需添加配置。自定义位置在 `config/classclaw.toml` 中设置（相对路径以 TOML 文件目录为基准）：

```toml
[storage]
database_url = "sqlite:///../data/classclaw.db"
usage_database_url = "sqlite:///../data/usage.db"
```

兼容环境变量 `CLASSCLAW_USAGE_DATABASE_URL` 优先于 TOML。两个库不可指向同一文件（包括符号链接/硬链接），用量库不得置于会被重置删除的附件/班级工作区目录内。变更已经投入使用的独立用量库路径时，必须停机搬迁原文件；修改配置不会搜索或搬迁另一个独立库。

升级前先停止旧版 ClassClaw 进程，不能让旧进程继续向原表写入。然后在项目目录执行：

```bash
source .venv/bin/activate
python scripts/backup.py ./backups
alembic upgrade head
python run.py
```

迁移 `0014` 创建独立 schema，以每批 500 条复制旧表、按 UUID 去重，逐条比较全部字段。目标库提交后才移除业务库原表；目标创建/写入/校验失败则原表保留；目标提交后、源提交前中断也可安全重试。同 UUID 内容不同会报错，禁止静默覆盖。不会自动执行 `VACUUM`，因此业务库文件大小不一定立即减小。

启动及 `scripts/init_db.py` 也包含相同的幂等迁移以兼容未使用 Alembic 的部署，但生产推荐显式运行 Alembic。版本仍由业务库中的 Alembic 链管理；`0014` 显式管理外部用量 schema，之后若修改用量表，同样必须新增迁移并明确使用用量引擎，不能依赖业务 `Base` 的 autogenerate。`alembic downgrade 0013` 会把独立记录逐条校验复制回业务库，同时保留独立副本；降级旧应用前先停服，建议先备份。跨库复制需要在线连接，不支持生成离线 SQL 完成数据迁移。

启动时用量初始化失败不会阻止业务服务启动，使用量接口返回 503 `USAGE_DATABASE_UNAVAILABLE`，日志记录初始化失败类型；统计不会显示为零或使用不完整的迁移结果。CLI 初始化和 Alembic 则会以失败退出，便于运维发现问题。修复权限、路径或冲突记录后，停服重新迁移并重启。

## 备份、恢复与完整初始化

`scripts/backup.py` 使用 SQLite Backup API 分别生成 `classclaw.db` 和 `usage.db`，复制附件，并最后写入 `manifest.json`。各库快照单独一致，不是同一时刻的跨库事务快照；需要严格对齐时应停服备份。工作区、OpenClaw 状态、配置和密钥仍需单独备份。

`scripts/restore.py` 先校验备份完整性及清单，双库备份缺少/损坏用量库时不覆盖当前数据；两个目标库均通过 SQLite Backup API 恢复，避免直接复制文件留下的旧 WAL 覆盖恢复结果。兼容含旧用量表的历史单库备份：先恢复业务库、将当前独立用量库重置为空，下一次启动迁移该备份中的旧记录，不混入恢复前的用量。恢复必须停服；两库与附件的替换不是整体原子操作，执行中出错应保持停服并重试完整备份。

网页完整初始化同时清空两库：预清理外部资源成功后先清空用量库，再事务性清空业务库；用量清理失败则不清空业务库，业务清理失败则回滚业务删除并明确报告用量已清空。需要回退时恢复完整停机备份。

## 验证

`tests/test_usage_database.py` 覆盖迁移逐字段一致性、分批、幂等重试、同 ID 冲突、Alembic 升降级、独立锁、统计故障降级和管理员双库浏览。`tests/test_database_backup.py` 覆盖双库备份恢复、旧备份迁移、损坏/缺失拒绝和锁等待截止；用量写入不会提交或回滚业务事务另有回归测试。
