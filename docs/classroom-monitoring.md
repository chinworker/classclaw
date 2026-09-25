# 班级监控、点名广播与教室终端（服务端实现）

本文描述已经落地的部分。设计动机、现场验证清单和 Windows 端形态见 `docs/classroom-monitoring-plan.md`。

实现状态：

| 部分 | 状态 |
| --- | --- |
| 服务端控制面（配对、心跳、命令账本、广播、音量、摄像头登记、观看租约） | 已实现并有回归测试 |
| 网页端「点名广播」「教室设备与实时监控」 | 已实现并有 node --test 回归 |
| Agent / ClassClaw Channels 的点名与音量提案、状态查询 | 已实现（复用 WriteProposal，无新增工具） |
| Windows 终端 Classroom Mate | 已实现于 `classroom-mate/`：托盘、自启、广播、音量、分轨媒体发布和 Windows x64 打包；协议与合成媒体验证通过，真实 Windows 硬件待验收 |
| 媒体转发（MediaMTX）与浏览器播放 | 已实现分轨 WHIP/WHEP、真实媒体鉴权与回收；默认关闭，部署见 `docs/classroom-media-deployment.md` |
| 按需 AI 教室概况 | 已实现单帧画面概况，网页与 Channel 均可显式请求；不做声音概况 |

## 1. 分层与模块

沿用既有分层：路由只做 HTTP、身份与班级归属校验，组合句子、冻结文本、命令账本、摄像头唯一性与观看租约都在 Service 层。

- `app/models/entities.py`：`ClassroomDevice`、`ClassroomCamera`、`ClassroomBroadcast`、`ClassroomDeviceCommand`、`ClassroomMediaSession`（迁移 `0017` 与 `0018` 麦克风字段）
- `app/schemas/classroom.py`：网页/Agent 请求模型与终端协议模型；命令 payload 全部 `extra="forbid"`
- `app/services/classroom_channel.py`：进程内 WebSocket 注册表，只保存在线连接
- `app/services/classroom_devices.py`：配对、凭据、心跳、命令账本、到期清扫、音量与输出设备
- `app/services/classroom_broadcast.py`：句子组合、冻结、执行、停止与清除
- `app/services/classroom_camera.py`：每班唯一摄像头的登记、更换与断开
- `app/services/classroom_media.py`：观看租约、音轨授权、宽限停流
- `app/services/classroom_streaming.py`：分轨信令、签名媒体鉴权、按需采集状态与连接回收
- `app/services/classroom_sources.py`：服务器直读 RTSP、FFmpeg 转码、内存单帧采样
- `app/services/classroom_observation.py`：临时视频租约与单帧概况
- `app/api/v1/classroom_streaming.py`：设备发布、本人观看信令、关闭与概况路由
- `web/js/classroomPlayer.js`：每条授权媒体轨使用独立 WebRTC 连接
- `app/services/classroom.py`：只读概况聚合
- `app/api/v1/classroom.py`：教师/管理员接口（受 `require_authenticated` 保护）
- `app/api/v1/classroom_terminal.py`：终端接口，用设备凭据鉴权，挂在受保护路由之外
- `web/js/pages/broadcast.js`、`web/js/pages/classroom.js`

不引入 Redis、Celery、消息队列或业务微服务。SQLite 是唯一的执行账本；控制连接在线就直接投递，**不做离线命令积压**：终端不在线时实时点名和音量操作被拒绝（409 `DEVICE_OFFLINE`），恢复网络后不会补播上一节课的点名。

## 2. 数据模型要点

- 每班一台终端、一路摄像头，由 `uq_classroom_device_class`、`uq_classroom_camera_class` 与 Service 校验共同保证；网页限制不能代替服务端约束。
- 终端凭据与配对码只存 SHA-256 哈希，明文只在生成时返回一次。
- 广播记录保存**冻结后的**分段文本与对象（学号＋姓名），不存学生 UUID，因此名单变更或删除后记录仍可解释。
- 命令账本状态：`authorized`（已登记）→ `delivered`（终端已收到）→ `executing` → `succeeded` / `failed`；另有 `expired`（未回执）与 `unknown`（已开始执行但结果未知）。
- 广播的 `display_status` 与 `speak_status` 分别记录，屏幕显示成功不代表播报成功。
- 观看租约只存令牌哈希，绑定用户、班级、摄像头与音视频权限，带 `expires_at`。

