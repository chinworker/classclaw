"""教室终端、点名广播、摄像头登记与观看租约的请求模型。

终端协议模型对多余字段一律拒绝：终端只能收到固定动作，不能收到自由文本指令。
"""
from __future__ import annotations

from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

_CONTROL_TABLE = {code: None for code in range(0x20)}
_CONTROL_TABLE[0x7F] = None
_CONTROL_TABLE[ord("\n")] = " "
_CONTROL_TABLE[ord("\t")] = " "
_CONTROL_TRANSLATION = str.maketrans(_CONTROL_TABLE)

CommandKind = Literal[
    "broadcast.show",
    "broadcast.stop",
    "broadcast.clear",
    "volume.set",
    "device.test_display",
    "device.test_speak",
    "device.configure",
    "camera.start",
    "camera.stop",
]


def clean_text(value: str) -> str:
    """广播文本只用于显示和播报：去掉控制字符，保留老师写的原文。"""
    return value.translate(_CONTROL_TRANSLATION).strip()


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class BroadcastRecipient(_Strict):
    student_no: str = Field(min_length=1, max_length=50)
    name: str = Field(min_length=1, max_length=100)


class BroadcastSegment(_Strict):
    index: int = Field(ge=0)
    text: str = Field(min_length=1, max_length=400)
    recipients: list[BroadcastRecipient] = Field(default_factory=list, max_length=60)


class BroadcastShowPayload(_Strict):
    """下发终端的冻结内容。终端不得重新补全、改写或重排。"""

    segments: list[BroadcastSegment] = Field(min_length=1, max_length=60)
    display_seconds: int = Field(ge=3, le=600)
    repeat_count: int = Field(ge=1, le=10)
    gap_seconds: int = Field(ge=0, le=60)
    volume: int | None = Field(default=None, ge=0, le=100)
    target_screen: str | None = Field(default=None, max_length=200)


class EmptyPayload(_Strict):
    pass


class BroadcastControlPayload(_Strict):
    target_command_id: str = Field(min_length=1, max_length=36)


class VolumePayload(_Strict):
    volume: int | None = Field(default=None, ge=0, le=100)
    mute: bool | None = None
    restore_after_broadcast: bool = False


class TestTextPayload(_Strict):
    text: str = Field(min_length=1, max_length=200)


class DeviceConfigurePayload(_Strict):
    """老师预选的显示屏与扬声器。取值必须来自终端上报的清单，不接受任意字符串。"""

    display_name: str | None = Field(default=None, max_length=200)
    audio_output_name: str | None = Field(default=None, max_length=200)


class CameraControlPayload(_Strict):
    camera_id: str = Field(min_length=1, max_length=36)
    config_revision: int = Field(ge=1)
    identifier: str | None = Field(default=None, max_length=200)
    access_path: Literal["windows_capture", "server_direct", "windows_relay"]
    audio: bool = False


COMMAND_PAYLOADS: dict[str, type[BaseModel]] = {
    "broadcast.show": BroadcastShowPayload,
    "broadcast.stop": BroadcastControlPayload,
    "broadcast.clear": BroadcastControlPayload,
    "volume.set": VolumePayload,
    "device.test_display": TestTextPayload,
    "device.test_speak": TestTextPayload,
    "device.configure": DeviceConfigurePayload,
    "camera.start": CameraControlPayload,
    "camera.stop": CameraControlPayload,
}


class DeviceCapabilities(_Strict):
    display: bool = True
    speak: bool = False
    volume_control: bool = False
    chinese_tts: bool = False
    capture: bool = False
    displays: list[str] = Field(default_factory=list, max_length=20)


class DeviceInventory(_Strict):
    """Windows 检测到的设备清单；只用于网页展示与选择，不作为绑定权威。"""

    cameras: list[dict] = Field(default_factory=list, max_length=40)
    microphones: list[dict] = Field(default_factory=list, max_length=40)
    speakers: list[dict] = Field(default_factory=list, max_length=40)


class DevicePairRequest(_Strict):
    pairing_code: str = Field(min_length=4, max_length=32)
    protocol_version: int = Field(default=1, ge=1, le=99)
    app_version: str = Field(default="", max_length=50)
    os_version: str = Field(default="", max_length=100)
    device_name: str = Field(default="", max_length=100)
    capabilities: DeviceCapabilities = Field(default_factory=DeviceCapabilities)
    inventory: DeviceInventory = Field(default_factory=DeviceInventory)


class DeviceHello(_Strict):
    type: Literal["hello"] = "hello"
    device_id: str = Field(min_length=1, max_length=36)
    credential: str = Field(min_length=8, max_length=200)
    protocol_version: int = Field(default=1, ge=1, le=99)
    app_version: str = Field(default="", max_length=50)
    os_version: str = Field(default="", max_length=100)
    capabilities: DeviceCapabilities = Field(default_factory=DeviceCapabilities)
    inventory: DeviceInventory = Field(default_factory=DeviceInventory)
    display_name: str = Field(default="", max_length=200)
    audio_output_name: str = Field(default="", max_length=200)
    volume_level: int | None = Field(default=None, ge=0, le=100)
    muted: bool = False


class DeviceHeartbeat(_Strict):
    type: Literal["heartbeat"] = "heartbeat"
    capabilities: DeviceCapabilities | None = None
    inventory: DeviceInventory | None = None
    display_name: str | None = Field(default=None, max_length=200)
    audio_output_name: str | None = Field(default=None, max_length=200)
    volume_level: int | None = Field(default=None, ge=0, le=100)
    muted: bool | None = None
    camera_state: dict | None = None


