"""Reusable request dependencies: session auth, admin auth, idempotency key, client network."""

from __future__ import annotations

import hmac
import uuid
from dataclasses import dataclass

import asyncpg
from fastapi import Request

from app import netutil
from app.config import Settings
from app.errors import AppError
from app.security import sid_hash, verify_session_token

COOKIE_NAME = "fd_session"


@dataclass(frozen=True)
class Session:
    session_id: uuid.UUID
    sid_hash: str
    user_id: uuid.UUID
    user_public_id: str
    device_id: str | None


def settings_of(request: Request) -> Settings:
    settings: Settings = request.app.state.settings
    return settings


def pool_of(request: Request) -> asyncpg.Pool:
    pool: asyncpg.Pool = request.app.state.pool
    return pool


def session_token_from(request: Request) -> str | None:
    """The request's session token: the `fd_session` cookie first, else `Authorization: Bearer`."""
    cookie = request.cookies.get(COOKIE_NAME)
    if cookie:
        return cookie
    header = request.headers.get("authorization", "")
    scheme, _, value = header.partition(" ")
    if scheme.lower() == "bearer" and value.strip():
        return value.strip()
    return None


async def current_session(request: Request) -> Session:
    """Authenticate by session identity only: cookie first, else bearer. The signature is checked
    without a database call; then the row must exist, be unrevoked and unexpired. IP, device and
    user-agent are NOT part of validity (phones change networks all the time)."""
    settings = settings_of(request)
    token = session_token_from(request)
    session_id = verify_session_token(settings.session_secret, token) if token else None
    if session_id is None:
        raise AppError("UNAUTHENTICATED", "Sign in again")
    row = await pool_of(request).fetchrow(
        "SELECT s.id, s.user_id, s.device_id, u.public_id FROM sessions s"
        " JOIN users u ON u.id = s.user_id"
        " WHERE s.id = $1 AND s.revoked_at IS NULL"
        "   AND s.created_at > now() - make_interval(secs => $2)",
        session_id,
        float(settings.session_ttl_s),
    )
    if row is None:
        raise AppError("UNAUTHENTICATED", "Sign in again")
    return Session(
        session_id=row["id"],
        sid_hash=sid_hash(row["id"]),
        user_id=row["user_id"],
        user_public_id=row["public_id"],
        device_id=row["device_id"],
    )


async def require_admin(request: Request) -> None:
    settings = settings_of(request)
    given = request.headers.get("x-admin-key", "")
    if not hmac.compare_digest(given.encode(), settings.admin_key.encode()):
        raise AppError("UNAUTHENTICATED", "Admin key required")


def parse_idempotency_key(request: Request, *, required: bool) -> uuid.UUID | None:
    raw = request.headers.get("idempotency-key")
    if raw is None or raw == "":
        if required:
            raise AppError("IDEMPOTENCY_KEY_MISSING", "Idempotency-Key header is required")
        return None
    try:
        return uuid.UUID(raw)
    except ValueError:
        raise AppError("VALIDATION_ERROR", "Idempotency-Key must be a UUID") from None


def require_idempotency_key(request: Request) -> uuid.UUID:
    key = parse_idempotency_key(request, required=True)
    if key is None:  # unreachable: required=True raises when the header is missing
        raise AppError("IDEMPOTENCY_KEY_MISSING", "Idempotency-Key header is required")
    return key


def client_ip(request: Request) -> str:
    return netutil.client_ip(request, settings_of(request))
