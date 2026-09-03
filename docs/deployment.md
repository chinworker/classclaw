# 单机服务器部署

ClassClaw 的生产边界是单机、单进程、单 Uvicorn worker。推荐让 ClassClaw 与 OpenClaw Gateway 运行在同一台服务器、同一个专用系统用户下，通过 Nginx 或同类反向代理提供 HTTPS；不需要 Docker、Redis 或消息队列。

以下示例假设代码位于 `/opt/classclaw`，运行用户为 `classclaw`。实际路径可替换，但数据库、附件、班级 Agent 工作区和 OpenClaw 状态目录必须持久化且可由该用户读写。

先创建专用用户，并确保代码目录归它所有；后续 Python、Alembic、配置校验和 OpenClaw 命令都应以这个用户执行：

```bash
sudo useradd --system --create-home --shell /bin/bash classclaw
sudo chown -R classclaw:classclaw /opt/classclaw
sudo -u classclaw -H bash
```

## 1. 准备运行环境

```bash
cd /opt/classclaw
python3.12 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp config/classclaw.example.toml config/classclaw.toml
cp .env.example .env
```

编辑 `config/classclaw.toml` 和 `.env`。至少替换三个示例密钥值；`CLASSCLAW_API_TOKEN` 还要与 OpenClaw 的 ClassClaw 插件配置一致，`CLASSCLAW_OPENCLAW_GATEWAY_TOKEN` 要与 Gateway 管理 Token 一致。

生成随机 Token 的一种方式：

```bash
openssl rand -hex 32
```

配置文件、`.env`、数据目录和 OpenClaw 状态目录建议设为仅运行用户可读：

```bash
chmod 600 .env config/classclaw.toml
python scripts/check_config.py --config config/classclaw.toml
alembic upgrade head
```

如果 `storage.openclaw_state_dir` 使用 `~`，它会按执行命令的当前系统用户展开，因此校验、迁移和服务必须使用同一用户；服务器上更推荐填写绝对路径。

可以运行 `python scripts/seed_demo.py` 生成演示数据，但生产环境通常不执行。

## 2. 安装并验证 OpenClaw 集成

按 [OpenClaw 集成说明](openclaw-integration.md) 安装仓库内的 `classclaw` 插件、`classclaw-manager` Skill 和微信兼容层。Gateway 应只监听本机或可信内网，ClassClaw 后端需要能访问其 `/tools/invoke` 与 `/v1/responses`。

如果使用默认 `~/.openclaw`，systemd 的 `User` 必须与启动 Gateway 的用户一致，否则 Agent 统计、会话清理、工作区和微信绑定容易出现权限或状态不一致。

## 3. 配置 systemd

创建 `/etc/systemd/system/classclaw.service`：

```ini
[Unit]
Description=ClassClaw
After=network.target

[Service]
Type=simple
User=classclaw
Group=classclaw
WorkingDirectory=/opt/classclaw
ExecStart=/opt/classclaw/.venv/bin/python run.py
Restart=on-failure
RestartSec=3
UMask=0077

[Install]
WantedBy=multi-user.target
```

`.env` 由应用从项目根目录读取，不需要在 unit 中重复写密钥。若配置文件放在其他位置，可在 `[Service]` 中添加：

```ini
Environment=CLASSCLAW_CONFIG_FILE=/etc/classclaw/classclaw.toml
```

然后启动：

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now classclaw
sudo systemctl status classclaw
curl --fail http://127.0.0.1:8000/health
```

监听地址、端口和访问日志由 TOML `[server]` 控制。不要增加 worker 数量；`run.py` 固定使用一个 worker，SQLite 写入模型和后台会话清理均按单进程设计。

## 4. 配置 HTTPS 反向代理

Nginx 最小站点示例：

```nginx
server {
    listen 443 ssl http2;
    server_name class.example.com;

    client_max_body_size 21m;

    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_read_timeout 180s;
    }
}
```

证书和公网访问控制应按服务器现有方案配置。不要直接把 OpenClaw Gateway 管理端口暴露到公网。上传限制应不小于 `storage.max_attachment_bytes`，代理读取超时应大于网页 AI 请求等待时间。

## 5. 验收与运维

部署后依次检查：

```bash
curl --fail https://class.example.com/health
curl --fail https://class.example.com/api/v1/app-config
```

再打开 `/app/` 登录并完成：管理员改密、OpenClaw 健康状态检查、班主任账户创建、网页 onboarding、文件解析和可选微信二维码绑定。`/app/test.html` 是综合验收台，不建议长期向公网开放，可由反向代理限制来源。

升级前执行 `python scripts/backup.py ./backups`；更新代码和依赖后执行 `alembic upgrade head`，再重启唯一的 ClassClaw 进程。恢复数据库和附件必须先停服务，具体命令见 README 的备份与恢复章节。
