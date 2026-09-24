# Windows 教室终端开发交接 Prompt

以下正文可以完整交给负责 Windows 客户端的智能体。当前任务只编写了这份交接要求，没有实现 Windows 程序。

---

请为本仓库 ClassClaw 实现 Windows 教室终端 **ClassClaw Classroom**。本次只负责 Windows 客户端，服务器媒体、网页播放和现场概况接口已经提供，不需要你再实现服务端。先核对实际代码与协议，给出简短实施顺序，再分阶段实现、测试和交付。保留仓库现有修改，不重做班级管理系统或网页 Chat。

## 1. 场景与产品要求

ClassClaw 部署在服务器上；每个班级有一台配置较低的 Windows 教室电脑，平时学生不使用它。每班绑定一台终端、最多一路摄像头。

- 程序在 **Windows 用户登录后自动启动，默认不显示主窗口，驻留系统托盘**。首次运行未配对时提供配对入口。开机但未登录不保证可显示或发声，不偷偷设置自动登录。
- 用户已经明确不需要权限分离或强制防关闭。使用普通用户应用，提供托盘状态、设置、设备检测、暂停和正常退出。关闭设置窗口返回托盘；不要拦截任务管理器、互相拉起守护进程、隐藏进程、安装内核驱动或强制置顶覆盖所有操作。
- 网页发起配对、选择输出屏幕/扬声器、连接/更换/断开摄像头；终端检测硬件、上报清单、执行固定命令。不要在托盘另造一套能绕过网页权威配置的摄像头绑定流程。
- “点名广播”是叫学生办事，不是考勤。例如“请张三同学现在去扫地。”“请张三、李四同学下课后来老师办公室。”也可以是自定义完整句子。服务器已经组合并冻结内容，Windows 原样显示、原样播报，绝不加“请”“同学”、换人、重排或让模型润色。
- “下课后”是播报内容；收到指令就立即播报。首期没有定时广播，不解析自然语言时间，不创建离线待播任务。
- 终端不运行 OpenClaw、Agent 或本地大模型；ClassClaw Channels 通过已有服务器接口控制它，不是另建一个微信账号。

## 2. 开始前必须阅读并核对

以当前工作区为准，特别注意本次 review 修正过的停止目标与摄像头版本字段：

1. 根目录 `AGENTS.md`。
2. `docs/classroom-monitoring.md`、`docs/classroom-monitoring-plan.md`。
3. `app/schemas/classroom.py`：全部请求与固定命令模型。
4. `app/api/v1/classroom_terminal.py`、`app/api/v1/classroom.py`。
5. `app/services/classroom_devices.py`、`classroom_channel.py`、`classroom_broadcast.py`、`classroom_camera.py`、`classroom_media.py`。
6. `tests/test_classroom_channel.py`、`test_classroom_devices.py`、`test_classroom_regressions.py` 及 `tests/helpers.py`。
7. `web/js/pages/broadcast.js`、`web/js/pages/classroom.js`，理解用户可选择的参数。

目前已有：配对、专属凭据、WebSocket、心跳、命令账本、点名广播、音量控制请求、设备清单、摄像头登记、观看租约、Agent 提案、MediaMTX WHIP/WHEP 协商、分轨鉴权、网页 WebRTC 播放器、按需启停与单帧 AI 现场概况。`media_provider=none` 是明确关闭媒体功能；启用 `mediamtx` 并按 `docs/classroom-media-deployment.md` 部署后可接入。还需阅读 `app/services/classroom_streaming.py`、`app/api/v1/classroom_streaming.py`、`web/js/classroomPlayer.js` 和 `scripts/test_classroom_media.py`。尚未完成的是 Windows 程序及真实教室硬件验收。

## 3. 技术方向与模块

优先选择 C# + WinForms，使用普通用户托盘程序。根据实际目标 Windows 版本核实并选用仍受支持的 .NET LTS、语音和采集依赖，记录支持范围；不要在不知道教室版本时宣称兼容 Windows 7 或任意旧系统。不要引入 Electron、WebView 全套前端、服务器本地模型、Docker 或消息队列。

