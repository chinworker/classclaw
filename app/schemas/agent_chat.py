from __future__ import annotations

from pydantic import BaseModel, Field, field_validator


class ClassAgentModelUpdate(BaseModel):
    main_model: str | None = Field(default=None, max_length=200)
    image_model: str | None = Field(default=None, max_length=200)
    speech_model: str | None = Field(default=None, max_length=200)

    @field_validator("main_model", "image_model", "speech_model")
    @classmethod
    def validate_model_reference(cls, value: str | None) -> str | None:
        if value is None or not value.strip():
            return None
        value = value.strip()
        provider, separator, model = value.partition("/")
        if not separator or not provider or not model or any(character.isspace() for character in value):
            raise ValueError("模型必须使用 provider/model 格式")
        return value