数据库提交与物理显示/播报不是同一事务，所以**提案 completed 不等于已发出声音**。终端以 `command_id` 去重；回执丢失时先查状态，未知不自动重播。

## 3. 终端协议

### 3.1 配对

1. 网页 `POST /classes/{id}/classroom/pairing` 生成一次性配对码（`K7M2-P9QX` 形式，29^8 空间，默认 600 秒有效）。已配对的班级必须先撤销凭据才能重新配对。
2. 终端 `POST /api/v1/classroom/device/pair` 用配对码换取本设备专用凭据。该端点不需要用户会话，按来源 IP 限流（每 300 秒 10 次，超出返回 429 `DEVICE_PAIRING_THROTTLED`）。
3. 凭据只返回一次；终端本地加密保存。终端不持有 `CLASSCLAW_API_TOKEN` 或 Gateway 管理员 Token。

### 3.2 控制通道

`WS /api/v1/classroom/device-channel`。终端主动出站连接，学校不需要开放入站端口。

首条消息必须是 `hello`，之后服务端才认为该设备在线：

```json
{"type":"hello","device_id":"…","credential":"…","protocol_version":1,
 "app_version":"1.0.0","os_version":"Windows 10","capabilities":{…},
 "inventory":{"cameras":[…],"microphones":[…],"speakers":[…]},
 "display_name":"教室一体机","audio_output_name":"教室音箱","volume_level":30,"muted":false}
```

| 方向 | 消息 | 说明 |
| --- | --- | --- |
| 终端→服务器 | `hello` | 鉴权并上报能力与设备清单；返回 `welcome` |
| 终端→服务器 | `heartbeat` | 更新 `last_seen_at`、音量、输出设备、清单与摄像头采集状态；返回 `heartbeat_ack` |
| 终端→服务器 | `command_result` | `state: started\|succeeded\|failed\|unknown`，并分别给出 `display` 与 `speak` 结果 |
| 服务器→终端 | `welcome` | `class_id`、`config_revision`、`command_ttl_seconds`、建议心跳间隔 |
| 服务器→终端 | `command` | `command_id`、`kind`、`payload`、`expires_at` |
| 服务器→终端 | `error` | `code` 与说明 |

关闭码：`4400` 协议错误、`4401` 凭据无效或已撤销、`4408` 心跳超时。撤销凭据、解绑终端或删除班级时，服务端主动发出 `DEVICE_REVOKED` 错误帧再关闭，让终端能区分「被撤销」和「网络断开」，不拿着失效凭据反复重连。

连接是长连接，因此每次消息只开一个短数据库会话（`anyio.to_thread.run_sync` + `writer_session`），不整段持有写锁。`receive()` 只在消费完成后重建，绝不取消在途接收，避免丢消息。

### 3.3 命令白名单

终端只能收到这些固定动作，参数都经 Pydantic 校验；**禁止下发模型生成的 shell、路径或任意可执行命令**。

| kind | payload |
| --- | --- |
| `broadcast.show` | `{segments:[{index,text,recipients:[{student_no,name}]}], display_seconds, repeat_count, gap_seconds, volume?, target_screen?}` |
| `broadcast.stop` / `broadcast.clear` | `{target_command_id}`，指向原始 `broadcast.show` 命令；只停止该命令的播报或清除其画面，不影响其他广播 |
| `volume.set` | `{volume?, mute?, restore_after_broadcast}` |
| `device.test_display` / `device.test_speak` | `{text}`，测试文本由服务器固定生成 |
| `device.configure` | `{display_name?, audio_output_name?}`，取值必须来自终端上报的清单 |
| `camera.start` / `camera.stop` | `{camera_id, config_revision, identifier?, access_path, audio}` |

命令带包含时区的 `expires_at`（默认 600 秒）。终端必须拒绝已过期命令，并按 `command_id` 去重。`command_result` 的确认消息是 `command_ack`，含 `command_id`、`status`、`duplicate`。

