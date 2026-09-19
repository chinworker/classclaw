from __future__ import annotations

import unicodedata
from datetime import date
from typing import Annotated, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, field_validator, model_validator

Name = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=60)]
ClockTime = Annotated[str, StringConstraints(pattern=r"^(?:[01]\d|2[0-3]):[0-5]\d$")]


def normalized_name(value: str) -> str:
    return "".join(unicodedata.normalize("NFKC", value).casefold().split())


class MemoryEntry(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    memory_id: str | None = Field(default=None, max_length=36)
    kind: Literal["schedule", "preference", "fact"]
    name: Name
    aliases: list[Name] = Field(default_factory=list, max_length=5)
    content: str = Field(default="", max_length=400)
    start_time: ClockTime | None = None
    end_time: ClockTime | None = None
    weekdays: list[Annotated[int, Field(strict=True, ge=1, le=7)]] = Field(default_factory=list, max_length=7)
    valid_from: date | None = None
    valid_to: date | None = None

    @field_validator("weekdays")
    @classmethod
    def ordered_days(cls, value: list[int]) -> list[int]:
        return sorted(set(value))

    @model_validator(mode="after")
    def validate_rule(self) -> Self:
        names = {normalized_name(self.name)}
        aliases = []
        for alias in self.aliases:
            key = normalized_name(alias)
            if key not in names:
                aliases.append(alias)
                names.add(key)
        self.aliases = sorted(aliases, key=normalized_name)
        if (self.valid_from is None) != (self.valid_to is None):
            raise ValueError("临时记忆必须同时提供生效和结束日期")
        if self.valid_from and self.valid_to < self.valid_from:
            raise ValueError("结束日期不能早于生效日期")
        if self.kind == "schedule":
            if not self.start_time or not self.end_time or not self.weekdays:
                raise ValueError("作息必须明确起止时间及适用星期（1至7）")
            if self.start_time >= self.end_time:
                raise ValueError("作息结束时间必须晚于开始时间；跨午夜请拆成明确的时段")
        elif not self.content or self.start_time or self.end_time or self.weekdays:
            raise ValueError("偏好和约定必须有内容，不能含作息时间或星期")
        return self


class MemoryUpsert(BaseModel):
    model_config = ConfigDict(extra="forbid")
    class_id: str
    entries: list[MemoryEntry] = Field(min_length=1, max_length=50)


class MemoryForget(BaseModel):
    model_config = ConfigDict(extra="forbid")
    class_id: str
    memory_ids: list[str] = Field(min_length=1, max_length=100)
