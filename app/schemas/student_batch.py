from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.schemas.domain import StudentUpdate


class StudentBatchUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    class_id: str
    student_ids: list[str] = Field(min_length=1, max_length=100)
    changes: StudentUpdate
    only_if_empty: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_batch(self) -> StudentBatchUpdate:
        fields = self.changes.model_fields_set
        if not fields:
            raise ValueError("批量修改必须指定变更字段")
        if len(set(self.student_ids)) != len(self.student_ids):
            raise ValueError("批量修改不能包含重复学生")
        if set(self.only_if_empty) - fields:
            raise ValueError("仅补空值的字段必须包含在变更字段中")
        return self
