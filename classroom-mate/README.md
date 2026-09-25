# Classroom Mate

ClassClaw 的 Windows 教室托盘终端。目录名为 **classroom-mate**。普通用户运行，支持登录自启、网页配对、点名句子显示/中文播报、指定音箱音量、设备清单和按需音视频发布。没有 Agent 或本地大模型，不需要教室电脑开放入站端口。

## 安装与使用

目标为 **Windows x64**：Windows 11，或符合 .NET 10 支持范围的 Windows 10 LTSC/受支持服务版本。没有验证 Windows 7、32 位 Windows 或 ARM64。发布包包含 .NET 自包含运行时以及媒体所需嵌入式 Python/原生编解码库，不需要安装开发工具或 Python。

1. 将 `dist/ClassroomMate-win-x64.zip` 完整解压。不要只拷贝 EXE。
2. 直接运行 `ClassroomMate.exe`，或在 PowerShell 中运行发布包根目录的 `install.ps1` 安装到当前用户的 `%LOCALAPPDATA%\Programs\ClassroomMate`，创建开始菜单快捷方式和登录启动项。脚本不需要管理员权限。
3. 网页「教室设备」生成一次性配对码，在客户端填写 HTTPS 服务器根地址及配对码。正式环境验证证书；开发模式只允许本机 HTTP。
4. 网页选择客户端上报的教室显示屏、音箱、摄像头和麦克风。未选定输出设备时明确报错，不随意把广播送到教师副屏或默认音箱。
5. 关闭设置窗口继续驻留托盘；托盘菜单提供暂停/恢复及正常退出。锁屏、用户会话断开或休眠会暂停，解锁后从托盘主动恢复。
6. 在网页预览并发送广播，查看显示和语音分别返回的执行状态。广播中的“下课后”是句子内容，不是本机定时任务。

首次成功配对默认打开登录自启，可在设置取消。系统必须已有可用中文 TTS 语音；缺少时显示可以工作，语音会明确报不可用。摄像头和麦克风需在 Windows 隐私设置中允许桌面应用访问。其他程序独占摄像头时需要人工处理，本程序不会终止掌上看家等应用。

发布包目前未进行代码签名。Windows 上的真实 UI、TTS、Core Audio、DirectShow 设备以及低配机器资源占用须按下述清单现场验收；跨平台编译成功不代表完成 Windows 硬件实测。

## 功能和行为

- WinForms 原生托盘、单实例、普通用户应用；没有隐藏进程、防卸载、防任务管理器或守护重启。
- 凭据只通过配对返回一次，DPAPI `CurrentUser` 加密后保存在 `%LOCALAPPDATA%\ClassClaw\ClassroomMate\device.dpapi`。广播原文和学生名单不进入本地账本。
- WebSocket 单接收循环/串行发送、分片和大小限制、退避重连；4401/撤销/协议不匹配停止自动重连。
- 命令 ID 在副作用前原子落盘；重复只回报旧结果，崩溃中的任务恢复为 unknown；断线、暂停、到期或退出取消当前任务，不补播，不做离线队列。
- 同时一个广播任务，忙碌返回 BUSY。按服务端冻结段落顺序执行；每段显示至少指定时间，并等待该段所有语音完成。有效期预检计入显示时间和重复播报固定间隔。显示与语音分别报告，多段中的失败不会被后续成功覆盖。
- stop 只取消指定广播的声音，clear 只清除指定广播的显示；历史目标不影响当前广播。可关闭显示窗口，非强制置顶，不能跨锁屏显示。
- 广播 `volume` 为临时音量，结束条件恢复；教师中途手动修改时不覆盖。`volume.set.restore_after_broadcast=true` 设置一次性临时音量，在下一次广播完成后恢复；已有广播运行时拒绝这类临时设置；暂停/退出也恢复。普通音量命令持续生效。没有明确静音指令时不自动取消静音。
- 硬件清单随心跳重新检测；屏幕名称为 Windows 显示设备名，输入/输出设备使用原生稳定标识的 SHA-256，重名设备加 ID 短后缀。

## 媒体实现

C# 托盘负责授权和进程生命周期，独立媒体子进程采用 **aiortc + PyAV**，底层使用原生 FFmpeg 库。没有 Electron/WebView 或外部 ffmpeg 命令行。媒体依赖使发布包较大，但无人观看时不启动媒体进程。

