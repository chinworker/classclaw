from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date, timedelta
from typing import Literal

from lunar_python import Lunar

from app.utils.time import today

SemesterSeason = Literal["spring", "autumn"]


@dataclass(frozen=True, slots=True)
class SemesterPeriod:
    semester_name: str
    semester_start: date
    semester_end: date
    season: SemesterSeason

    def as_json(self) -> dict[str, str]:
        value = asdict(self)
        value["semester_start"] = self.semester_start.isoformat()
        value["semester_end"] = self.semester_end.isoformat()
        return value


def _solar_date(lunar_year: int, lunar_month: int, lunar_day: int) -> date:
    """Convert a Chinese lunar date to a Gregorian date without any online lookup."""
    solar = Lunar.fromYmd(lunar_year, lunar_month, lunar_day).getSolar()
    return date(solar.getYear(), solar.getMonth(), solar.getDay())


def semester_period(year: int, season: SemesterSeason) -> SemesterPeriod:
    if season == "spring":
        return SemesterPeriod(
            semester_name=f"{year} 春季学期",
            semester_start=_solar_date(year, 1, 15),
            semester_end=date(year, 7, 1),
            season=season,
        )

    if season == "autumn":
        return SemesterPeriod(
            semester_name=f"{year} 秋季学期",
            semester_start=date(year, 9, 1),
            semester_end=_solar_date(year + 1, 1, 1) - timedelta(days=7),
            season=season,
        )

    raise ValueError(f"不支持的学期类型：{season}")


def current_or_next_semester(as_of: date | None = None) -> SemesterPeriod:
    """Return the active semester, or the nearest semester that has not started."""
    reference = as_of or today()
    candidates = [
        semester_period(reference.year - 1, "autumn"),
        semester_period(reference.year, "spring"),
        semester_period(reference.year, "autumn"),
        semester_period(reference.year + 1, "spring"),
        semester_period(reference.year + 1, "autumn"),
    ]
    active = [item for item in candidates if item.semester_start <= reference <= item.semester_end]
    if active:
        return min(active, key=lambda item: item.semester_start)
    return min((item for item in candidates if item.semester_start >= reference), key=lambda item: item.semester_start)