重连 `hello` 会终结旧连接遗留的命令，不在新连接上补发。推送前、回执处理前与详情轮询时同样进行到期检查；迟到的成功回执不能把过期/未知记录改回成功。停止/清屏指令有自己的结果，不能覆盖原始广播的显示与播报结果。广播的终结回执缺少某一路结果时，该路记为 `unknown`，不能无限保持执行中。

### 3.4 投递与状态收敛

登记与投递分离：Service 在调用方事务内写入 `authorized` 命令并把 id 挂到 `db.info["classroom_dispatch"]`，**提交之后**才唤醒控制通道推送。这样「已提交」不会被误报成「已送达」。

`expire_overdue()` 是命令状态的唯一权威，在概况与广播列表读取时惰性执行，两种触发条件：

1. TTL 到期：`authorized`/`delivered` → `expired`，`executing` → `unknown`；
2. 终端已不在控制通道上：同样收敛，因此服务器重启后（注册表为空）也不会留下永远「已登记」的命令。

GET 仍使用读会话；需要持久化到期状态时，Service 通过独立 `writer_session()` 串行执行短事务，不直接用读连接写库。连接被新连接替换后，旧连接结束不能修改新连接的状态。

## 4. 点名广播

「三个词」落实为三个可编辑片段，默认模板固定为 `请{称谓}{时间}{谓词}。`：

- 单选：`请张三同学现在去扫地。`
- 多选合并：`请张三、李四同学下课后来老师办公室。`
- 多选逐人：按**学号自然升序**为每人生成一句完整句子，逐句显示并播报
- 自定义：原样使用老师填写的完整句子，不追加「请」「同学」或标点，也不选学生

组合是确定性的：相同输入产生相同句子。冻结内容包括分段文本、对象（学号＋姓名）、顺序、停留时长、播报次数与间隔。发送后客户端不能再补全或改写；终端收到的就是这段冻结文本。

称谓留空时只使用姓名。网页任何编辑都立即使旧预览失效；忽略乱序返回的旧预览，只允许提交最后一次预览的输入和 `expected_texts`。若名单姓名等在预览后改变，服务端返回 `BROADCAST_PREVIEW_STALE`，要求重新预览。目标屏幕必须来自终端清单，空清单不放开校验。

限制由配置决定：单句字数 `broadcast_max_chars`（默认 160）、逐人句数 `broadcast_max_segments`（默认 20）、停留时长上限 `display_seconds_max`、播报次数上限 `speak_repeat_max`、音量上限 `volume_ceiling`。超出直接报错，不静默截断。控制字符会被清除，其余原文保留。

**这不是考勤**：广播不创建考勤记录、不判断到场情况，也不会因为「去扫地」自动生成值日安排或把任务标记完成。

广播中的「下课后」「今天大课间」首先是**通知内容**，默认立即显示并播报。只有用户明确要求「下课后再播报」才是定时任务；首期不支持定时广播，Agent 必须说明当前只能立即播报，不默默创建延时任务。

幂等：`idempotency_key` 在写入广播记录**之前**检查，因此重试不会产生「多一条记录但只播一次」的错觉。Agent 路径使用 `broadcast:proposal:{proposal_id}`。

## 5. 音量与输出设备

- 音量上限来自 `classroom.volume_ceiling`，网页与 Agent 共用；超过返回 422 `VOLUME_ABOVE_CEILING`，不静默截断。
- 终端未上报 `volume_control` 能力时拒绝执行（409 `VOLUME_UNSUPPORTED`）。
- 显示屏与扬声器的取值必须来自终端上报的清单，否则 422 `DEVICE_OUTPUT_UNAVAILABLE`；不能默认把教师个人副屏当作全班显示屏。
- 服务端不假装已生效：接口返回的是登记状态与终端**上一次回报**的值，实际值以下一次回执/心跳为准。
- 能力缺失（无可用显示屏、无中文 TTS）以 `device_warnings` 明确提示，但不阻塞屏幕显示——显示与播报是两件事。

## 6. 摄像头与观看租约

摄像头连接、更换、断开只能从网页发起。