- 每条获授权轨道一个 sendonly PeerConnection：H.264 视频与 Opus 音频分别发布。视频观看不会顺带打开麦克风。
- 本机摄像头/麦克风通过 DirectShow 设备枚举、PyAV dshow 读取；默认 640×480、10 fps，编码前限制尺寸。不支持该模式时如实返回采集失败，需现场验证设备兼容性。
- 网络来源支持 RTSP、RTMP(S)、HTTP(S) 媒体输入；WHIP 是发布协议，不能作为本版本网络摄像头读取来源。服务器直读由服务端完成，本机不重复采集。
- 摄像头 ID/版本变化先停旧再启新；`media_state` 决定是否采集和有效期。音频权限撤销时关闭音频子进程并保留有效的视频进程。
- 完整 ICE SDP 经设备专属 JSON 接口提交。子进程只接受固定消息；凭据/地址经继承的 stdin 传入，不放在命令行、不记录原生错误原文。
- 使用 Windows Job Object 的 kill-on-close；托盘退出/崩溃不会留下孤立采集进程。正常停止先通知并最多等待 2 秒，然后结束子进程；本地停止不等待远端清理成功。
- 失败不会无限快速重开摄像头。观看者停止后重新观看，或重新连接摄像头后再尝试；服务端/设备问题不伪造 connected。

服务器需先按 [媒体部署](../docs/classroom-media-deployment.md) 启用 MediaMTX，配置 HTTPS/WSS 和 ICE 可达性。仅看到托盘在线不表示媒体服务已启用。

## 构建

需要 .NET 10 SDK 和用于打包的 Python 3/pip。可在 Windows、macOS 或 Linux 交叉编译 Windows x64，最终 Windows API 验收必须在 Windows 上执行。

```bash
dotnet run --project tests/ClassroomMate.Tests
dotnet build src/ClassroomMate.Windows -c Release
python scripts/package.py
```

`package.py` 下载官方 Python 嵌入式运行时及哈希锁定的 Windows wheels，再执行 `dotnet publish --self-contained true -r win-x64`。支持 `--dotnet <路径>`、`--offline`（已缓存媒体依赖）、`--nuget-source <源地址或本地包目录>`。NuGet 首次还原需要网络；使用本地源时跳过在线漏洞审计，不代表已经完成依赖安全审计。输出目录与 ZIP 均位于 `dist/`，含逐文件 `SHA256SUMS.json`、依赖许可和安装脚本。缓存和发布二进制不提交 Git。

媒体逻辑测试与可选真实联调：

```bash
python -m pip install -r media/requirements.txt
python media/test_publisher.py
# 另需仓库服务端依赖、httpx 与同机 MediaMTX；只用临时端口和合成音视频
python media/test_live.py --mediamtx /path/to/mediamtx
```

协议/状态机测试使用可替换硬件接口，另有真实 Kestrel/WebSocket 集成测试。媒体联调只替换采集来源为合成画面和静音，真实执行 JSON SDP、MediaMTX 鉴权、音视频接收解码和关闭。

## 升级、卸载与验收

先从托盘正常退出，再运行新版 `install.ps1`。升级保留加密凭据、输出配置和去重账本。运行 `uninstall.ps1` 删除登录启动项、快捷方式和安装目录；默认保留设备数据，显式 `-RemoveDeviceData` 才删除。删掉数据或凭据无法解密时，先在网页撤销旧设备再重新配对。不要在运行中手动清除账本。

现场验收至少包含：中文 TTS、长句与多段、屏幕拔出、音箱热插拔、静音/音量恢复、正常退出与任务管理器结束后无残留采集、相机占用/拔出、仅视频/带声音、多观看者、租约到期、服务器撤销、断线不补播。记录低配 Windows 空闲/播报/采集的 CPU、内存、带宽，不以开发机器的合成测试代替。

协议依据：[终端设计与接口](../docs/windows-client-agent-prompt.md)、[服务端实现](../docs/classroom-monitoring.md)。依赖资料：[.NET 10 支持范围](https://github.com/dotnet/core/blob/main/release-notes/10.0/supported-os.md)、[Python 嵌入式分发](https://docs.python.org/3/using/windows.html#the-embeddable-package)、[aiortc](https://aiortc.readthedocs.io/en/latest/)、[NAudio](https://github.com/naudio/NAudio)。
