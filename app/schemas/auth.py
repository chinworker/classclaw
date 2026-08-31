from __future__ import annotations

from pydantic import BaseModel, Field, field_validator


class LoginRequest(BaseModel):
    username: str = Field(min_length=1, max_length=100)
    password: str = Field(min_length=1, max_length=128)


class UserCreate(BaseModel):
    username: str = Field(min_length=2, max_length=100)
    display_name: str | None = Field(default=None, max_length=100)
    password: str = Field(default="32767", min_length=5, max_length=128)

    @field_validator("username")
    @classmethod
    def username_has_no_spaces(cls, value: str) -> str:
        if any(character.isspace() for character in value):
            raise ValueError("用户名不能包含空格")
        return value


class UserUpdate(BaseModel):
    username: str | None = Field(default=None, min_length=2, max_length=100)
    display_name: str | None = Field(default=None, max_length=100)
    is_active: bool | None = None

    @field_validator("username")
    @classmethod
    def updated_username_has_no_spaces(cls, value: str | None) -> str | None:
        if value is not None and any(character.isspace() for character in value):
            raise ValueError("用户名不能包含空格")
        return value


class PasswordChange(BaseModel):
    current_password: str = Field(min_length=1, max_length=128)
    new_password: str = Field(min_length=8, max_length=128)
