"""Signed session tokens, server-side expiry, cached lookup, revocation.

Token = `<session uuid>.<base64url HMAC-SHA256(key, uuid)>`. Checking the signature is a pure CPU
operation, so the rate limiter (Plan 12) can identify a session before touching Redis or Postgres.
The signature proves "we issued this id"; it says nothing about expiry or revocation, so every
request that needs a session also loads the session record and enforces both there. That is true
for the cookie and for the bearer header alike, because both go through `load_session`.

The signing key is derived from SESSION_SECRET with its own label (`sess:v1`); the OTP hash key is
derived with a different label, so the two uses of one secret can never be confused.
"""

import base64
import hashlib
import hmac
import json
import time
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Any
from uuid import UUID

import asyncpg

from app import db
from app.cache import Cache
from app.config import Settings
from app.errors import Unauthenticated

CACHE_TTL_S = 60
_KEY = "sess:"

# Tests move this to simulate the passage of time.
_now = time.time


@lru_cache(maxsize=8)
def _derive(secret: str, label: bytes) -> bytes:
    return hmac.new(secret.encode(), label, hashlib.sha256).digest()


def session_key(settings: Settings) -> bytes:
    return _derive(settings.session_secret.get_secret_value(), b"sess:v1")


def otp_key(settings: Settings) -> bytes:
    return _derive(settings.session_secret.get_secret_value(), b"otp:v1")


def sid_hash(session_id: UUID) -> str:
    """Identifies a session inside admission tokens without exposing the session id itself."""
    return hashlib.sha256(str(session_id).encode()).hexdigest()


@dataclass(frozen=True, slots=True)
class Session:
    session_id: UUID = field(repr=False)
    user_id: UUID
    user_public_id: str
    device_id: str | None = field(repr=False)
    expires_at: float = field(repr=False)

    @property
    def sid_hash(self) -> str:
        return sid_hash(self.session_id)


def _sign(settings: Settings, session_id: str) -> str:
    mac = hmac.new(session_key(settings), session_id.encode(), hashlib.sha256).digest()
    return base64.urlsafe_b64encode(mac).rstrip(b"=").decode()


def issue_token(settings: Settings, session_id: UUID) -> str:
    sid = str(session_id)
    return f"{sid}.{_sign(settings, sid)}"


def verify_token(settings: Settings, token: str) -> UUID | None:
    """The session id if the signature is ours, else None. Constant-time signature comparison."""
    sid, dot, signature = token.partition(".")
    if not dot or len(token) > 128:
        return None
    try:
        session_id = UUID(sid)
    except ValueError:
        return None
    if str(session_id) != sid:  # only the canonical form was ever issued
        return None
    if not hmac.compare_digest(signature.encode(), _sign(settings, sid).encode()):
        return None
    return session_id


def expiry_of(settings: Settings, created_at_epoch: float) -> float:
    return created_at_epoch + settings.session_ttl_s


def is_expired(settings: Settings, created_at_epoch: float) -> bool:
    return expiry_of(settings, created_at_epoch) <= _now()


async def _cache_get(cache: Cache, session_id: UUID) -> Session | None:
    raw = await cache.run(lambda r: r.get(_KEY + str(session_id)), fallback=lambda: None)
    if not raw:
        return None
    try:
        data: dict[str, Any] = json.loads(raw)
        return Session(
            session_id=session_id,
            user_id=UUID(data["u"]),
            user_public_id=str(data["p"]),
            device_id=data["d"],
            expires_at=float(data["x"]),
        )
    except (ValueError, KeyError, TypeError):
        return None  # damaged entry: treat as a miss and reload from Postgres


async def cache_put(cache: Cache, session: Session) -> None:
    ttl = int(min(CACHE_TTL_S, session.expires_at - _now()))
    if ttl < 1:
        return
    value = json.dumps(
        {
            "u": str(session.user_id),
            "p": session.user_public_id,
            "d": session.device_id,
            "x": session.expires_at,
        }
    )
    await cache.run(
        lambda r: r.set(_KEY + str(session.session_id), value, ex=ttl), fallback=lambda: None
    )


async def cache_drop(cache: Cache, session_ids: list[UUID]) -> None:
    if session_ids:
        keys = [_KEY + str(i) for i in session_ids]
        await cache.run(lambda r: r.delete(*keys), fallback=lambda: None)


async def load_session(
    pool: asyncpg.Pool, cache: Cache, settings: Settings, session_id: UUID
) -> Session:
    """The live session for a signed id, or UNAUTHENTICATED (revoked, expired or unknown).

    Redis first (60 s), Postgres on a miss or when Redis is down. Revoked sessions are never
    cached and `revoke_session` deletes the cache entry, so revocation is immediate."""
    cached = await _cache_get(cache, session_id)
    if cached is not None:
        if cached.expires_at <= _now():
            raise Unauthenticated()  # one message for every reason
        return cached
    async with db.acquire(pool, 1.0) as conn:
        row = await conn.fetchrow(
            "SELECT s.user_id, s.device_id, s.created_at, s.revoked_at, u.public_id"
            " FROM sessions s JOIN users u ON u.id = s.user_id WHERE s.id = $1",
            session_id,
        )
    if row is None or row["revoked_at"] is not None:
        raise Unauthenticated()  # one message for every reason
    created = row["created_at"].timestamp()
    if is_expired(settings, created):
        raise Unauthenticated()  # one message for every reason
    session = Session(
        session_id=session_id,
        user_id=row["user_id"],
        user_public_id=row["public_id"],
        device_id=row["device_id"],
        expires_at=expiry_of(settings, created),
    )
    await cache_put(cache, session)
    return session


async def revoke_session(pool: asyncpg.Pool, cache: Cache, session_id: UUID) -> bool:
    """Mark a session revoked and drop its cache entry. True if it was live."""
    async with db.acquire(pool, 1.0) as conn:
        result: str = await conn.execute(
            "UPDATE sessions SET revoked_at = now() WHERE id = $1 AND revoked_at IS NULL",
            session_id,
        )
    await cache_drop(cache, [session_id])
    return result.endswith("1")
