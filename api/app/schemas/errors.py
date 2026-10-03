from typing import Any

from pydantic import BaseModel

from app.errors import ErrorCode


class ErrorBody(BaseModel):
    code: ErrorCode
    message: str
    retry_after_ms: int | None = None
    details: dict[str, Any] | None = None


class ErrorResponse(BaseModel):
    """The single error envelope. Every non-2xx response has this shape."""

    error: ErrorBody
    server_time: str