- 每班最多一路：数据库唯一约束 + Service 校验。
- 更换必须显式指认替换对象并带上 `expected_revision`；版本不符返回 409 `CAMERA_REVISION_CONFLICT`，未带版本返回 409 `CAMERA_ALREADY_CONNECTED` 并附当前对象供页面展示。并发更换按配置版本拒绝过时请求。
- 更换事务固定登记顺序：撤销旧观看会话 → 登记 `camera.stop` → 写入新配置 → 登记 `camera.start`；提交后按登记顺序（SQLite `rowid`）投递。终端必须串行处理摄像头动作，确保旧采集已停止后才启新。当前服务端没有等待停止回执后再下发启动的两阶段编排，不能将登记顺序等同于物理执行成功。
- `stop_requested` / `capture_requested` 只表示指令已登记投递；`capture_started=false` 与 `old_connection_stopped=false` 表示同步请求尚未证明采集或停止。媒体未启用时只登记网络来源配置；启用后由有效观看需求驱动采集，绝不把登记结果当作真实播放成功。
- 流地址只允许 `rtsp/rtmps/rtmp/http/https/whip`，协议必须与所选一致；**不允许内嵌账号密码**（422 `CAMERA_CREDENTIAL_IN_URL`），并拒绝环回与云元数据地址（422 `CAMERA_LOCATION_FORBIDDEN`），防止把服务器自身当成摄像头或 SSRF。
- 摄像头凭据不入库明文：只保存引用名 `credential_ref`，实际值从服务器环境变量 `CLASSCLAW_CLASSROOM_CAMERA_<引用名大写>` 读取。缺失时 503 `CAMERA_CREDENTIAL_MISSING`。引用名与明文都不回传浏览器，也不写日志；接口只返回 `credential_configured` 布尔值。
- 摄像头是否真正可用以终端心跳回报的 `camera_state` 为准：必须包含匹配的 `camera_id`、`config_revision`、`identifier` 与 `state`，可带 `error`、`video`、`audio_capable`。旧版本/旧标识的状态不更新当前配置；控制通道断开后不能继续显示已连通。登记后是 `registered`，连通后才变 `connected`，失败变 `failed`。
- 断开后点名广播与音量功能仍可用。

观看租约：

- `POST /classes/{id}/classroom/media-sessions` 发放短期租约（默认 300 秒），令牌只返回一次，服务端只存哈希。
- 音轨授权在**媒体层**执行：只有 `audio=true` 且摄像头确实有音轨时才 `audio_allowed=true`；没有音轨时明确给出原因，不靠浏览器静音充当权限。
- 续期延长有效期；到期、离页释放、登出、撤销终端凭据、更换或断开摄像头会终止租约。最后一名观看者离开后计算 `media_stop_grace_seconds` 宽限，一人关闭不影响其他合法观看。后台正常情况下每 2 秒核对并删除失效的媒体连接；失败会继续重试。音频在最后一位音频观看者离开后停止，仅视频有宽限。网页同一视图只创建一个租约，离页后才返回的创建结果也立即释放。
- JSON 信令校验本班用户与本人租约（`classroom_streaming.valid_lease`）；终端发布仅接受本设备凭据、当前摄像头版本与有效采集需求。MediaMTX 再通过短签名凭据校验操作和媒体路径；内部媒体地址和签名不发给浏览器。

**实际媒体协议**：配置 `mediamtx` 并安装同机组件后，租约返回 `available=true`、`offers.video` 和获授权时的 `offers.audio`，不返回永久播放 URL。网页分别发送单轨 recvonly SDP；Windows 分别发送单轨 sendonly SDP。所有 offer 都通过 ClassClaw 的 JSON 接口，ICE 候选完整收集后一次提交。媒体未配置返回 `MEDIA_NOT_CONFIGURED`，控制 API 尚未就绪返回 `MEDIA_UNAVAILABLE`。

