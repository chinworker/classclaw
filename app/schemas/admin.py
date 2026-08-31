from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator

from app.schemas.domain import ClassCreate


class AdminClassCreate(ClassCreate):
    owner_user_id: str | None = None
    provision_agent: bool = True


class AdminClassOwnerUpdate(BaseModel):
    owner_user_id: str | None = None


class AdminSettingUpdate(BaseModel):
    value: Any


class OpenClawGlobalUpdate(BaseModel):
    responses_enabled: bool | None = None
    dm_scope: Literal["main", "per-peer", "per-channel-peer", "per-account-channel-peer"] | None = None
    classclaw_plugin_enabled: bool | None = None


class OpenClawAgentUpdate(BaseModel):
    display_name: str | None = Field(default=None, min_length=1, max_length=100)
    model: str | None = Field(default=None, max_length=200)
    model_fallbacks: list[str] | None = Field(default=None, max_length=5)
    utility_model: str | None = Field(default=None, max_length=200)
    thinking_default: Literal["off", "minimal", "low", "medium", "high", "xhigh", "adaptive", "max"] | None = None
    reasoning_default: Literal["off", "on", "stream"] | None = None
    verbose_default: Literal["off", "on", "full"] | None = None
    fast_mode_default: bool | Literal["auto"] | None = None
    context_injection: Literal["always", "continuation-skip", "never"] | None = None
    bootstrap_max_chars: int | None = Field(default=None, ge=1000, le=100_000)
    bootstrap_total_max_chars: int | None = Field(default=None, ge=1000, le=500_000)
    max_skills_prompt_chars: int | None = Field(default=None, ge=1000, le=50_000)
    memory_search_enabled: bool | None = None
    model_params: dict[str, str | int | float | bool | None] | None = None

    @field_validator("model_params")
    @classmethod
    def validate_model_params(cls, value):
        if value is None:
            return None
        allowed = {"temperature", "topP", "maxTokens", "cacheRetention"}
        unknown = sorted(set(value) - allowed)
        if unknown:
            raise ValueError(f"不支持的模型参数：{', '.join(unknown)}")
        return value


class OpenClawWorkspaceFileUpdate(BaseModel):
    content: str = Field(max_length=100_000)
    expected_sha256: str | None = Field(default=None, min_length=64, max_length=64)


class AdminInitializeRequest(BaseModel):
    confirmation: Literal["INITIALIZE"]
