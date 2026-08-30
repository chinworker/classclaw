from __future__ import annotations

from typing import Any

from fastapi import Request
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse


def ok(request: Request, data: Any = None, message: str = "操作成功", status_code: int = 200) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content={
            "success": True,
            "data": jsonable_encoder(data),
            "message": message,
            "request_id": request.state.request_id,
        },
    )

