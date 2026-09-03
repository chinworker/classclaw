from __future__ import annotations

from datetime import date

import pytest

from app.services.semester_calendar import current_or_next_semester, semester_period


def test_spring_semester_uses_lantern_festival_and_july_first():
    semester = semester_period(2026, "spring")

    assert semester.semester_name == "2026 春季学期"
    assert semester.semester_start == date(2026, 3, 3)
    assert semester.semester_end == date(2026, 7, 1)


def test_autumn_semester_ends_one_week_before_next_chinese_new_year():
    semester = semester_period(2026, "autumn")

    assert semester.semester_name == "2026 秋季学期"
    assert semester.semester_start == date(2026, 9, 1)
    assert semester.semester_end == date(2027, 1, 30)


@pytest.mark.parametrize(
    ("as_of", "expected_name", "expected_start"),
    [
        (date(2026, 1, 1), "2025 秋季学期", date(2025, 9, 1)),
        (date(2026, 2, 11), "2026 春季学期", date(2026, 3, 3)),
        (date(2026, 3, 3), "2026 春季学期", date(2026, 3, 3)),
        (date(2026, 7, 1), "2026 春季学期", date(2026, 3, 3)),
        (date(2026, 7, 2), "2026 秋季学期", date(2026, 9, 1)),
        (date(2026, 9, 1), "2026 秋季学期", date(2026, 9, 1)),
        (date(2026, 9, 2), "2026 秋季学期", date(2026, 9, 1)),
        (date(2027, 1, 30), "2026 秋季学期", date(2026, 9, 1)),
        (date(2027, 1, 31), "2027 春季学期", date(2027, 2, 20)),
    ],
)
def test_current_or_next_semester_prefers_active_term_then_nearest_future_term(as_of, expected_name, expected_start):
    semester = current_or_next_semester(as_of)

    assert semester.semester_name == expected_name
    assert semester.semester_start == expected_start


def test_semester_json_uses_html_date_input_format():
    assert current_or_next_semester(date(2026, 9, 2)).as_json() == {
        "semester_name": "2026 秋季学期",
        "semester_start": "2026-09-01",
        "semester_end": "2027-01-30",
        "season": "autumn",
    }