建议目录 `clients/windows/ClassClaw.Classroom/`，沿用仓库既有约定时可调整。将 UI 与可测试逻辑分离：

- 托盘生命周期、设置、登录启动与单实例。
- 配对客户端、设备凭据存储、WebSocket 连接管理。
- 明确类型的协议 DTO、命令校验、去重账本与回执发送。
- 广播显示窗口、TTS 播报器、音量控制器。
- 显示器/音频/摄像头清单检测、设备热插拔处理。
- 摄像头采集适配器、媒体发布适配器（失败或部署未启用时显式不可用）。

避免堆在一个 Form 类里。Windows API 调用通过接口隔离，协议和状态机测试不依赖真实硬件。

## 4. 当前可使用的服务器协议

### 配对与凭据

- 用户填写服务器基础地址和网页生成的一次性配对码。
- `POST /api/v1/classroom/device/pair`，不携带教师会话或共享 Token。
- 请求字段：`pairing_code`、`protocol_version:1`、`app_version`、`os_version`、`device_name`、`capabilities`、`inventory`。
- HTTP 使用统一 `{success,data,message,request_id}` 包装；失败读取 `error.code/message`。成功的 `data` 含 `device_id`、`class_id`、`credential`、`protocol_version`、`server_time`、`config_revision`、`command_ttl_seconds`、`heartbeat_interval_seconds`。
- 明文凭据只返回一次，使用当前 Windows 用户范围的系统加密能力保护后写入用户应用数据目录。禁止写注册表明文、日志、命令行、截图或源码。配对失败/落盘失败应明确显示恢复办法，不能假装已绑定。
- 正式环境使用 HTTPS/WSS，验证证书；不得通过“忽略全部证书错误”解决部署问题。仅显式开发模式允许本机测试地址。

### WebSocket

主动连接 `wss://<服务器>/api/v1/classroom/device-channel`，不要求教室电脑开放入站端口。连接后 20 秒内发送首条 `hello`：

```json
{
  "type": "hello",
  "device_id": "配对结果中的设备ID",
  "credential": "加密存储后解密得到的本设备凭据",
  "protocol_version": 1,
  "app_version": "实现的版本",
  "os_version": "实际系统版本",
  "capabilities": {
    "display": true,
    "speak": true,
    "volume_control": true,
    "chinese_tts": true,
    "capture": true,
    "displays": ["教室显示屏"]
  },
  "inventory": {
    "cameras": [{"identifier": "稳定设备标识", "name": "USB 摄像头"}],
    "microphones": [{"identifier": "稳定麦克风标识", "name": "教室麦克风"}],
    "speakers": [{"identifier": "稳定音频输出标识", "name": "教室音箱"}]
  },
  "display_name": "教室显示屏",
  "audio_output_name": "教室音箱",
  "volume_level": 30,
  "muted": false
}
```

示例能力只是格式说明，必须按实际检测结果填写；缺中文语音不能报 `chinese_tts=true`。当前 `DeviceInventory` 只有 cameras/microphones/speakers，显示屏列表在 `capabilities.displays`，不要添加服务端会拒绝的字段。设备名称须能唯一映射到本机稳定 ID，重名时提供可区分名称；摄像头 identifier 上限 200 字符，原生路径过长时使用稳定摘要与本机映射，不截断造成冲突。

收到 `welcome` 后按给出的间隔发 `heartbeat`，不要把间隔写死。心跳字段可省略未变化项：`capabilities`、`inventory`、`display_name`、`audio_output_name`、`volume_level`、`muted`、`camera_state`。服务器返回 `heartbeat_ack`。

`welcome.media`、`heartbeat_ack.media` 与主动推送的 `type:"media_state"` 使用相同的媒体期望状态格式，详见 §6；每次都必须更新本地采集授权期限。`welcome.output` 是上次回报的输出设备；`media.volume_ceiling` 是服务端音量上限。