class CommandChannelResult(_Strict):
    """显示与播报结果分别返回；收到指令不等于已完成。"""

    type: Literal["command_result"] = "command_result"
    command_id: str = Field(min_length=1, max_length=36)
    state: Literal["started", "succeeded", "failed", "unknown"]
    display: Literal["pending", "shown", "unavailable", "failed", "cleared"] | None = None
    speak: Literal["pending", "spoken", "unavailable", "failed", "skipped"] | None = None
    error_code: str = Field(default="", max_length=100)
    error_message: str = Field(default="", max_length=400)


class BroadcastComposeRequest(BaseModel):
    """网页与 Agent 共用的广播草稿。class_id 由路由或班级隔离逻辑注入。"""

    model_config = ConfigDict(str_strip_whitespace=True)

    class_id: str | None = None
    mode: Literal["three_part", "custom"] = "three_part"
    student_ids: list[str] = Field(default_factory=list, max_length=60)
    salutation: str = Field(default="同学", max_length=60)
    time_phrase: str = Field(default="现在", max_length=60)
    predicate: str = Field(default="", max_length=200)
    text: str = Field(default="", max_length=400)
    merge_mode: Literal["combined", "per_student"] = "combined"
    display_seconds: int | None = Field(default=None, ge=3, le=600)
    repeat_count: int = Field(default=1, ge=1, le=10)
    gap_seconds: int = Field(default=0, ge=0, le=60)
    volume: int | None = Field(default=None, ge=0, le=100)
    target_screen: str | None = Field(default=None, max_length=200)
    expected_texts: list[str] | None = Field(default=None, max_length=60)

    @field_validator("salutation", "time_phrase", "predicate", "text", "target_screen")
    @classmethod
    def strip_control(cls, value: str | None) -> str | None:
        return None if value is None else clean_text(value)

    @field_validator("student_ids")
    @classmethod
    def unique_students(cls, value: list[str]) -> list[str]:
        return list(dict.fromkeys(value))

    @model_validator(mode="after")
    def validate_mode(self) -> Self:
        if self.mode == "custom":
            if not self.text:
                raise ValueError("自定义句子不能为空")
            if self.student_ids:
                raise ValueError("自定义句子按原文播报，不另选学生")
        else:
            if not self.student_ids:
                raise ValueError("三段式点名必须至少选择一名学生")
            if not self.predicate:
                raise ValueError("三段式点名必须填写事项（谓词）")
            if not self.salutation and not self.time_phrase and not self.predicate:
                raise ValueError("称谓、时间与事项不能同时为空")
        return self


class VolumeSetRequest(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    class_id: str | None = None
    volume: int | None = Field(default=None, ge=0, le=100)
    mute: bool | None = None
    restore_after_broadcast: bool = False

    @model_validator(mode="after")
    def require_change(self) -> Self:
        if self.volume is None and self.mute is None:
            raise ValueError("至少提供音量或静音其中一项")
        return self


class DeviceTestRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Literal["display", "speak"]


class DeviceOutputRequest(BaseModel):
    """预选教室显示屏与扬声器；不能默认把教师个人副屏当作全班显示屏。"""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    display_name: str | None = Field(default=None, max_length=200)
    audio_output_name: str | None = Field(default=None, max_length=200)

    @model_validator(mode="after")
    def require_target(self) -> Self:
        if not self.display_name and not self.audio_output_name:
            raise ValueError("至少选择显示屏或扬声器其中一项")
        return self


class CameraConnectRequest(BaseModel):
    """登记或更换本班唯一摄像头。凭据只接受服务器侧引用，不接受明文。"""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    name: str = Field(default="教室摄像头", min_length=1, max_length=100)
    access_path: Literal["windows_capture", "server_direct", "windows_relay"]
    source_kind: Literal["windows_device", "network_stream"]
    device_identifier: str | None = Field(default=None, max_length=200)
    device_label: str | None = Field(default=None, max_length=200)
    microphone_identifier: str | None = Field(default=None, max_length=200)
    protocol: Literal["rtsp", "rtmps", "rtmp", "http", "https", "whip"] | None = None
    location: str = Field(default="", max_length=500)
    credential_ref: str = Field(default="", max_length=100)
    video: dict = Field(default_factory=dict)
    audio_capable: bool = False
    expected_revision: int | None = Field(default=None, ge=1)

    @model_validator(mode="after")
    def validate_source(self) -> Self:
        if self.source_kind == "windows_device":
            if not self.device_identifier:
                raise ValueError("Windows 采集设备必须提供稳定的设备标识")
            if self.access_path == "server_direct":
                raise ValueError("服务器直读只能用于网络摄像头")
            if self.location or self.protocol or self.credential_ref:
                raise ValueError("Windows 采集设备不需要流地址、协议或凭据引用")
        else:
            if self.access_path == "windows_capture":
                raise ValueError("网络摄像头不能声明为 Windows 本机采集")
            if not self.location:
                raise ValueError("网络摄像头必须提供流地址")
            if not self.protocol:
                raise ValueError("网络摄像头必须明确协议")
        return self


class MediaSessionCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    audio: bool = False
    surface: Literal["web", "agent"] = "web"


class PairingIssueRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(default="教室终端", min_length=1, max_length=100)


class MediaOffer(_Strict):
    sdp: str = Field(min_length=10, max_length=65536)


class MediaPeerClose(_Strict):
    peer_id: str = Field(min_length=1, max_length=64)
