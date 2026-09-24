# 教室音视频服务部署与联调

服务端、网页播放器和 ClassClaw Channels 的按需现场概况已经实现。Windows 程序由下一位智能体按 [Windows 交接提示词](windows-client-agent-prompt.md) 编写。部署时另装同机 MediaMTX 二进制和 FFmpeg；不引入 Docker、Redis 或任务队列。默认 `media_provider="none"` 保持只显示设备状态，只有完成下述配置后才启用播放。

## 1. 组件和边界

- FastAPI 负责本班用户/设备鉴权、短期观看租约、JSON SDP 信令和连接回收。只有单个 Uvicorn worker。
- MediaMTX **v1.17.0** 负责媒体包转发，信令、控制 API、RTSP 只监听服务器环回地址。专供 ClassClaw 使用；后端恢复时会清除 `classclaw/` 路径的遗留 WebRTC/RTSP 会话。
- 每班一路摄像头，独立的 video、audio 路径和 WebRTC 连接。视频观看权限不能用于协商声音。多个观看者共用采集上行。
- Windows 默认按 `media_state` 的有效期与音视频开关采集；无人观看不采声音，最后一名视频观看者离开后最多保留配置的宽限期。撤权与设备断连由后台循环回收，正常情况下约 2 秒开始处理；上游故障会重试，不能将逻辑撤销当成已完成物理断流。
- 服务器直读仅支持可达的 RTSP 摄像头，使用固定 FFmpeg 参数输出 H.264 视频和可选 Opus 音频。域名在连接前解析、校验并固定 IP。教室局域网地址从云服务器不可达时应选择 Windows 中转。
- 目前无录像回放；现场概况只取一张图片，明确不使用声音、不做身份识别或考勤。图片不写业务库或附件目录，通过 OpenClaw 的临时分析会话发送给模型；Gateway/模型服务的会话保留仍遵循其部署策略，沿用既有临时会话清理。

## 2. 安装

先完成 [Ubuntu 部署](deployment.md)，服务器应已有 `/opt/classclaw`、`classclaw` 服务用户和 HTTPS 域名。

