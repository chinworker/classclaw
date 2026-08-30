from __future__ import annotations

from typing import Any


class AppError(Exception):
    def __init__(self, code: str, message: str, status_code: int = 400, details: dict[str, Any] | None = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code
        self.details = details or {}


def not_found(entity: str, entity_id: str) -> AppError:
    return AppError("NOT_FOUND", f"{entity}不存在", 404, {"id": entity_id})

