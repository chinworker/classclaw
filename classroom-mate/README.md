# Classroom Mate

ClassClaw 的 Windows 教室托盘终端。目录名为 **classroom-mate**。普通用户运行，支持登录自启、网页配对、点名句子显示/中文播报、指定音箱音量、设备清单和按需音视频发布。没有 Agent 或本地大模型，不需要教室电脑开放入站端口。

## 安装与使用

目标为 **Windows x64**，支持范围为 Windows 11 及符合 .NET 10 支持范围的 Windows 10 服务版本。安装包含 .NET 自包含运行时、嵌入式 Python 和媒体编解码库，教室电脑可离线安装。

1. 打开网页「监控与播报 → 终端与监控设置」，下载最新版 Windows x64 安装包。 [从 ClassClaw 服务器下载](https://claw.dickhead.top/downloads/classroom-mate/latest/ClassroomMate-Setup-win-x64.exe)
2. 双击 `ClassroomMate-Setup-win-x64.exe`。安装向导请求管理员权限，程序安装到 `%ProgramFiles%\ClassroomMate`。日常运行使用普通权限。可在安装完成时启动，也可从开始菜单打开 **Classroom Mate**。
3. 在网页「监控与播报 → 终端与监控设置」生成 4 位一次性数字码，在客户端「设置 → 服务器与配对」输入。成功后网页自动显示已配对。默认地址为 `https://claw.dickhead.top`，可改为自己的服务器。正式环境验证证书，开发模式允许本机 HTTP。
4. 在客户端「设置 → 音视频与播放设备」选择摄像头、视频清晰度、麦克风、播报扬声器和显示屏。默认 720p，可选 1080p、480p 或 360p。保存后主界面直接预览本地画面。打开网页「监控与播报 → 实时监控」会自动跟随当前 Windows 来源。
5. 主界面可查看连接和采集状态、打开网页、暂停或恢复连接，以及测试显示和播报。主界面可独立预览所选摄像头；网页观看时共用同一路摄像头。关闭主界面后继续驻留托盘，双击托盘或开始菜单重新打开。锁屏、用户会话断开或休眠会暂停，解锁后可主动恢复。
6. 在网页预览并发送广播，查看显示和语音分别返回的执行状态。广播中的“下课后”是句子内容，不是本机定时任务。

首次成功配对默认打开登录自启，可在设置取消。系统必须已有可用中文 TTS 语音；缺少时显示可以工作，语音会明确报不可用。摄像头和麦克风需在 Windows 隐私设置中允许桌面应用访问。其他程序独占摄像头时需要人工处理，本程序不会终止掌上看家等应用。

安装程序目前未进行代码签名，Windows 可能提示未知发布者。Windows 上的真实 UI、TTS、Core Audio、DirectShow 设备以及低配机器资源占用须按下述清单现场验收。

## 功能和行为

- WinForms 原生托盘、单实例、普通用户应用；没有隐藏进程、防卸载、防任务管理器或守护重启。
- 凭据只通过配对返回一次，DPAPI `CurrentUser` 加密后保存在 `%LOCALAPPDATA%\ClassClaw\ClassroomMate\device.dpapi`。广播原文和学生名单不进入本地账本。
- WebSocket 单接收循环/串行发送、分片和大小限制、退避重连；4401/撤销/协议不匹配停止自动重连。
- 命令 ID 在副作用前原子落盘；重复只回报旧结果，崩溃中的任务恢复为 unknown；断线、暂停、到期或退出取消当前任务，不补播，不做离线队列。
- 同时一个广播任务，忙碌返回 BUSY。按服务端冻结段落顺序执行，末段正常播报完成 3 秒后关闭。前面的段落按设置的停留时间逐段显示并等待语音完成。显示与语音分别报告，多段中的失败不会被后续成功覆盖。
- 广播期间在所选屏幕置顶显示，字体按屏幕和文本自动放大。可按 Esc 或点击按钮关闭。stop 只取消指定广播的声音，clear 只清除指定广播的显示。锁屏、断线或退出会中止执行并清除窗口。
- 广播 `volume` 为临时音量，结束条件恢复；教师中途手动修改时不覆盖。`volume.set.restore_after_broadcast=true` 设置一次性临时音量，在下一次广播完成后恢复；已有广播运行时拒绝这类临时设置；暂停/退出也恢复。普通音量命令持续生效。没有明确静音指令时不自动取消静音。
- 硬件清单随心跳重新检测；屏幕名称为 Windows 显示设备名，输入/输出设备使用原生稳定标识的 SHA-256，重名设备加 ID 短后缀。

## 媒体实现

C# 托盘负责授权和进程生命周期，独立媒体子进程采用 **aiortc + PyAV**，底层使用原生 FFmpeg 库。没有 Electron/WebView 或外部 ffmpeg 命令行。媒体依赖使发布包较大，但无人观看时不启动媒体进程。

- 每条获授权轨道一个 sendonly PeerConnection。Windows 以低延迟 H.264 编码视频，以 Opus 编码音频，MediaMTX 直接转发。网页默认静音，等到真实解码画面后显示分辨率。只有视频授权的会话仍只使用视频轨。
- 主界面直接预览选定的本机摄像头，刷新上限为每秒 12 帧。网页开始或停止观看时只切换发布连接，复用已有的本地采集。预览帧只在内存中传递；隐藏主界面、锁屏、暂停或退出会停止本地预览。
- 本机摄像头和麦克风通过 DirectShow 读取。视频优先打开所选模式，不兼容时使用设备原生模式。分辨率上限由 1080p、720p、480p、360p 档位决定，按比例缩小，最高 30 fps，不放大低分辨率来源。采集优先提供最新帧，跳过积压画面。
- 网络来源支持 RTSP、RTMP(S)、HTTP(S) 媒体输入；WHIP 是发布协议，不能作为本版本网络摄像头读取来源。服务器直读由服务端完成，本机不重复采集。
- 摄像头 ID/版本变化先停旧再启新；`media_state` 决定是否采集和有效期。音频权限撤销时关闭音频子进程并保留有效的视频进程。
- 完整 ICE SDP 经设备专属 JSON 接口提交。子进程只接受固定消息；凭据/地址经继承的 stdin 传入，不放在命令行、不记录原生错误原文。
- 使用 Windows Job Object 的 kill-on-close；托盘退出/崩溃不会留下孤立采集进程。正常停止先通知并最多等待 2 秒，然后结束子进程；本地停止不等待远端清理成功。
- 摄像头打开失败后限速重试；网络重连保留可用的本机预览。网页在同一授权内等待上行，停帧时重建观看连接。

服务器需先按 [媒体部署](../docs/classroom-media-deployment.md) 启用 MediaMTX，配置 HTTPS/WSS 和 ICE 可达性。仅看到托盘在线不表示媒体服务已启用。

## 构建

需要 .NET 10 SDK 和用于打包的 Python 3/pip。可在 Windows、macOS 或 Linux 交叉编译 Windows x64，最终 Windows API 验收必须在 Windows 上执行。

```bash
dotnet run --project tests/ClassroomMate.Tests
dotnet build src/ClassroomMate.Windows -c Release
python scripts/package.py
```

`package.py` 下载官方 Python 嵌入式运行时及哈希锁定的 Windows wheels，再执行 `dotnet publish --self-contained true -r win-x64`。Python 运行时整包下载，校验 SHA-256 后才写入缓存；校验通过的缓存会直接复用。支持 `--dotnet <路径>`、`--offline`（已缓存媒体依赖）、`--nuget-source <源地址或本地包目录>`。NuGet 首次还原需要网络；使用本地源时跳过在线漏洞审计，不代表已经完成依赖安全审计。`dist/` 中的便携 ZIP 含逐文件 `SHA256SUMS.json`、依赖许可和原有安装脚本。缓存和发布二进制不提交 Git。

在 Windows 上安装 Inno Setup 6.3 或更新版本后，可同时生成 `dist/ClassroomMate-Setup-win-x64.exe` 和 `.exe.sha256`：

```powershell
python scripts/package.py --iscc "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe"
```

推送包含 `classroom-mate/` 变更时，GitHub Actions 会在 `windows-2025` 上运行协议测试、打包并验证全新安装、从已发布旧版升级、脚本版迁移和卸载。推送到 `main` 时，测试通过后自动发布一次 GitHub Release，附上安装程序和 SHA-256 文件。一次推送包含多个提交时生成一个 Release，标签为 `classroom-mate-build-<工作流运行编号>`，重跑沿用该标签。其他分支或从 Actions 手动运行可下载构建产物。 [构建记录](https://github.com/chinworker/classclaw/actions/workflows/classroom-mate-windows.yml)

媒体逻辑测试与可选真实联调：

```bash
python -m pip install -r media/requirements.txt
python media/test_publisher.py
# 另需仓库服务端依赖、httpx 与同机 MediaMTX；只用临时端口和合成音视频
python media/test_live.py --mediamtx /path/to/mediamtx
```

协议/状态机测试使用可替换硬件接口，另有真实 Kestrel/WebSocket 集成测试。媒体联调只替换采集来源为合成画面和静音，真实执行 JSON SDP、MediaMTX 鉴权、音视频接收解码和关闭。

## 升级、卸载与验收

各平台的历史验证记录和待办见 [VALIDATION.md](VALIDATION.md)。

从 0.3.1 开始，在「设置 → 版本更新」检查并从 ClassClaw 官方服务器下载最新版。安装包校验通过后，客户端退出并打开安装向导。GitHub Actions 将构建号写入四段 Windows 版本，验证服务器安装包的 SHA-256 后发布独立的 GitHub Release。 [发布记录](https://github.com/chinworker/classclaw/releases)

从 0.3.0 开始，安装使用管理员权限和全机共用的程序目录、固定 AppId 与 Windows 卸载登记。用日常运行终端的 Windows 账号打开新版安装程序并完成 UAC，旧的每用户安装在发起安装的用户上下文中迁移。旧版在运行时，向导会提示从托盘正常退出。确认页会说明升级步骤，点击 Install 后先移除旧版程序，再安装新版。加密配对凭据、设备配置、去重账本和原用户的登录自启设置随升级保留。以后版本沿用同一程序目录及卸载登记，升级先移除旧程序；卸载入口异常或旧文件仍被占用时停止升级并说明原因。

卸载时先从托盘退出，再在 Windows「设置 → 应用 → 已安装的应用」（Windows 10 为「应用和功能」）找到 **Classroom Mate**，选择「卸载」。卸载会移除程序、快捷方式和登录启动项，设备数据保留在 `%LOCALAPPDATA%\ClassClaw\ClassroomMate`，供同一 Windows 用户重装使用。

四位配对码和网页确认本机来源需要同步更新 ClassClaw 服务端。升级后的客户端可沿用已有设备配对，未在本机改选的采集项可沿用旧登记。

通过 `install.ps1` 装在默认位置的旧版也可由新版安装程序移除。需要彻底移除设备时，先在网页撤销设备，再在退出程序后删除上述设备数据目录。

现场验收至少包含：中文 TTS、长句与多段、屏幕拔出、音箱热插拔、静音/音量恢复、正常退出与任务管理器结束后无残留采集、相机占用/拔出、仅视频/带声音、多观看者、租约到期、服务器撤销、断线不补播。记录低配 Windows 空闲/播报/采集的 CPU、内存、带宽，不以开发机器的合成测试代替。

协议依据：[终端设计与接口](../docs/windows-client-agent-prompt.md)、[服务端实现](../docs/classroom-monitoring.md)。依赖资料：[.NET 10 支持范围](https://github.com/dotnet/core/blob/main/release-notes/10.0/supported-os.md)、[Python 嵌入式分发](https://docs.python.org/3/using/windows.html#the-embeddable-package)、[aiortc](https://aiortc.readthedocs.io/en/latest/)、[NAudio](https://github.com/naudio/NAudio)。