- 普通网络错误：指数退避加抖动重连，不重新兑换配对码。
- `DEVICE_UNAUTHORIZED` / `DEVICE_REVOKED`：停止采集和执行、停止自动重连，保留诊断并引导网页复核/重新配对；4401 也可能表示连接被替换。
- 4400 协议错误、4408 心跳超时、协议版本不兼容要分别呈现；不要无上限快速重试。
- WebSocket 只有一个接收循环和串行发送通道；处理分片、关闭、取消、JSON 错误及消息大小限制。显示、TTS、采集不能阻塞心跳或停止指令。

### 命令与回执

收到的信封是 `{type:"command", command_id, kind, payload, expires_at}`。`expires_at` 含时区；用服务器时间偏移和单调计时处理本机时间差，不能简单按本地无时区时间解析。

| kind | payload 与行为 |
| --- | --- |
| `broadcast.show` | `segments:[{index,text,recipients:[{student_no,name}]}]`、`display_seconds`、`repeat_count`、`gap_seconds`、`volume?`、`target_screen?`。严格按收到的分段顺序显示和播报 |
| `broadcast.stop` | `target_command_id` 指向原 `broadcast.show`；只停止这一条的语音，不清屏、不停止其他广播 |
| `broadcast.clear` | 同上，只清除此广播的画面，不代替语音停止 |
| `volume.set` | `volume?`、`mute?`、`restore_after_broadcast` |
| `device.test_display` / `device.test_speak` | `{text}`，按服务器固定测试文本测试对应能力 |
| `device.configure` | `display_name?`、`audio_output_name?`，校验仍在本机清单中，成功后持久化并心跳上报实际选择 |
| `camera.start` / `camera.stop` | `camera_id`、`config_revision`、`identifier?`、`access_path`、`audio`；详见媒体边界 |

执行前发送 `{type:"command_result",command_id,state:"started"}`。执行终结发送 `state:"succeeded"|"failed"|"unknown"`，广播必须分别填写 `display` 和 `speak`：

- display：`pending|shown|unavailable|failed|cleared`。
- speak：`pending|spoken|unavailable|failed|skipped`。
- 可带 `error_code`（最长 100）、`error_message`（最长 400），不要包含凭据或完整采集 URL。
- 服务器回复 `{type:"command_ack",command_id,status,duplicate}`。收到命令/写入显示队列不等于实际完成；`shown` 在实际显示后，`spoken` 在 TTS 真正完成后上报。显示成功而 TTS 不可用时分别报 shown/unavailable，整体 failed。
- 原广播被停止或清除时，原命令和控制命令分别回执；目标已不在执行时只对目标做幂等 no-op，不影响当前其他内容。

终端按 `command_id` 在执行副作用前持久化去重标记。收到重复指令只回报已有结果，不能重播。进程在执行中崩溃，重启后标记结果未知，不恢复播报。确认回执丢失可以重发同一回执，不能重新执行。**当前没有设备专用 REST 命令查询端点**，不要杜撰查询接口或使用管理员 Token。

服务端新 `hello` 会终结旧连接的命令，终端同样禁止离线积压和断线补播。连接失效、程序暂停/退出、命令到期时停止相应活动并释放资源；是否已发声无法判断就报 unknown，不猜测成功。去重账本只保留必要 ID、时间、状态及结果码并限制保留量，不长期存储广播全文或学生名单。

广播采用单个活动任务；忙碌时明确返回 BUSY，不叠音、不悄悄排队。停止、清屏、心跳独立于长任务，必须及时响应。需要排队功能时另行设计，不擅自更改即时广播语义。

## 5. 显示、语音和音量

