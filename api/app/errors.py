"""One error envelope, one exception hierarchy, global handlers.

Envelope: {"error": {"code", "message", "retry_after_ms"?, "details"?}, "server_time"}.
Handlers build JSONResponse directly (not through a response_model), so the envelope is the
same for application errors, validation errors, unknown paths, database trouble and crashes.
"""

import asyncio
import math
from enum import StrEnum
from typing import Any, cast

import asyncpg
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.clock import server_time


class ErrorCode(StrEnum):
    RATE_LIMITED = "RATE_LIMITED"
    OTP_THROTTLED = "OTP_THROTTLED"
    INVALID_PHONE = "INVALID_PHONE"
    OTP_INVALID = "OTP_INVALID"
    OTP_EXPIRED = "OTP_EXPIRED"
    UNAUTHENTICATED = "UNAUTHENTICATED"
    WINDOW_CLOSED = "WINDOW_CLOSED"
    WINDOW_NOT_OPEN = "WINDOW_NOT_OPEN"
    TOKEN_INVALID = "TOKEN_INVALID"  # noqa: S105 (error code name)
    NOT_OFFERED = "NOT_OFFERED"
    OFFER_EXPIRED = "OFFER_EXPIRED"
    SOLD_OUT = "SOLD_OUT"
    STEP_UP_REQUIRED = "STEP_UP_REQUIRED"
    IDEMPOTENCY_KEY_REUSED = "IDEMPOTENCY_KEY_REUSED"
    IDEMPOTENCY_KEY_MISSING = "IDEMPOTENCY_KEY_MISSING"
    INVALID_TRANSITION = "INVALID_TRANSITION"
    NOT_FOUND = "NOT_FOUND"
    VALIDATION_ERROR = "VALIDATION_ERROR"
    SERVICE_UNAVAILABLE = "SERVICE_UNAVAILABLE"
    INTERNAL = "INTERNAL"


