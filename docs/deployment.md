# Ubuntu 24.04 · 2 核 4 GB 同机部署指南

适用场景：**Ubuntu 24.04 LTS，一台 2 vCPU / 4 GB RAM 服务器，同机运行 ClassClaw、OpenClaw Gateway 和 Nginx，模型使用远程 API。** 检查日期：2026-09-15。

结论：现有架构适合作为这类小服务器的起点。主要资源压力来自 OpenClaw、文件解析、多个并行 AI 请求，以及安装依赖时的峰值。先用少量班级上线，记录实际内存和响应时间，再决定是否扩容。**本文的内存预算是部署参数，不是目标服务器的实测结果，也不承诺固定在线人数或 QPS。**

## 1. 配套文件与检查结论

| 文件 | 作用 |
| --- | --- |
| [deploy/classclaw](../deploy/classclaw)、[scripts/manage.py](../scripts/manage.py) | `classclaw restart / upgrade / backup / doctor` 等管理命令 |
| [classclaw.service](../deploy/systemd/classclaw.service) | 单 worker 后端、启动配置校验、内存限制 |
| [openclaw-gateway.service](../deploy/systemd/openclaw-gateway.service) | 同用户运行 Gateway、内存限制、自动重启 |
| [classclaw.2c4g.toml](../deploy/classclaw.2c4g.toml) | 固定绝对路径和小服务器初始配置 |
| [Nginx 站点](../deploy/nginx/classclaw.conf) | SSE 流式响应、上传限制、反向代理 |
| [备份 service](../deploy/systemd/classclaw-backup.service)、[timer](../deploy/systemd/classclaw-backup.timer) | 可选凌晨停机备份，需单独启用 |
| [requirements-runtime.txt](../requirements-runtime.txt) | 生产 Python 依赖；开发继续使用 `requirements.txt` |

### 1.1 已有优化与本次适配

| 检查项 | 代码现状 | 部署处理 |
| --- | --- | --- |
| Python 进程 | `run.py` 固定 `workers=1` | 保留；进程内写锁、聊天状态和后台清理都按单进程设计，2 核不等于要开 2 个 worker |
| SQLite | 业务库和用量库分离，各自读池/单写者，启用 WAL、busy timeout | 保留，数据库放本机 SSD，不引入额外数据库服务 |
| 上传 | `operations.save_attachment()` 按 1 MiB 分块写盘，默认单文件 20 MiB | 小服务器模板降为 10 MiB；Nginx 整个请求体限制为 21 MiB |
| 文件分析 | 交给 Gateway 时部分文件会整体读取并编码；Office 解析也有额外内存 | 大文件依次处理；上传大小不等于解析时内存用量 |
| HTTP | 已复用 `httpx.AsyncClient`，AI 等待期间释放业务写锁 | 保留，避免缩小连接数后堵住 Gateway 回调 |
| 网页 | 原生 HTML/JS/CSS，无前端构建或 Node 前端进程 | 继续由 FastAPI 提供；资源尚无版本哈希，不添加长期浏览器缓存 |
| 流式聊天 | 已有 SSE、10 秒心跳及 `X-Accel-Buffering: no` | Nginx 显式关闭响应缓冲、代理缓存，读取超时 180 秒 |
| 日志 | JSON 日志约 2 MiB/文件，另保留 2 个轮转文件 | 保留 INFO；关闭重复访问日志，限制 journald 磁盘量 |
| 分析缓存 | `analysis_cache` 在数据库中，默认最多 500 条 | 模板设为 200；控制的是缓存表规模，不是立即释放 300 个内存对象 |
| 二维码查询 | 默认 2 秒轮询 | 模板常规轮询改为 3 秒；短轮询、二维码刷新和验证码流程保留 |
| Python 依赖 | 原先运行与测试依赖混用 | 拆出运行依赖，生产不安装 pytest；主要减少安装内容，不能声称常驻内存因此明显下降 |
| 发布维护 | 原文档只有手动更新/重启步骤 | 增加运维互斥锁、Git 快进检查、停机全量备份、迁移、健康检查和失败标记 |

不在本机安装桌面环境、本地大模型、无关浏览器自动化常驻进程、Docker、Redis、Celery 或额外任务队列，它们不是项目运行依赖。

### 1.2 内存预算

| 部分 | 模板设置 | 含义 |
| --- | --- | --- |
| ClassClaw | `MemoryHigh=512M`、`MemoryMax=1G` | 包括后端及其发起的 OpenClaw CLI 等子进程 |
| OpenClaw Gateway | `MemoryHigh=1536M`、`MemoryMax=2G` | 包括 Gateway 和子进程；Node 老生代堆为 1280 MiB |
| 系统、Nginx、SSH、文件缓存 | 两项服务硬上限合计外预留约 1 GiB | 不要同时安装大量后台服务 |
| 可选 swap | 2 GiB | 缓冲短时峰值；持续换页意味着需要降负载或扩容 |