- `POST /classroom/device/media/{camera_id}/{revision}/{track}/offer`：设备 ID 头与设备 Bearer 凭据；返回 `peer_id/sdp/track`。
- `POST /classroom/device/media/close`：关闭本设备 `peer_id`。
- `POST /classes/{id}/classroom/media-sessions/{session_id}/{track}/offer`：网页身份 + 本人租约；无声音权限不能提交 audio 或混轨 SDP。
- `POST /classes/{id}/classroom/media/close`：关闭本人 `peer_id`。
- `POST /classroom/media/auth`：仅同机 MediaMTX 回调，公网 Nginx 禁止访问。
- `welcome.media`、`heartbeat_ack.media` 和主动 `media_state` 包含 `enabled/video/audio/valid_until/camera/publish/ice_servers/volume_ceiling`；其中 camera 明确区分视频设备和 `microphone_identifier`。采集必须受状态与授权期限约束，登记本身不代表允许全天采集。
- 本机设备选择声音时必须选终端已上报的麦克风。网络来源的账号密码只通过本设备加密控制通道交付，服务器直读仅支持 RTSP。服务器直读的 connected 状态来自 MediaMTX 实际 ready 路径。

`POST /classes/{id}/classroom/observation` 生成一次现场概况：临时无声音租约 → 内存 JPEG → OpenClaw 图片分析 → 释放租约。返回采样时间、`source=single_video_frame`、`audio_used=false`、可见环境/活动及限制。每班仅允许一个在途采样，失败同样释放。图片不进入业务库或附件；Gateway 临时分析会话仍遵循既有保留/清理策略。普通设备状态轮询不会触发采样。

部署、媒体端口、生命周期、自测与现场验收见 [媒体部署](classroom-media-deployment.md)。Windows 的完整消息示例见 [交接提示词](windows-client-agent-prompt.md)。

## 7. Agent 与 ClassClaw Channels

没有新增插件工具：点名与音量复用既有 WriteProposal 通道，因此班级隔离、预览确认与批量原子提交的既有保证全部适用。

- 新增 operation：`classroom.broadcast.send`、`classroom.volume.set`（已加入 `SUPPORTED_OPERATIONS`、预览 spec、固定执行器与提取器 payload hints）。
- 新增只读资源：`classroom_status`、`classroom_camera`、`classroom_broadcast`（需 `class_id` 与 `broadcast_id`）、`classroom_observation`（明确请求时才采单帧，不自动轮询）。
- 学号解析：`classroom.broadcast.send` 的 `student_nos` 经 `app/services/student_refs.py` 在绑定班级内确定性解析；歧义报 `STUDENT_AMBIGUOUS`，跨班报 403。
- 预览阶段就冻结完整句子，确认消息里直接列出原文，并说明这不是考勤。确认后不得改人、改时间、改事项或再让模型润色。
- 提案确认后由 `approval._dispatch_classroom()` 在提交之后推送命令，并把投递结果写进 `result_json.delivery`。因此 `completed` 只代表已登记下发；显示与播报结果只能依据终端回执。
- 终端离线时确认会整批回滚（提案保持 `pending_review`，不留广播记录），返回 409 `DEVICE_OFFLINE`。
- `before_tool_call` 已有逻辑对这两个操作强制注入 `class_id`/`bound_class_id` 并拒绝跨班，无需新增钩子。
- 能力边界写进 `classclaw-manager` Skill 与班级工作区 `AGENTS.md`：广播不是考勤、「下课后」是内容不是定时、注册不等于已播报、离线不补播、摄像头绑定与看画面只在网页。

关于提示词预算的两个实际约束：

1. 班级工作区 `AGENTS.md` 等文件受 `tests/test_approval_onboarding.py` 的 3400 字节总量断言限制，当前已用到 3396 字节，**没有余量**。因此教室规则不写进工作区，只放在 Skill 与提取器规则里。
2. `SKILL.md` 已有约 1.2 万字符，而 provisioning 设的是 `skillsLimits.maxSkillsPromptChars: 5000`。为确保关键约束不被截断，「广播不是考勤」和「completed 只代表已登记下发」这两条同时写在 Skill 开头（5000 字符内）和 `## Classroom terminal` 章节，细节放 `references/`。真正的强制点在后端：广播执行器只写广播表、句子在预览时冻结、`result_json.note` 与 `delivery` 会在提交结果里直接告知「以终端回执为准」，而且后端根本没有定时广播能力。

