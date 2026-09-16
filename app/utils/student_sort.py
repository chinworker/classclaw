from __future__ import annotations

import re
import unicodedata


def student_no_key(value: str | None) -> tuple:
    """Natural ascending order, including full-width digits and long identifiers.

    Keep stored identifiers intact. Numerically equal spellings use the shorter
    spelling first, then text, so pagination has deterministic boundaries.
    """
    raw = value or ""
    text = unicodedata.normalize("NFKC", raw).strip().casefold()
    parts = tuple((0, int(part)) if part.isascii() and part.isdigit() else (1, part)
                  for part in re.split(r"([0-9]+)", text) if part)
    return (not bool(text), parts, len(text), text, raw)


def compare_student_nos(left: str, right: str) -> int:
    a, b = student_no_key(left), student_no_key(right)
    return (a > b) - (a < b)