`MemoryHigh` 在内存压力下触发回收/节流，`MemoryMax` 是最后的硬限制，超限可能导致进程被杀并重启；它们不是预先占用的内存。设置后须观察文件导入和 CLI 子进程峰值。依据：[systemd 资源控制说明](https://raw.githubusercontent.com/systemd/systemd/v255/man/systemd.resource-control.xml)。

Node 的 `--max-old-space-size` 不包含全部 Buffer、原生模块等内存，不能直接设成服务器的 4 GB。依据：[Node.js 参数说明](https://nodejs.org/api/cli.html#--max-old-space-sizesize-in-mib)。

### 1.3 并发与 AI 边界

- 先按少量教师、串行大文件导入验收，再测多班同时使用的峰值。
- 保留独立提取 Agent，它是一套远程模型配置，不是本机额外加载一个模型。
- **不要简单把 Gateway 的 `agents.defaults.maxConcurrent` 设为 1。** 聊天 Agent 可能等待 ClassClaw 工具，而工具又发起一次提取 Agent 调用。过小的全局并发限制可能使外层占满槽位、内层无法运行。本文保留安装版本的并发配置。
- 不设置很低的 Uvicorn 全局并发限制来代替 AI 限流：SSE、二维码查询、业务接口和插件回调共用入口，限制过低会一起拒绝。Uvicorn 达到该限制会直接返回 503，见 [官方配置](https://raw.githubusercontent.com/encode/uvicorn/master/docs/settings.md)。
- `class_agent_thinking=off` 沿用项目默认，网页仍根据 Gateway 返回的模型能力选择档位，不硬编码能力表。
- 若负载增加，下一项值得开发的是**进入外层聊天前限制同时进行的 AI 对话，并为内部提取请求保留处理能力**。当前并未实现这一独立业务准入机制。

本地少量对话样本显示主要等待在模型生成与提取，不能据此推导服务器容量；容量仍以本机实际导入和对话验收为准。

## 2. 目录、端口与拓扑

```text
教师浏览器 ── HTTPS :443 ── Nginx ── 127.0.0.1:8000 ── ClassClaw
                                                        │
                                      本地双 SQLite / 附件 / 工作区
                                                        │
                                                127.0.0.1:18789
                                                        │
                                                OpenClaw Gateway
                                                        │
                                                远程模型 API / 微信

OpenClaw 插件回调 ── 127.0.0.1:8000 ── ClassClaw REST
```

```text
/opt/classclaw/                         Git 工作区，保持可快进更新
  .venv/                               服务器本地 Python 虚拟环境
  .env                                 密钥，600 权限
  config/classclaw.toml                 非敏感配置，600 权限
  data/classclaw.db                     业务库
  data/usage.db                         AI 用量库
  data/attachments/                     附件
  data/openclaw-agents/                 班级与提取 Agent 工作区
  data/logs/                           后端轮转日志
  backups/                             完整备份与升级失败标记
/home/classclaw/.openclaw/              Gateway 状态、账号与会话
/home/classclaw/.npm-global/            固定位置安装 OpenClaw CLI
/usr/local/bin/classclaw                管理命令启动器
/usr/local/lib/classclaw/manage.py      root 所有的运维脚本副本
```

两个进程均以专用系统用户 `classclaw` 运行。8000 和 18789 只监听回环地址；云安全组只需放行受限来源的 SSH，以及网页 80/443。服务器须能出站访问代码/包仓库、模型 API 和微信服务。

**管理脚本针对上述两个 systemd 系统级 unit。** 不要同时使用 `nohup`、PM2、`openclaw onboard --install-daemon` 或另一套用户级 Gateway 服务。脚本不能停止手工启动的其他进程。

磁盘使用本地 SSD，可按 40 GB 起步，再按附件增长调整。每份完整备份都会复制双库、附件和 OpenClaw 状态；空间至少覆盖计划保留的备份，并额外预留依赖安装空间。SQLite 不能放 NFS/网络共享盘。

## 3. 准备 Ubuntu

以下在服务器的普通运维账号中执行，需要 `sudo`。假定新服务器；已有同名用户、目录或站点时，先核对后复用。

```bash
sudo apt update
sudo apt install -y python3.12 python3.12-venv python3-pip git curl ca-certificates \
  nginx sqlite3 rsync nano build-essential pkg-config
sudo timedatectl set-timezone Asia/Shanghai

sudo useradd --system --create-home --home-dir /home/classclaw --shell /bin/bash classclaw
sudo chmod 700 /home/classclaw
sudo install -d -o classclaw -g classclaw -m 755 /opt/classclaw
sudo -u classclaw -H git clone https://github.com/chinworker/classclaw.git /opt/classclaw
```

如仓库私有，给 `classclaw` 用户配置只读 Git 凭据；升级的 Git 操作由该用户执行。不要把 Token 写进 Git remote URL。

### 3.1 安装 Node.js

选择 **Node.js 24 LTS**。当前插件锁文件中的 OpenClaw 为 `2026.7.1-2`，声明 Node 24 至少为 `24.15.0`；后续版本要求可能不同。首次上线先保持全局 Gateway 与插件锁文件版本一致，不能用宽泛的 `latest` 代替兼容性检查。

可使用 NodeSource 的发行包，见 [NodeSource 安装说明](https://github.com/nodesource/distributions/blob/master/DEV_README.md)：

```bash
curl -fsSL https://deb.nodesource.com/setup_24.x -o /tmp/classclaw-nodesource-setup.sh
# 核对下载脚本后执行；它会配置 NodeSource apt 软件源。
sudo bash /tmp/classclaw-nodesource-setup.sh
sudo apt install -y nodejs
node --version
npm --version
```

配套 unit 使用 `/usr/bin`、`/usr/local/bin` 和固定的 `.npm-global/bin`，不依赖 nvm 的交互 shell 初始化。若采用其他位置，须同时调整两个 unit 和管理脚本的 PATH。

### 3.2 可选：增加 2 GiB swap

先检查，已有合适 swap 可跳过：

```bash
free -h
swapon --show
df -h /
```

仅在需要新建时执行。已存在同名文件时该块会退出，不覆盖：

```bash
sudo bash <<'SH'
set -eu
test ! -e /swapfile-classclaw
fallocate -l 2G /swapfile-classclaw
chmod 600 /swapfile-classclaw
mkswap /swapfile-classclaw
swapon /swapfile-classclaw
printf '/swapfile-classclaw none swap sw 0 0\n' >> /etc/fstab
SH
```

## 4. 初始化 ClassClaw

进入专用用户 shell；本节剩余命令均以它执行：

```bash
sudo -u classclaw -H bash
cd /opt/classclaw
umask 077
python3.12 -m venv .venv
.venv/bin/python -m pip install --upgrade pip
.venv/bin/python -m pip install --no-cache-dir -r requirements-runtime.txt
.venv/bin/python -m pip check

cp deploy/classclaw.2c4g.toml config/classclaw.toml
mkdir -p data/attachments data/openclaw-agents data/logs backups /home/classclaw/.openclaw
chmod 700 data backups /home/classclaw/.openclaw
```

首次部署生成三个独立随机值，不在终端打印。已有 `.env` 时以下脚本拒绝覆盖：

```bash
.venv/bin/python - <<'PY'
from pathlib import Path
import secrets

names = (
    "CLASSCLAW_API_TOKEN",
    "CLASSCLAW_OPENCLAW_GATEWAY_TOKEN",
    "CLASSCLAW_DEFAULT_ADMIN_PASSWORD",
)
with Path(".env").open("x", encoding="utf-8") as file:
    for name in names:
        file.write(f"{name}={secrets.token_hex(32)}\n")
Path(".env").chmod(0o600)
PY
chmod 600 config/classclaw.toml
.venv/bin/python scripts/check_config.py
.venv/bin/python -m alembic upgrade head
```

初始管理员用户名是 `admin`，密码为 `.env` 中刚生成的 `CLASSCLAW_DEFAULT_ADMIN_PASSWORD`。在私密终端编辑器中查看，首次网页登录后修改；新服务器不运行 `seed_demo.py`。

配置由 TOML 与 `.env` 统一读取。管理命令不把 `.env` 当 shell 脚本执行，也不继承运维账号临时设置的 `CLASSCLAW_*` 变量。外置 TOML 请在项目 `.env` 设置 `CLASSCLAW_CONFIG_FILE`，不要只在 systemd 中设置应用配置覆盖，见 [配置说明](configuration.md)。

运行依赖文件仍是版本范围，不是完全锁定的发行依赖。升级不使用 `pip --upgrade` 强制刷新所有已满足范围的包，并保存 `pip freeze`；若要求跨服务器完全复现，发布流程还需维护经 Linux 验证的锁文件/离线 wheel 包。

## 5. 安装同机 OpenClaw 和插件

继续使用上一节的 `classclaw` shell：

```bash
npm config set prefix /home/classclaw/.npm-global
export PATH="/home/classclaw/.npm-global/bin:$PATH"
npm install --global openclaw@2026.7.1-2
openclaw --version
openclaw onboard
```

在 onboarding 中配置远程模型 Provider 和凭据，选择本地 Gateway，**不安装另一套 daemon**。不下载本地模型，不启用无关浏览器或扩展。若 npm 启用了全局生命周期脚本审批，按提示允许 OpenClaw 安装脚本；安装方式差异见 [OpenClaw 官方安装文档](https://docs.openclaw.ai/install)。

### 5.1 构建并链接插件

两个目录依次处理，避免并行构建。ClassClaw 插件的验证命令需要 TypeScript 和 OpenClaw SDK，必须保留构建依赖。

```bash
cd /opt/classclaw/integrations/openclaw/classclaw
NODE_OPTIONS=--max-old-space-size=1024 npm ci --include=dev --ignore-scripts --no-audit --no-fund
NODE_OPTIONS=--max-old-space-size=1024 npm run plugin:validate

cd /opt/classclaw/integrations/openclaw/openclaw-weixin-compat
npm ci --omit=peer --ignore-scripts --no-audit --no-fund

cd /opt/classclaw
openclaw plugins install --link ./integrations/openclaw/classclaw
openclaw plugins enable classclaw
openclaw plugins enable admin-http-rpc
openclaw plugins install --link ./integrations/openclaw/openclaw-weixin-compat
openclaw config set plugins.entries.openclaw-weixin.enabled true
```

`classclaw-manager` Skill 随插件安装。微信兼容层 `2.4.6-classclaw.3` 固定上游微信插件 `2.4.6`，必须保留其网页登录状态机，详见 [兼容层说明](../integrations/openclaw/openclaw-weixin-compat/README.md)。

兼容层 `.4` 提供带已保存账号清单的班级级扫码取消和 `channels.logout` 账号登出能力，班级删除依赖它们清理微信凭据。升级顺序固定为先更新兼容层（并在空闲时重启 Gateway），再升级后端；在旧兼容层上删除班级会停在 `WECHAT_PLUGIN_UPDATE_REQUIRED`，更新后从失败阶段重试即可，业务数据尚未删除。

### 5.2 同步两个 Token

Gateway 尚未启动时，在私密终端编辑器中打开 `.env` 和 `/home/classclaw/.openclaw/openclaw.json`，保留 onboarding 生成的其他字段，只合并下面的设置：

| OpenClaw 配置字段 | 取值 |
| --- | --- |
| `gateway.auth.mode` | `token` |
| `gateway.auth.token` | `.env` 的 `CLASSCLAW_OPENCLAW_GATEWAY_TOKEN` |
| `plugins.entries.classclaw.config.apiToken` | `.env` 的 `CLASSCLAW_API_TOKEN` |

不要把明文 Token 放进 shell 命令参数、历史记录或前端代码。可用 `nano .env`、`nano /home/classclaw/.openclaw/openclaw.json` 编辑，然后设置权限并配置其他非敏感项：

```bash
chmod 600 /home/classclaw/.openclaw/openclaw.json
openclaw config set gateway.mode local
openclaw config set gateway.bind loopback
openclaw config set gateway.http.endpoints.responses.enabled true
openclaw config set plugins.entries.classclaw.config.baseUrl http://127.0.0.1:8000
openclaw config set plugins.entries.classclaw.config.timeoutMs 120000
openclaw config set tools.alsoAllow '["classclaw","classclaw_commit_write","classclaw_commit_writes"]' --strict-json
openclaw config set session.dmScope per-account-channel-peer
openclaw plugins list
openclaw skills list
```

若已有其他工具白名单，合并 `tools.alsoAllow`，不要覆盖它们。本地媒体上传按需授权最小目录，例如：

```bash
openclaw config set plugins.entries.classclaw.config.allowedUploadRoots \
  '["/home/classclaw/.openclaw/media"]' --strict-json
```

不能授权整个 `/` 或 `/home`。完整接口与班级创建说明见 [OpenClaw 集成](openclaw-integration.md)、[班级 Agent 与微信](class-agent-onboarding.md)。退出专用用户 shell：

```bash
exit
```

## 6. 安装服务与 `classclaw` 命令

```bash
sudo install -d -m 755 /usr/local/lib/classclaw
sudo install -m 644 /opt/classclaw/scripts/manage.py /usr/local/lib/classclaw/manage.py
sudo install -m 755 /opt/classclaw/deploy/classclaw /usr/local/bin/classclaw
sudo install -m 644 /opt/classclaw/deploy/systemd/classclaw.service /etc/systemd/system/classclaw.service
sudo install -m 644 /opt/classclaw/deploy/systemd/openclaw-gateway.service /etc/systemd/system/openclaw-gateway.service
sudo systemd-analyze verify /etc/systemd/system/classclaw.service /etc/systemd/system/openclaw-gateway.service
sudo systemctl daemon-reload
sudo systemctl enable classclaw.service openclaw-gateway.service
sudo classclaw start
sudo classclaw doctor
```

启动顺序为 ClassClaw、Gateway；Gateway 暂时不可用时后端仍提供确定性 REST，AI 首次使用时会重试准备。首次初始化 Agent 配置可能触发 Gateway 重启，待服务稳定后再做网页 AI 验收。

Gateway 的 `Restart=always` 用于正常退出后的受监督重启，执行 `systemctl stop` 不会被它自动拉起；后端为 `Restart=on-failure`。停止过程最多等待 180 秒，操作前仍应等待进行中聊天结束。依据：[systemd 服务语义](https://raw.githubusercontent.com/systemd/systemd/v255/man/systemd.service.xml)。

### 常用命令

```bash
sudo classclaw status
sudo classclaw health
sudo classclaw doctor
sudo classclaw version

sudo classclaw restart                  # 重启后端与 Gateway
sudo classclaw restart --backend-only   # 仅重启 ClassClaw
sudo classclaw stop                     # 停两项服务
sudo classclaw start                    # 启两项服务并检查存活

sudo classclaw logs -n 100              # 后端进程启动/退出日志
sudo classclaw logs --app -f            # 后端业务 JSON 日志，追踪轮转
sudo classclaw logs --gateway -f        # Gateway 日志

sudo classclaw backup                   # 停机完整备份，恢复原运行状态
sudo classclaw upgrade --check          # fetch upstream，展示计划，不停服
sudo classclaw upgrade                  # 备份、更新、依赖/插件/迁移、恢复运行状态
classclaw --help
```

启动器和运维脚本副本由 root 所有，避免用 root 直接执行服务用户可改写的管理代码。脚本把 Git、pip、Alembic、插件构建、备份和元数据写入降为 `classclaw` 用户执行，该用户无需 sudo 权限，应用也不以 root 运行。

`health` 只检查 HTTP 存活，**不代表数据库、插件、Provider 和微信全部正常**；`doctor` 额外检查配置、Python 依赖、unit 状态和内存。完整可用性须完成第 8 节验收。

## 7. HTTPS 与 Nginx

准备解析到服务器公网 IP 的域名，下例为 `class.example.com`。先安装 HTTP 站点，再添加 TLS，避免配置引用尚不存在的证书：

```bash
sudo install -m 644 /opt/classclaw/deploy/nginx/classclaw.conf /etc/nginx/sites-available/classclaw
sudo nano /etc/nginx/sites-available/classclaw
# 将 server_name 改成自己的域名。
sudo ln -s /etc/nginx/sites-available/classclaw /etc/nginx/sites-enabled/classclaw
sudo nginx -t
sudo systemctl reload nginx

sudo apt install -y certbot python3-certbot-nginx
sudo certbot --nginx -d class.example.com --redirect
sudo nginx -t
sudo certbot renew --dry-run
sudo systemctl list-timers --all
```

已有同名链接时复用，不覆盖其他站点。在定时器列表确认 Certbot 续期任务。也可选择 [Certbot 官方 snap 安装方式](https://certbot.eff.org/instructions?ws=nginx&os=snap)，两种方式择一。

没有域名时先用 SSH 隧道验收，在自己的电脑运行：

```bash
ssh -L 8000:127.0.0.1:8000 your-operator@your-server
# 浏览器访问 http://127.0.0.1:8000/app/
```

公网 HTTP 登录不能作为长期方案。云安全组和本机防火墙都要保留 SSH，并放行 80/443，无须放行 8000/18789。使用 UFW 时，先按实际 SSH 端口放行并验证另一条 SSH 会话，再启用防火墙。

### 代理参数说明

- `proxy_buffering off` 及时传递 SSE，`proxy_cache off` 禁止缓存账号/班级接口。
- `proxy_read_timeout 180s` 是两次读取之间的超时，不是整轮总时限；后端 Gateway 上限仍为 120 秒，网页默认等待 150 秒。
- 保留请求缓冲，慢客户端先把有大小上限的上传交给 Nginx，减少后端占用。
- 关闭上游自动重试，避免维护故障时重放写入。
- 只代理 ClassClaw，不代理 Gateway。模板限制公网 `/docs`、`/redoc`、`/openapi.json` 和 `/app/test.html`，这些页面可通过 SSH 隧道检查。

依据：[Nginx proxy 模块](https://nginx.org/en/docs/http/ngx_http_proxy_module.html)。模板按 Nginx 是唯一公网入口编写；若增加 CDN/负载均衡，还需检查它的 SSE、超时、缓存和可信来源设置。

## 8. 首次上线与容量验收

```bash
sudo classclaw doctor
curl --fail https://class.example.com/health
curl --fail https://class.example.com/api/v1/app-config
sudo ss -ltnp
```

在 `/app/` 逐项完成：

1. 管理员登录并修改初始密码，确认公开配置不含密钥或数据路径。
2. 确认 Gateway、ClassClaw 插件和 admin RPC 可用，模型目录可读取。
3. 创建班主任账号，从网页 onboarding 创建班级并核对名单/课表。
4. 测试学生、作业、考勤等结构化操作；短暂停 Gateway 后，已建班级的这些操作仍应可用。
5. 上传小文件验证预览确认，再用接近 10 MiB 的正常文件测内存峰值。
6. 网页聊天持续收到 SSE，最终有完成事件；断流不能当成功。验证一次“预览 → 明确确认 → 写入”。
7. 用两个班主任账号验证班级隔离，聊天进行时站内切换页面不应取消已发送请求。
8. 在账户设置测试微信新二维码、轮询及必要的数字验证码；重启丢失正在扫码的内存状态后须重新生成二维码。
9. 完整备份一次，并在隔离目录或备用机器演练恢复。

第 4 项使用 `sudo systemctl stop openclaw-gateway`，验证后执行 `sudo systemctl start openclaw-gateway`，不要在真实写入/对话期间做故障演练。

分别观察以下指标：

```bash
free -h
vmstat 1
```

```bash
sudo systemd-cgtop
sudo systemctl show classclaw openclaw-gateway -p MemoryCurrent -p MemoryPeak -p NRestarts
sudo journalctl -k --since today --grep='oom\|Out of memory\|Killed process'
```

比较空闲、普通页面、单次导入、两个同时对话的负载。持续 swap in/out、OOM、频繁重启、普通业务也变慢时，先停止大文件并发，再决定扩容；不要直接把两个 MemoryMax 都扩大到 4 GB。

## 9. 升级流程

先把部署改动提交并推送到服务器跟踪的分支。`upgrade` 只获取远程已有代码，不上传本地电脑的未提交内容。

无进行中聊天时执行；较长更新可使用 `tmux` 会话避免 SSH 断开中止运维进程，日常服务仍只由 systemd 管理：

```bash
sudo classclaw upgrade --check
sudo classclaw upgrade
sudo classclaw doctor
```

`upgrade` 按以下顺序执行：

1. 检查 Git，拒绝脏工作区、未跟踪文件、detached HEAD、无远程 upstream 或无法快进的分支。
2. fetch 后固定目标提交，`--check` 到此结束，不更新工作区、不停服务。
3. 校验当前配置和两个 unit，记录原运行状态。
4. 先停 Gateway，再停 ClassClaw，停止成功后才备份。
5. 保存双库、附件、配置、工作区、OpenClaw 状态、代码归档、Python 依赖版本。
6. 写入 `backups/.upgrade-pending.json`，然后快进更新，不自动 stash/reset 或清理文件。
7. 安装运行依赖并 `pip check`；涉及 `integrations/openclaw/` 变更时，按锁文件串行安装两个插件依赖，执行 ClassClaw 插件构建/契约校验。
8. 用新代码校验配置，执行 `alembic upgrade head`。
9. 依赖和迁移成功后解除启动保护，恢复原来运行的服务并检查 HTTP 存活；原已停止的服务继续保持停止。
10. 健康检查失败时重新保留升级标记并请求停服；成功则提示网页验收。

停机维护阶段失败时会请求停止两项服务、报告原版本和备份位置。迁移不是整个发布过程的原子事务，**不会用旧代码自动启动可能已迁移的数据库**。标记存在时再次 `start/restart/upgrade` 会拒绝，两个 systemd unit 也会阻止重启机器后自动启动，按第 11 节处理。

运维锁位于 `/run/lock/classclaw-admin.lock`；它只协调脚本管理的操作，不阻止你手动执行的 systemctl、pip、SQL 或其他脚本。维护期间不要混用手动修改。

### 升级命令的范围

| 内容 | 处理方式 |
| --- | --- |
| `.env`、`config/classclaw.toml` | 保留本机设置；新版校验不兼容时停服报告，按发行说明修改 |
| 已安装 Nginx/systemd 配置 | 查看 `deploy/` 差异后人工合并到 `/etc`，执行配置验证和 reload |
| 已安装的管理命令副本 | 查看 `scripts/manage.py` / `deploy/classclaw` 差异，核对后重新执行第 6 节的两条文件安装命令；不自动替换 root 运行的脚本 |
| 全局 OpenClaw、Node.js | 独立维护，先验证 SDK、admin RPC、微信兼容层 |
| 操作系统更新 | Ubuntu 自身维护流程 |

`--check` 展示提交和插件重建计划，不是完整兼容性审计。全局 OpenClaw 升级前要备份并停两项服务，以服务用户安装明确版本，验证插件后再启动，不能在 Gateway 运行中用 `npm install -g openclaw@latest` 覆盖它。

## 10. 备份与日志保留

### 10.1 完整停机备份

```bash
sudo classclaw backup
```

默认保存到 `/opt/classclaw/backups/classclaw-backup-时间戳/`：

```text
classclaw.db / usage.db / attachments/    原备份脚本生成的业务快照
manifest.json                           双库清单
deployment.env / classclaw.toml          配置及密钥
openclaw-agents/                         班级/提取器工作区（原目录存在时）
openclaw-state/                          Gateway 状态及凭据（原目录存在时）
requirements.freeze.txt                  Python 精确安装版本
source.tar                              当前 HEAD 的已跟踪代码归档
deployment.json                         原提交、目标提交、原路径和实际复制项目
```

只有最后的 `deployment.json` 存在，才表示扩展的部署备份全部完成；只有 `manifest.json` 不能证明工作区/配置也已备份成功。未提交代码不进入 `source.tar`，生产应保持工作区干净。

两个服务停止后，不再有应用写入，但复制过程本身不整体原子；不要与其他外部写进程同时运行。底层在线备份则只有各库单独一致，附件/其他状态不保证同一时刻，见 [双库备份边界](database-splitting.md)。

完整备份含学生资料、密钥、OpenClaw 会话及登录凭据，仅服务用户/root 可读。符号链接会保留，链接到范围外的文件不会自动归档。全局 OpenClaw/Node/Python 二进制、外部工作区、`/etc` 配置、HTTPS 证书还需另行备份；freeze 文件也不是离线安装包。

支持指定服务用户可写的备份盘：

```bash
sudo install -d -o classclaw -g classclaw -m 700 /mnt/classclaw-backups
sudo classclaw backup --directory /mnt/classclaw-backups
```

不能把目标放进附件、工作区或 `.openclaw` 内，脚本会拒绝循环复制路径。

### 10.2 可选每日 03:30 备份

这会短暂停机，业务和微信在备份期间不可用。确认维护时间后再启用：

```bash
sudo install -m 644 /opt/classclaw/deploy/systemd/classclaw-backup.service /etc/systemd/system/classclaw-backup.service
sudo install -m 644 /opt/classclaw/deploy/systemd/classclaw-backup.timer /etc/systemd/system/classclaw-backup.timer
sudo systemctl daemon-reload
sudo systemctl enable --now classclaw-backup.timer
sudo systemctl list-timers classclaw-backup.timer
sudo journalctl -u classclaw-backup.service --since today
```

定时器不补跑关机期间错过的任务，避免开机后白天突然停服务。备份失败会保留停服状态，应对备份失败/服务不可用设置服务器侧告警。

可按“最近 7 份日备份 + 4 份周备份”起步，定期复制到异机或加密备份存储。**脚本不自动删除旧备份**，需要按磁盘容量清理或转移；同一磁盘的副本不能应对整盘损坏。

### 10.3 日志与会话

后端 JSON 日志已有约 6 MiB 的轮转总量；Nginx 错误日志使用发行版 logrotate。journald 可设置全系统上限：

```bash
sudo mkdir -p /etc/systemd/journald.conf.d
sudo nano /etc/systemd/journald.conf.d/classclaw.conf
```

写入：

```ini
[Journal]
SystemMaxUse=200M
RuntimeMaxUse=50M
MaxRetentionSec=7day
```

```bash
sudo systemctl restart systemd-journald
sudo journalctl --disk-usage
```

这是系统级策略，会影响其他服务；已有统一日志策略时遵循原策略。OpenClaw 自己写入的日志/会话文件不受 journald 上限限制，须另外观察其目录大小。

提取会话默认每 24 小时经 OpenClaw CLI 清理；应检查实际结果和安装版本的会话维护策略，聊天会话长期复用，不能为腾空间批量删除。见 [OpenClaw 集成说明第 8 节](openclaw-integration.md)。

## 11. 升级失败或数据恢复

先阅读完整步骤。恢复到备份时刻会失去之后的新写入，数据库、附件、Agent 绑定和 Gateway 状态必须对应。这里提供人工恢复过程，不提供默认覆盖数据的快捷 restore。

### 11.1 停止服务并核对备份

```bash
sudo systemctl stop classclaw-backup.timer    # 已启用时
sudo systemctl stop openclaw-gateway.service
sudo systemctl stop classclaw.service
sudo classclaw logs -n 100
sudo classclaw logs --app -n 100

sudo -u classclaw -H bash
cd /opt/classclaw
CC_BACKUP=/opt/classclaw/backups/classclaw-backup-实际时间戳
python3 -m json.tool "$CC_BACKUP/deployment.json"
python3 -m json.tool "$CC_BACKUP/manifest.json"
```

若新代码的脚本不能运行，systemctl 仍可独立停服。不要仅凭“最新目录”认定备份完整。如果只是 pip 网络故障，可以保持停服修复依赖/配置/插件并完成迁移；不能仅删除失败标记就启动。以下是回到旧代码和旧数据的完整路线。

### 11.2 恢复代码、配置和依赖

继续在服务用户 shell 中运行，填写清单中的 revision，先确认没有需保留的代码修改：

```bash
git status --short
CC_OLD_REV=填写deployment.json中的revision
CC_RECOVERY=/home/classclaw/recovery-$(date +%Y%m%d-%H%M%S)
mkdir -m 700 "$CC_RECOVERY"
git switch -c "recovery-$(date +%Y%m%d-%H%M%S)" "$CC_OLD_REV"

cp -a .env "$CC_RECOVERY/current.env"
cp -a config/classclaw.toml "$CC_RECOVERY/current.toml"
cp "$CC_BACKUP/deployment.env" .env
cp "$CC_BACKUP/classclaw.toml" config/classclaw.toml
chmod 600 .env config/classclaw.toml

mv .venv "$CC_RECOVERY/venv-failed"
python3.12 -m venv .venv
.venv/bin/python -m pip install -r "$CC_BACKUP/requirements.freeze.txt"
.venv/bin/python -m pip check
```

外置配置/存储路径按清单恢复，不能直接套默认路径。Git 不可用时可在干净目录解开 `source.tar`，不能直接向混杂的新代码覆盖解压；缺少 `.git` 的目录也不能继续自动升级。

### 11.3 恢复双库、附件和 Gateway 状态

先保存故障现场，空间不足时使用独立备份盘：

```bash
cp -a data "$CC_RECOVERY/data-before-restore"
.venv/bin/python scripts/restore.py "$CC_BACKUP"

mv data/openclaw-agents "$CC_RECOVERY/agents-before-restore"
mv /home/classclaw/.openclaw "$CC_RECOVERY/state-before-restore"
cp -a "$CC_BACKUP/openclaw-agents" data/openclaw-agents
cp -a "$CC_BACKUP/openclaw-state" /home/classclaw/.openclaw
.venv/bin/python scripts/check_config.py
.venv/bin/python -m alembic upgrade head
```

上述目录复制适用于清单包含它们的备份；原目录不存在时按清单恢复为空/缺省状态，不混入失败版本的状态。恢复脚本使用 SQLite Backup API 避免残留 WAL 覆盖恢复结果，不能只替换某一个 `.db`。

按旧版本插件锁文件重新执行第 5.1 节的依赖安装和构建，确认全局 OpenClaw 仍是匹配版本。本次也升级过全局 OpenClaw 时，恢复对应版本及状态文件；上游已失效的账号凭据可能仍需重新绑定。

确认代码、依赖、配置、迁移和插件都恢复完成后，移走失败标记留档：

```bash
if [ -f backups/.upgrade-pending.json ]; then
  mv backups/.upgrade-pending.json "$CC_RECOVERY/upgrade-pending.json"
fi
exit
sudo classclaw start
sudo classclaw doctor
```

再次完成网页验收后恢复备份定时器。恢复分支不自动跟踪生产 upstream，待故障在开发环境修复后明确选择发布分支/upstream；不要强行 reset 来通过快进检查。

## 12. 常见问题

| 表现 | 先检查 |
| --- | --- |
| Nginx 502 | `classclaw status`、`logs`、`nginx -t`，后端是否监听 127.0.0.1:8000 |
| 网页回答最后一次性出现 | Nginx/CDN 是否缓冲 SSE，浏览器是否收到分段事件 |
| 上传 413 | Nginx 总请求体上限和 TOML 单文件上限，它们作用不同 |
| 普通页面能用，AI 失败 | Gateway、插件、admin RPC、Provider 凭据及模型能力；仅 health 通过不够 |
| 工具 401/403 | 两个 Token 是否配对正确；不能把管理 Token 发往浏览器 |
| 二维码消失/验证码失败 | 是否加载仓库兼容层、是否误启动另一份 Gateway、登录是否被重启打断 |
| 清理命令找不到 OpenClaw | TOML CLI 绝对路径、systemd PATH，以及是否同一用户 |
| DATABASE_BUSY | 是否误开多个后端/worker，是否有外部 SQLite 写进程或慢磁盘 |
| 升级拒绝脏工作区 | 检查 git status，将开发改动合入远程；本机配置写入忽略文件，不修改已跟踪模板 |
| 提示未完成升级 | 按第 11 节恢复或停服修复，不能仅删除标记 |
| 频繁重启/OOM | MemoryPeak、内核 OOM 日志、大文件并发、额外插件与 CLI 子进程 |
| 忘记管理员密码 | 停后端，以服务用户执行 `.venv/bin/python scripts/reset_admin_password.py`，重置为 .env 的目标密码并撤销旧会话 |

## 13. 将来迁移或分机的限制

ClassClaw 不只通过 HTTP 调 Gateway，还直接写班级/提取器工作区、读 OpenClaw 状态并执行本地 CLI。**仅修改 `gateway_url` 不构成完整分机部署。** 同机是当前完整功能最直接的方式。

迁移时停两项服务，保存双库、附件、配置、工作区与 Gateway 状态，在目标机重新创建 venv、安装 Node/OpenClaw 并构建插件。不能从 macOS 复制 `.venv` 或 `node_modules` 到 Ubuntu。数据库中的附件/工作区路径及插件链接可能是绝对路径，尽量保持相同；路径变化须逐项迁移验收，不能只改 TOML。

未来若拆分 Gateway，需要另行设计远端工作区/状态管理、双向插件回调、身份验证和清理执行方式；不能把 SQLite 放网络文件系统来代替这些工作。

## 14. 当前验证范围

回归覆盖真实临时 Git 仓库的更新预检、拒绝脏/分叉分支、停服备份先于更新、依赖/迁移/健康检查失败后停服、失败标记阻止重复升级、保留原停止状态、运维锁、部署快照不输出密钥及小服务器 TOML 加载；原有双库备份/恢复和配置回归同时执行。

本地环境为 macOS。systemd、Nginx、Ubuntu 安装、TLS 签发和 2 核 4 GB 实际负载须在目标服务器按本指南验收；尚未登录目标服务器部署。

## 可选教室媒体服务

教室实时音视频另按 [媒体部署](classroom-media-deployment.md) 安装同机 MediaMTX 与 FFmpeg，并使用配套可选 systemd drop-in。媒体 unit 随 ClassClaw 停止/重启，遵守升级失败标记；不开公网信令/控制 API。Windows 程序按 [交接提示词](windows-client-agent-prompt.md) 实现。