确定性网页操作（点名、音量、摄像头、观看）不依赖 OpenClaw 在线。

## 8. 删除与残留

班级删除沿用既有分阶段清理，业务阶段中：关闭控制连接 → 撤销观看租约 → 终止未执行命令 → 删除媒体会话、摄像头、命令、广播与终端记录。`deletion_operations.result_json.classroom` 记录关闭的连接数与撤销的租约数，`deleted_counts` 包含各表条数。删除进行中时 `app/core/security.py` 拒绝该班级的新写入，终端 `hello` 也会被 `DELETION_IN_PROGRESS` 拒绝。

解绑终端（`DELETE /classes/{id}/classroom/device`）只删除终端记录，**不删除**班级 Agent、记忆或其他 Channels；摄像头对终端的外键置空。

登出（`POST /auth/logout`）会撤销本人所有活跃观看租约。

## 9. 配置

`config/classclaw.toml` 的 `[classroom]` 段（也可用 `CLASSCLAW_CLASSROOM_*` 覆盖）：

| 键 | 默认 | 说明 |
| --- | --- | --- |
| `pairing_code_ttl_seconds` | 600 | 一次性配对码有效期 |
| `heartbeat_timeout_seconds` | 90 | 超过该时间无心跳即视为离线 |
| `command_ttl_seconds` | 600 | 命令有效期，必须大于 `display_seconds_max * speak_repeat_max` |
| `broadcast_max_chars` | 160 | 单句字数上限 |
| `broadcast_max_segments` | 20 | 逐人播报句数上限 |
| `display_seconds_default` / `display_seconds_max` | 15 / 120 | 停留时长默认与上限 |
| `speak_repeat_max` | 3 | 播报次数上限 |
| `volume_ceiling` | 100 | 音量上限 |
| `media_lease_seconds` / `media_stop_grace_seconds` | 300 / 30 | 观看租约与最后观看者宽限 |
| `media_provider` / `media_base_url` | `none` / `""` | 媒体转发组件，未接入时保持 `none` |

启动时会校验这些关系（例如命令有效期必须覆盖最长展示时间），不合法直接在启动前报 `ConfigurationError`。全部键都在管理员设置中心可见，并遵循既有的「环境变量 > TOML > 默认值」优先级。

摄像头凭据是敏感值，只放 `.env` 或服务器环境变量：`CLASSCLAW_CLASSROOM_CAMERA_<引用名大写>`。

## 10. 接口索引

教师/管理员（需登录，经班级归属校验）：

| 方法与路径 | 作用 |
| --- | --- |
| GET `/classes/{id}/classroom/status` | 终端、摄像头、观看与最近点名的只读概况 |
| POST / DELETE `/classes/{id}/classroom/pairing` | 生成 / 作废一次性配对码 |
| POST `/classes/{id}/classroom/device/revoke` | 撤销终端凭据并终止连接与观看会话 |
| DELETE `/classes/{id}/classroom/device` | 解绑终端记录 |
| POST `/classes/{id}/classroom/device/output` | 预选显示屏与扬声器 |
| POST `/classes/{id}/classroom/device/test` | 显示测试 / 播报测试 |
| POST `/classes/{id}/classroom/broadcasts/preview` | 组合并返回冻结句子，不写入 |
| POST / GET `/classes/{id}/classroom/broadcasts` | 显示并播报 / 分页历史 |
| GET `/classes/{id}/classroom/broadcasts/{broadcast_id}` | 单条记录与命令回执 |
| POST `/classes/{id}/classroom/broadcasts/{broadcast_id}/stop`、`/clear` | 停止播报 / 清除屏幕 |
| POST `/classes/{id}/classroom/volume` | 调整音量或静音 |
| GET / POST / DELETE `/classes/{id}/classroom/camera` | 查询 / 登记或更换 / 断开 |
| GET / POST `/classes/{id}/classroom/media-sessions` | 观看人数 / 发放租约 |
| POST / DELETE `/classes/{id}/classroom/media-sessions/{session_id}` | 续期 / 离页释放 |

终端（设备凭据鉴权，不走用户会话）：

