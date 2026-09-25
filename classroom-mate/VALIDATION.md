# Classroom Mate 0.1.0 验证记录

首次验证：2026-09-24；代码复查与回归：2026-09-25。构建主机为 macOS ARM64；目标为 Windows x64。没有使用真实教室设备或业务数据库进行测试。

| 检查 | 结果 |
| --- | --- |
| .NET SDK 10.0.401，Windows 自包含运行时 10.0.12 | Windows Release 编译、发布成功 |
| Core 回归 | 15 项通过，包含真实本机 Kestrel/WebSocket 的鉴权、重复投递、回执与撤销 |
| 服务端终端相关回归 | `test_classroom_channel.py`、`test_classroom_devices.py`、`test_classroom_regressions.py` 共 35 项通过 |
| 媒体逻辑回归 | 5 项通过：接口范围、分轨设备选择、RTSP 音轨过滤、地址校验、凭据编码 |
| MediaMTX 1.17.0 实际媒体联调（2026-09-24） | 视频和音频均通过 JSON SDP、设备鉴权、接收解码与 EOF 关闭；仅输入来源替换为合成媒体 |
| Python 静态检查 | `ruff check classroom-mate --exclude dist --exclude .dist-cache` 通过 |
| 发布依赖 | Python 3.13.15 嵌入式运行时；13 个按 SHA-256 锁定的 Windows wheels，含 aiortc 1.15.0、PyAV 17.1.0 |

Core 回归包含过期拒绝、去重账本不留广播原文、崩溃不补播、忙碌拒绝、目标化停止、断线取消、服务器时间偏移、TTS 不可用时仍显示，以及音量恢复异常后释放任务且只发送一次终结回执。复查新增：多段中的显示或播报失败不能被后续成功覆盖；重复播报的固定间隔超过有效期时必须在执行前拒绝。

复查还修复了媒体进程关闭异常时的清理、失败进程回收和停止状态回报，以及音量设备异常时的资源释放；打包脚本显式使用 UTF-8 读取元数据，并移除了导致干净缓存下缺少 Windows 框架运行时的还原限制。Windows 原生路径已重新编译，但这些硬件行为仍须在 Windows 上实测。

发布目录内的 `SHA256SUMS.json` 覆盖 EXE、全部运行时、媒体组件、许可和安装脚本。ZIP 校验值另存 `dist/ClassroomMate-win-x64.zip.sha256`，不写进 ZIP 自身。二进制和下载缓存不提交 Git。

尚未验证：真实 Windows 启动/自启、DPAPI、安装与卸载脚本执行、WinForms 显示与 DPI、多屏拔插、中文 TTS 发音、Core Audio 音量恢复、DirectShow 摄像头/麦克风兼容性、任务管理器终止后的硬件释放，以及低配电脑 CPU/内存/带宽。Windows 可执行文件已交叉编译，未在本开发机运行；不能将上述协议和合成媒体结果当成硬件验收结果。具体现场清单见 [README](README.md)。
