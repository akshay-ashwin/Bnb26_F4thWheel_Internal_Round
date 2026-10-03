"""One error envelope for the whole API (docs/contract/error-codes.md)."""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime
from typing import Any

import asyncpg
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

log = logging.getLogger("fairdrop.errors")

STATUS: dict[str, int] = {
    "RATE_LIMITED": 429,
    "OTP_THROTTLED": 429,
    "INVALID_PHONE": 400,
    "OTP_INVALID": 401,
    "OTP_EXPIRED": 410,
    "UNAUTHENTICATED": 401,
    "WINDOW_CLOSED": 403,
    "WINDOW_NOT_OPEN": 403,
    "TOKEN_INVALID": 401,
    "NOT_OFFERED": 403,
    "OFFER_EXPIRED": 409,
    "SOLD_OUT": 409,
    "STEP_UP_REQUIRED": 423,
    "IDEMPOTENCY_KEY_REUSED": 422,
    "IDEMPOTENCY_KEY_MISSING": 400,
    "INVALID_TRANSITION": 409,
    "NOT_FOUND": 404,
    "VALIDATION_ERROR": 400,
    "SERVICE_UNAVAILABLE": 503,
    "INTERNAL": 500,
}


def now_iso() -> str:
    return datetime.now(UTC).isoformat()


class AppError(Exception):
    """A contract error: `code` picks the HTTP status from STATUS."""

    def __init__(self, code: str, message: str = "", retry_after_ms: int | None = None) -> None:
        super().__init__(message or code)
        if code not in STATUS:
            raise ValueError(f"unknown error code {code}")
        self.code = code
        self.status = STATUS[code]
        self.message = message or code.replace("_", " ").capitalize()
        self.retry_after_ms = retry_after_ms


def envelope(
    code: str, message: str, retry_after_ms: int | None = None, request_id: str | None = None
) -> JSONResponse:
    error: dict[str, Any] = {"code": code, "message": message}
    if retry_after_ms is not None:
        error["retry_after_ms"] = retry_after_ms
    if request_id:
        error["request_id"] = request_id
    headers: dict[str, str] = {}
    if retry_after_ms is not None:
        headers["Retry-After"] = str(max(1, -(-retry_after_ms // 1000)))
    return JSONResponse(
        {"error": error, "server_time": now_iso()}, status_code=STATUS[code], headers=headers
    )


def _request_id(request: Request) -> str | None:
    value = request.scope.get("fairdrop.request_id")
    return value if isinstance(value, str) else None


def install_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(AppError)
    async def _app_error(request: Request, exc: AppError) -> JSONResponse:
        return envelope(exc.code, exc.message, exc.retry_after_ms)

    @app.exception_handler(RequestValidationError)
    async def _validation(request: Request, exc: RequestValidationError) -> JSONResponse:
        first = exc.errors()[0] if exc.errors() else {}
        where = ".".join(str(p) for p in first.get("loc", ()) if p != "body")
        return envelope("VALIDATION_ERROR", f"Invalid request: {where}".strip(": "))

    @app.exception_handler(StarletteHTTPException)
    async def _http(request: Request, exc: StarletteHTTPException) -> JSONResponse:
        if exc.status_code == 404:
            return envelope("NOT_FOUND", "Not found")
        if exc.status_code == 405:
            return envelope("VALIDATION_ERROR", "Method not allowed")
        return envelope("INTERNAL", str(exc.detail), request_id=_request_id(request))

    @app.exception_handler(asyncpg.PostgresConnectionError)
    @app.exception_handler(asyncpg.TooManyConnectionsError)
    @app.exception_handler(asyncpg.QueryCanceledError)
    @app.exception_handler(asyncpg.LockNotAvailableError)
    @app.exception_handler(asyncio.TimeoutError)
    @app.exception_handler(OSError)
    async def _unavailable(request: Request, exc: Exception) -> JSONResponse:
        log.warning("database unavailable: %s", type(exc).__name__)
        return envelope("SERVICE_UNAVAILABLE", "Temporarily unavailable, retry", 1000)

    @app.exception_handler(Exception)
    async def _internal(request: Request, exc: Exception) -> JSONResponse:
        log.exception("unhandled error")
        return envelope("INTERNAL", "Internal error", request_id=_request_id(request))