| 方法与路径 | 作用 |
| --- | --- |
| POST `/classroom/device/pair` | 用一次性配对码换取本设备凭据 |
| WS `/classroom/device-channel` | 控制通道：hello / heartbeat / command / command_result |

## 11. 错误码

`DEVICE_PAIRING_INVALID`、`DEVICE_PAIRING_EXPIRED`、`DEVICE_PAIRING_THROTTLED`、`DEVICE_PROTOCOL_UNSUPPORTED`、`DEVICE_UNAUTHORIZED`、`DEVICE_ALREADY_PAIRED`、`DEVICE_NOT_PAIRED`、`DEVICE_OFFLINE`、`DEVICE_OUTPUT_UNAVAILABLE`、`DEVICE_IDLE`、`DEVICE_DISCONNECTED`、`VOLUME_ABOVE_CEILING`、`VOLUME_UNSUPPORTED`、`BROADCAST_TOO_LONG`、`BROADCAST_TOO_MANY_SEGMENTS`、`BROADCAST_IDEMPOTENCY_CONFLICT`、`COMMAND_NOT_FOUND`、`COMMAND_EXPIRED`、`COMMAND_RESULT_UNKNOWN`、`CAMERA_ALREADY_CONNECTED`、`CAMERA_REVISION_CONFLICT`、`CAMERA_CREDENTIAL_IN_URL`、`CAMERA_CREDENTIAL_MISSING`、`CAMERA_LOCATION_FORBIDDEN`、`CAMERA_DISCONNECTED`、`MEDIA_SESSION_INVALID`、`MEDIA_SESSION_EXPIRED`、`MEDIA_NOT_CONFIGURED`、`MEDIA_UNAVAILABLE`、`MEDIA_TRACK_DENIED`、`MEDIA_PUBLISH_DENIED`、`PROTOCOL_ERROR`。

## 12. 测试

```bash
pytest tests/test_classroom_devices.py tests/test_classroom_broadcast.py \
       tests/test_classroom_camera_media.py tests/test_classroom_proposal.py \
       tests/test_classroom_channel.py
node --test tests/web/broadcastPage.test.mjs tests/web/classroomPage.test.mjs
cd integrations/openclaw/classclaw && npm test && npm run plugin:validate
```

覆盖：配对码一次性与过期、限流、协议版本、撤销后不能重连、离线拒绝实时操作、命令状态机与到期清扫、显示与播报分别回报、重复回执不改写已终结结果、学号自然升序冻结、自定义句子不套模板、字数与句数上限、预览只读、幂等不重复播报、广播不写考勤/值日/安排、每班唯一摄像头、并发更换版本冲突、先停旧再启新的顺序、凭据不出现在任何响应、流地址内嵌密码与环回被拒、租约音轨授权与到期、登出与更换即撤销会话、离页释放、跨班拒绝、班级删除清理、WebSocket 鉴权/心跳/投递/回执/撤销关闭、Agent 提案冻结与投递结果、插件班级隔离。

测试中终端控制通道用 `tests/helpers.py` 的 `online_terminal()`（内存连接，命令停在 `authorized`，由测试显式驱动送达与回执）或 `tests/test_classroom_channel.py` 的真实 WebSocket（`channel_db` fixture 把通道的短会话替换为测试库上的独立 Session）。

## 13. Windows 客户端与剩余验收

1. Windows 程序已实现于 [`classroom-mate/`](../classroom-mate/README.md)：.NET 10 WinForms 托盘、用户登录自启、网页配对、显示/TTS、Core Audio 音量、DirectShow 检测及 aiortc/PyAV 按需采集发布。包含自包含发布脚本、安装/卸载脚本、持久化去重与协议测试。广播的临时音量在结束后条件恢复；`restore_after_broadcast=true` 在下一次正式广播完成后恢复，已有广播时拒绝该设置，暂停/退出也恢复。
2. 真实 Windows 摄像头/麦克风、显示屏、扬声器、教室网络与 Linux 部署的现场验收。服务端真实媒体链路已通过合成画面/静音联调，不能替代硬件验收。
3. 全天录像回放、声音内容概况、身份识别/考勤不属于当前交付范围；本次只支持实时音视频查看与按需单帧概况。
