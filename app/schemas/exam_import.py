from __future__ import annotations

from datetime import date
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, FiniteFloat, PositiveInt, StringConstraints

Label = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)]
FullScore = Annotated[FiniteFloat, Field(gt=0)]


class RankColumns(BaseModel):
    model_config = ConfigDict(extra="forbid")

    score: PositiveInt | None = None
    class_rank: PositiveInt | None = None
    grade_rank: PositiveInt | None = None


class SubjectColumns(RankColumns):
    subject: Label
    score: PositiveInt
    full_score: FullScore | None = None


class ExamLayout(BaseModel):
    model_config = ConfigDict(extra="forbid")

    table: PositiveInt
    name: Label | None = None
    exam_date: date | None = None
    name_column: PositiveInt | None = None
    student_no_column: PositiveInt | None = None
    class_column: PositiveInt | None = None
    exam_column: PositiveInt | None = None
    date_column: PositiveInt | None = None
    subjects: list[SubjectColumns] = Field(min_length=1, max_length=30)
    total: RankColumns | None = None


class ExamLayouts(BaseModel):
    datasets: list[ExamLayout] = Field(default_factory=list, max_length=24)


class ExamImportSelection(BaseModel):
    model_config = ConfigDict(extra="forbid")

    dataset_id: PositiveInt
    name: Label
    exam_date: date
    full_scores: dict[Label, FullScore] = Field(min_length=1, max_length=30)


class ExamImportApply(BaseModel):
    model_config = ConfigDict(extra="forbid")

    preview_id: str = Field(min_length=32, max_length=64)
    datasets: list[ExamImportSelection] = Field(min_length=1, max_length=24)