- 选定教室屏幕显示完整句子，大字、高对比、长句自动换行，不能只显示姓名而丢掉时间/事项。采用可关闭的无边框广播窗口，广播结束恢复原界面。不得默认往任意教师副屏或所有屏幕输出。
- 推荐固定语义：按 segment 顺序处理，每段连续播报 repeat_count 次，重复间隔 gap_seconds；画面至少停留 display_seconds，并等待该段语音结束再切下一段。整个任务受 expires_at 限制，配置明显无法在有效期内完成时先拒绝并说明原因，不能部分播报后声称全部成功。将最终语义同步到协议文档和测试。
- TTS 使用本机可用中文语音；不存在时明确报错，显示仍可完成。中文姓名发音可作为后续显式配置，不自动改显示文本。TTS API 返回成功也不代表教室中每个人都听见。
- Core Audio 对选定扬声器控制系统主音量/静音，与麦克风采集严格分离。检测热插拔、禁用、远程桌面变化和设备丢失；不得偷偷切到另一个输出设备。
- 广播 payload.volume 推荐解释为本次临时音量；完成、取消或失败后条件恢复。如果教师执行期间手动改音量，不覆盖教师的改动。`volume.set.restore_after_broadcast` 的当前服务端只转发参数，客户端实现前明确其一次性恢复语义并补测试；普通 volume.set 则持续生效。不要为了发声擅自取消静音，除非命令明确要求。
- `welcome.output`、`welcome.media` 提供输出读数和当前摄像头/媒体期望状态；音量上限位于 `media.volume_ceiling`。重连上报客户端实际状态，输出配置变化成功后落盘；不要臆造配置拉取端点。

## 6. 摄像头与媒体：按现有接口边界实施

教室现有软件是掌上看家，摄像头型号未知。先检测 Windows 是否暴露本机摄像头和麦克风。掌上看家能显示画面不代表系统有可枚举的采集设备。优先 Media Foundation 等 Windows 接口，必要时评估 DirectShow/FFmpeg；不要读其他软件的密码、强制结束掌上看家进程或默认录制整个桌面。

本机摄像头能力：设备清单、占用检测、分辨率/帧率、视频与音频分开检测，单路采集。同一个 camera_id 更换后会增加 config_revision。终端串行处理 stop/start，启新前确保旧采集停止；旧版本的 stop 不能停掉新版本。停止失败就禁止并行开第二路。回报心跳格式：

```json
{"type":"heartbeat","camera_state":{"camera_id":"当前ID","config_revision":2,"identifier":"当前设备标识","state":"connected","audio_capable":false,"video":{"width":640,"height":480,"fps":10}}}
```

无观看者而主动停止时上报 `idle|stopped`，服务器会恢复为已登记；`audio_capable` 表示来源的音频能力，不是当前是否正在采集，不要因为 audio=false 就误报无音频能力。失败状态使用 `failed|unavailable|busy` 和脱敏 `error`。只有真实采集可用才能报告 connected；此状态也不等于网页媒体链路可用。

实际采集以服务器媒体期望状态为准，不因配对或单纯登记摄像头就全天采集。状态格式如下：

```json
{
  "type":"media_state", "enabled":true, "video":true, "audio":false,
  "valid_until":"2026-09-24T10:01:30+08:00", "volume_ceiling":80, "ice_servers":[],
  "camera":{"camera_id":"当前ID","config_revision":2,"identifier":"cam-1","microphone_identifier":"mic-1",
            "source_kind":"windows_device","access_path":"windows_capture","video":{},"audio_capable":true},
  "publish":{"video":"/api/v1/classroom/device/media/当前ID/2/video/offer"}
}
```

