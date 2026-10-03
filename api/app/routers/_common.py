"""Helper to document error responses per route in the OpenAPI contract."""

from typing import Any

from app.errors import ErrorCode
from app.schemas.errors import ErrorResponse

STATUS: dict[ErrorCode, int] = {
    ErrorCode.RATE_LIMITED: 429,
    ErrorCode.OTP_THROTTLED: 429,
    ErrorCode.INVALID_PHONE: 400,
    ErrorCode.OTP_INVALID: 401,
    ErrorCode.OTP_EXPIRED: 410,
    ErrorCode.UNAUTHENTICATED: 401,
    ErrorCode.WINDOW_CLOSED: 403,
    ErrorCode.WINDOW_NOT_OPEN: 403,
    ErrorCode.TOKEN_INVALID: 401,
    ErrorCode.NOT_OFFERED: 403,
    ErrorCode.OFFER_EXPIRED: 409,
    ErrorCode.SOLD_OUT: 409,
    ErrorCode.STEP_UP_REQUIRED: 423,
    ErrorCode.IDEMPOTENCY_KEY_REUSED: 422,
    ErrorCode.IDEMPOTENCY_KEY_MISSING: 400,
    ErrorCode.INVALID_TRANSITION: 409,
    ErrorCode.NOT_FOUND: 404,
    ErrorCode.VALIDATION_ERROR: 400,
    ErrorCode.SERVICE_UNAVAILABLE: 503,
    ErrorCode.INTERNAL: 500,
}


def errs(*codes: ErrorCode) -> dict[int | str, dict[str, Any]]:
    """`responses=` for a route: the listed codes plus the ones every /api route can return."""
    wanted = [*codes, ErrorCode.VALIDATION_ERROR, ErrorCode.SERVICE_UNAVAILABLE, ErrorCode.INTERNAL]
    by_status: dict[int, list[str]] = {}
    for code in wanted:
        names = by_status.setdefault(STATUS[code], [])
        if code.value not in names:
            names.append(code.value)
    return {
        status: {"model": ErrorResponse, "description": " | ".join(names)}
        for status, names in sorted(by_status.items())
    }