1. 从 [MediaMTX 官方 v1.17.0 release](https://github.com/bluenviron/mediamtx/releases/tag/v1.17.0) 下载匹配服务器架构的 Linux 二进制并核对 release 校验信息，安装为 `/usr/local/bin/mediamtx`。不要把 macOS 测试二进制部署到 Linux。
2. 安装 FFmpeg：`sudo apt-get install ffmpeg`。确认 `ffmpeg -encoders` 包含 `libx264`、`libopus`、`mjpeg`。
3. 安装配置和 systemd 文件：

```bash
sudo install -d -m 0750 -o root -g classclaw /etc/classclaw
sudo install -m 0640 -o root -g classclaw deploy/mediamtx.yml /etc/classclaw/mediamtx.yml
sudo install -m 0644 deploy/systemd/classclaw-media.service /etc/systemd/system/classclaw-media.service
sudo install -d /etc/systemd/system/classclaw.service.d
sudo install -m 0644 deploy/systemd/classclaw-media.conf /etc/systemd/system/classclaw.service.d/media.conf
```

4. 编辑 `/etc/classclaw/mediamtx.yml` 的 `webrtcAdditionalHosts`，填服务器实际公网 IP 或 DNS；如 FastAPI 不是 8000 端口，同步修改 `authHTTPAddress`。保留回环监听和 HTTP 鉴权，不要改成匿名读取。
5. 在现有 `config/classclaw.toml` 的 `[classroom]` 中设置（不要重复创建同名表）：

```toml
media_provider = "mediamtx"
media_base_url = "http://127.0.0.1:8889"
media_api_url = "http://127.0.0.1:9997"
media_rtsp_url = "rtsp://127.0.0.1:8554"
media_ffmpeg_path = "ffmpeg"
media_ice_servers = []
```

这些字段也支持 `.env` 的 `CLASSCLAW_CLASSROOM_MEDIA_*` 覆盖，ICE 数组用 JSON。摄像头账号只配置在 `CLASSCLAW_CLASSROOM_CAMERA_<引用名>`，值为 `{"username":"…","password":"…"}`；网页流地址不得嵌入账号密码。

6. 应用迁移 `alembic upgrade head`（包括 `0017` 与 `0018` 麦克风字段），同步 Nginx 配置中的终端 WebSocket 与媒体鉴权回调阻断规则；先 `nginx -t` 再 reload。使用正常运维停服流程备份、迁移与启动。
7. `sudo systemctl daemon-reload` 后 `sudo classclaw restart`。不必独立 enable 媒体 unit：可选 drop-in 随应用拉起它。`PartOf` / `BindsTo` 使应用停止、重启或退出时媒体服务同步停止；升级失败标记也会阻止媒体启动。原有备份/升级命令仍维护应用与 Gateway，媒体作为应用依赖参与生命周期。

检查 `systemctl status classclaw-media` 与 `journalctl -u classclaw-media`。不要将摄像头凭据、SDP 或设备凭据复制进工单日志。

## 3. 网络与 TURN

- 公网：HTTPS 443；媒体 UDP **8189** 和 TCP **8189**。同时检查云安全组、服务器防火墙和 NAT 映射。
- 仅环回：FastAPI 8000、MediaMTX 8889（WHIP/WHEP）、9997（控制）、8554（RTSP）。**不要向公网反代 MediaMTX 信令或控制 API**，也不要开放它们的监听端口。
- 仓库 Nginx 模板通过 `/api/v1/classroom/device-channel` 转发 WSS；`/api/v1/classroom/media/auth` 对公网返回 404。MediaMTX 从环回直接回调 FastAPI。
- 网络限制导致 WebRTC 无法连通时，可配置已有 TURN 服务的 RTCIceServer 条目，例如 `media_ice_servers = [{ urls = ["turn:turn.example.com:3478"], username = "classroom", credential = "受限的TURN客户端凭据" }]`。这些客户端凭据会交给授权观看者和终端，不得填 TURN 管理密钥。若 MediaMTX 所在网络也需要中继，在其 `webrtcICEServers2` 中按官方配置另设。此仓库不部署 TURN 服务。
- Windows 发完整 ICE SDP，服务端不提供 trickle PATCH。编解码统一优先 H.264 无 B 帧和 Opus，默认低分辨率/低帧率以适配教室电脑。

配置依据：[官方配置参考](https://mediamtx.org/docs/references/configuration-file)、[HTTP 鉴权](https://mediamtx.org/docs/features/authentication)、[WebRTC 读取与网络](https://mediamtx.org/docs/read/webrtc)。

## 4. 不依赖 Windows 的真实媒体自测

自动测试默认不需要 MediaMTX、FFmpeg 或摄像头。可选真实链路脚本只使用临时目录、随机环回端口、合成视频和静音，不打开业务数据库：

```bash
python3 -m venv /tmp/classclaw-media-check
/tmp/classclaw-media-check/bin/pip install -r requirements.txt aiortc==1.15.0
/tmp/classclaw-media-check/bin/python scripts/test_classroom_media.py --mediamtx /usr/local/bin/mediamtx
```

脚本验证真实 WHIP 发布 → HTTP 签名鉴权 → 分轨 WHEP → 解码、远端 DELETE、RTSP 单帧 JPEG、服务器直读 RTSP 经 FFmpeg 再发布以及轨道权限隔离。FFmpeg 的 RTSP 用户信息长度有限，内部签名凭据保持短格式，不能改回编码整段 JSON 的长密码。

本次已在 macOS 本机通过上述合成媒体链路；这不替代 Linux 部署验收、真实摄像头型号兼容性或教室出网测试。

## 5. 现场验收

Windows 完成后依次验证：网页配对 → 显示屏/音箱/摄像头/麦克风清单 → 选择一路摄像头 → 只看视频 → 授权声音 → 第二观看者 → 释放/登出/换摄像头 → 网络中断和重连。确认无离线补播、音量控制正确、屏幕显示与播报回执分别记录。

从网页或 ClassClaw Channel 明确请求一次现场概况；需为 OpenClaw 分析配置可读取图片的模型。模型不可用或图片不支持时应返回错误，不应产生虚构概况。常规状态轮询不会采样或调用模型。