- `enabled=false`、授权到期、WebSocket 断开、被撤销、用户退出：立即停止采集与发布。audio=false 必须真正关闭麦克风采集/音轨发布，不能靠静音冒充未采集。多观看者共用一个视频发布连接，声音另有一个按需音频连接；关闭最后一个声音观看者后，服务器撤销音轨需求并推送更新，不必重建视频连接。
- 服务器主动推送音视频需求变化，也在 hello/heartbeat 响应中提供完整状态；`valid_until` 是本机停止采集的硬期限，不自行续期。相同 camera_id/revision/track 的需求保持时复用现有连接，不每次心跳重建。
- 本地选择的采集设备来自 camera.identifier，现场声音来自明确选择的 microphone_identifier；缺失或设备不可用时回报错误。`camera.video` 是服务端保存的编码偏好；缺省按 640×360 或 640×480、10 fps、H.264 无 B 帧及约 700 kbps 起步，声音使用单声道 Opus 48 kHz。
- 每个 track 使用一个 WebRTC **sendonly** PeerConnection。视频 SDP 只能含一个 video media section，音频 SDP 只能含一个 audio media section，禁止混入另一轨或 data channel。H.264 与 Opus 是本项目实测路径。
- 收集完整 ICE 候选后向 `publish[track]` 发 JSON `{"sdp":"本机offer"}`；使用 `Authorization: Bearer <本设备credential>` 和 `X-ClassClaw-Device-ID: <device_id>`。返回统一响应 data：`{"peer_id":"…","sdp":"服务器answer","track":"video"}`。设置远端 answer，真实连通后才报 connected。当前使用完整 ICE 协商，不需要实现 trickle PATCH。
- 关闭时本地释放 PeerConnection，并 `POST /api/v1/classroom/device/media/close`，同一套设备鉴权，JSON `{"peer_id":"…"}`；关闭失败不应阻止本地停止采集。后端也会按租约与设备连接状态强制删除远端会话。
- 请求被返回 MEDIA_PUBLISH_DENIED 时重新同步媒体状态，不能擅自重试旧版本。MEDIA_NOT_CONFIGURED / MEDIA_UNAVAILABLE 表示服务端媒体未启用或暂不可用，托盘明确显示并保持其他功能正常。
- `windows_relay` 的 `camera.source` 会经本设备加密控制通道给出 `protocol`、`location` 及必要的 `username/password`；只在内存中用于该摄像头，不写日志或广播账本。使用支持该来源协议的采集库，校验来源、防止转发到服务器任意地址。`server_direct` 由服务器采集，Windows 不重复拉流。
- 摄像头更换或版本变化时停止旧源后再发布新源；收到旧版本 camera.stop 不可停止新源。任何本地 camera.start 指令都不能越过 media_state 的授权自行采集。

服务器已有按需单帧采样、OpenClaw 概况与自动释放临时租约。Windows 只要按 video=true 发布即可，不需要上传额外截图、分析学生、调用大模型或实现服务端接口。

先按低负载测试 480p 或 720p、10–15 fps、合理码率，再依据实际设备调整。优先复用已有编码/硬件编码，避免同时编码多路。没有观看者时不持续推流，不默认全天录像。实际 CPU、内存、带宽和延迟必须现场测量，不编造数字。

## 7. 交付与验收

先完成可运行的配对→WebSocket→网页广播→真实屏幕和中文语音→回执闭环，再音量与设备检测，最后完成媒体采集与发布。按当前完整服务端协议逐步联调。

交付源码、依赖版本说明、构建与打包脚本、安装/升级/卸载说明和 Windows README。提供普通用户可运行的发布包，区分真实构建包与示例文件；升级保留合法配对凭据及必要去重状态，卸载清理登录启动项。没有 Windows 构建环境时明确说明未完成的打包/硬件验收，不虚构 .exe 或实测结果。

至少覆盖：

1. 配对码无效/过期/429、凭据落盘、二次启动、服务端撤销、协议不匹配与断线退避。
2. 单实例、登录自启到托盘、关闭设置返回托盘、正常退出、屏幕锁定/用户注销不误报可显示。
3. 中文、长句、多选逐人、重复次数、空中文 TTS、指定屏幕丢失；显示与语音分路回执。
4. 重复 command_id 只执行一次，回执丢失不重播，进程崩溃/服务器重启/断线重连不补播，过期命令不执行。
5. 点历史广播的 stop/clear 不影响另一条当前广播；停止指令不被长 TTS 阻塞。
6. 临时音量恢复、手动音量变化、静音、输出设备拔出，不影响麦克风权限。
7. 摄像头占用、拔出、版本更换、停止失败不启第二路；video/audio 授权变更和 valid_until 到期确实停止对应采集。
8. 使用真实测试服务器联调固定消息，保留脱敏日志；在目标低配 Windows 上测量空闲/广播/采集资源占用。

执行仓库相关 pytest、网页测试和插件检查，任何服务器协议改动必须补回归与文档。若修改数据模型，新增 Alembic 迁移。不要以占位 success 或只通过 mock 测试宣称真实设备完成。

最终报告清楚列出：已实现、自动测试结果、真实 Windows 验证结果、仍受硬件或部署环境限制的事项，以及交付文件位置。服务端如果与本文有变化，先核对当前代码，报告具体接口差异；不要默认把服务端功能重新实现一遍。
