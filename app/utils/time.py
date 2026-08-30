from __future__ import annotations

from datetime import date, datetime
from zoneinfo import ZoneInfo

from app.config import settings


def now() -> datetime:
    return datetime.now(ZoneInfo(settings.timezone))


def today() -> date:
    return now().date()