class AppError(Exception):
    code: str = ErrorCode.INTERNAL
    status: int = 500
    default_message: str = "Unexpected error"

    def __init__(
        self,
        message: str | None = None,
        *,
        retry_after_ms: int | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        self.message = message or self.default_message
        self.retry_after_ms = retry_after_ms
        self.details = details
        super().__init__(self.message)


def _error(name: str, code: ErrorCode, status: int, message: str) -> type[AppError]:
    return type(name, (AppError,), {"code": code, "status": status, "default_message": message})


# One subclass per code in docs/contract/error-codes.md (statuses from that table).
RateLimited = _error("RateLimited", ErrorCode.RATE_LIMITED, 429, "Slow down")
OtpThrottled = _error("OtpThrottled", ErrorCode.OTP_THROTTLED, 429, "Too many code requests")
InvalidPhone = _error("InvalidPhone", ErrorCode.INVALID_PHONE, 400, "Invalid phone number")
OtpInvalid = _error("OtpInvalid", ErrorCode.OTP_INVALID, 401, "Wrong code")
OtpExpired = _error("OtpExpired", ErrorCode.OTP_EXPIRED, 410, "Code expired")
Unauthenticated = _error("Unauthenticated", ErrorCode.UNAUTHENTICATED, 401, "Not signed in")
WindowClosed = _error("WindowClosed", ErrorCode.WINDOW_CLOSED, 403, "Registration is closed")
WindowNotOpen = _error("WindowNotOpen", ErrorCode.WINDOW_NOT_OPEN, 403, "Registration not open yet")
TokenInvalid = _error("TokenInvalid", ErrorCode.TOKEN_INVALID, 401, "Admission token rejected")
NotOffered = _error("NotOffered", ErrorCode.NOT_OFFERED, 403, "No offer for this entry")
OfferExpired = _error("OfferExpired", ErrorCode.OFFER_EXPIRED, 409, "The offer has expired")
SoldOut = _error("SoldOut", ErrorCode.SOLD_OUT, 409, "All seats are taken")
StepUpRequired = _error("StepUpRequired", ErrorCode.STEP_UP_REQUIRED, 423, "Step-up required")
IdempotencyKeyReused = _error(
    "IdempotencyKeyReused", ErrorCode.IDEMPOTENCY_KEY_REUSED, 422, "Idempotency key reused"
)
IdempotencyKeyMissing = _error(
    "IdempotencyKeyMissing", ErrorCode.IDEMPOTENCY_KEY_MISSING, 400, "Idempotency-Key required"
)
InvalidTransition = _error(
    "InvalidTransition", ErrorCode.INVALID_TRANSITION, 409, "Not allowed in this phase"
)
NotFound = _error("NotFound", ErrorCode.NOT_FOUND, 404, "Not found")
ValidationFailed = _error(
    "ValidationFailed", ErrorCode.VALIDATION_ERROR, 400, "The request is not valid"
)
ServiceUnavailable = _error(
    "ServiceUnavailable", ErrorCode.SERVICE_UNAVAILABLE, 503, "Temporarily unavailable"
)
Internal = _error("Internal", ErrorCode.INTERNAL, 500, "Unexpected error")


class NotImplementedYet(AppError):
    """Dev-only 501 for stubs. Not in ErrorCode, so it never appears in the OpenAPI contract."""

    code = "NOT_IMPLEMENTED"
    status = 501
    default_message = "Not implemented yet"


def envelope(
    code: str,
    message: str,
    *,
    retry_after_ms: int | None = None,
    details: dict[str, Any] | None = None,
) -> dict[str, Any]:
    error: dict[str, Any] = {"code": code, "message": message}
    if retry_after_ms is not None:
        error["retry_after_ms"] = retry_after_ms
    if details:
        error["details"] = details
    return {"error": error, "server_time": server_time()}


def error_response(
    status: int,
    code: str,
    message: str,
    *,
    retry_after_ms: int | None = None,
    details: dict[str, Any] | None = None,
) -> JSONResponse:
    headers: dict[str, str] = {}
    if retry_after_ms is not None:
        headers["Retry-After"] = str(max(1, math.ceil(retry_after_ms / 1000)))
    return JSONResponse(
        envelope(code, message, retry_after_ms=retry_after_ms, details=details),
        status_code=status,
        headers=headers,
    )


def internal_response(request_id: str) -> JSONResponse:
    return error_response(500, ErrorCode.INTERNAL, f"Unexpected error (request id {request_id})")


def _app_error(_: Request, exc: Exception) -> JSONResponse:
    exc = cast(AppError, exc)
    return error_response(
        exc.status, exc.code, exc.message, retry_after_ms=exc.retry_after_ms, details=exc.details
    )


def _validation_error(_: Request, exc: Exception) -> JSONResponse:
    exc = cast(RequestValidationError, exc)
    # Field paths and messages only. Pydantic's "input" is dropped: it could be a phone or an OTP.
    fields = [
        {"field": ".".join(str(p) for p in e["loc"]), "message": str(e["msg"])}
        for e in exc.errors()
    ]
    return error_response(
        400, ErrorCode.VALIDATION_ERROR, "The request is not valid", details={"fields": fields}
    )


def _http_error(_: Request, exc: Exception) -> JSONResponse:
    exc = cast(StarletteHTTPException, exc)
    if exc.status_code == 404:
        return error_response(404, ErrorCode.NOT_FOUND, "Not found")
    if exc.status_code == 405:
        return error_response(405, ErrorCode.VALIDATION_ERROR, "Method not allowed")
    return error_response(exc.status_code, ErrorCode.INTERNAL, "Request failed")


def _unavailable(_: Request, exc: Exception) -> JSONResponse:
    # Postgres down, pool exhausted, lock or statement timeout: nothing was written.
    return error_response(
        503, ErrorCode.SERVICE_UNAVAILABLE, "Temporarily unavailable", retry_after_ms=1000
    )


# Exceptions that mean "the database is slow or gone". asyncpg's QueryCanceledError covers
# statement_timeout, LockNotAvailableError covers lock_timeout.
DB_UNAVAILABLE: tuple[type[Exception], ...] = (
    asyncpg.PostgresConnectionError,
    asyncpg.CannotConnectNowError,
    asyncpg.InterfaceError,
    asyncpg.QueryCanceledError,
    asyncpg.LockNotAvailableError,
    asyncpg.TooManyConnectionsError,
    asyncio.TimeoutError,
    ConnectionError,
)


def install_handlers(app: FastAPI) -> None:
    app.add_exception_handler(AppError, _app_error)
    app.add_exception_handler(RequestValidationError, _validation_error)
    app.add_exception_handler(StarletteHTTPException, _http_error)
    for exc_type in DB_UNAVAILABLE:
        app.add_exception_handler(exc_type, _unavailable)
    # Bare Exception is handled by the ASGI middleware (it knows the request id).
