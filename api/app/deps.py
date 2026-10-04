"""Dependency providers: pool, cache, settings, admin/telemetry auth, session, idempotency key."""

import hmac
import uuid
from dataclasses import dataclass
from typing import Annotated

import asyncpg
from fastapi import Depends, Header, Request
from fastapi.security import APIKeyCookie, APIKeyHeader, HTTPAuthorizationCredentials, HTTPBearer

from app import netutil
from app.cache import Cache
from app.config import Settings, get_settings
from app.errors import IdempotencyKeyMissing, Unauthenticated, ValidationFailed
from app.security import sid_hash, verify_session_token

SettingsDep = Annotated[Settings, Depends(get_settings)]


def get_pool(request: Request) -> asyncpg.Pool:
    pool: asyncpg.Pool = request.app.state.pool
    return pool


def get_cache(request: Request) -> Cache:
    cache: Cache = request.app.state.cache
    return cache


PoolDep = Annotated[asyncpg.Pool, Depends(get_pool)]
CacheDep = Annotated[Cache, Depends(get_cache)]

_admin_header = APIKeyHeader(name="X-Admin-Key", auto_error=False, scheme_name="AdminKey")
_sim_header = APIKeyHeader(name="X-Sim-Key", auto_error=False, scheme_name="SimKey")
_cookie = APIKeyCookie(name="fd_session", auto_error=False, scheme_name="SessionCookie")
_bearer = HTTPBearer(auto_error=False, scheme_name="SessionBearer")


def _matches(supplied: str | None, expected: str) -> bool:
    if not supplied or not expected:
        return False
    return hmac.compare_digest(supplied.encode(), expected.encode())


async def require_admin(
    settings: SettingsDep, key: Annotated[str | None, Depends(_admin_header)]
) -> None:
    if not _matches(key, settings.admin_key.get_secret_value()):
        raise Unauthenticated("Bad or missing admin key")


async def require_sim_key(
    settings: SettingsDep, key: Annotated[str | None, Depends(_sim_header)]
) -> None:
    if not _matches(key, settings.sim_telemetry_key.get_secret_value()):
        raise Unauthenticated("Bad or missing telemetry key")


async def session_credential(
    cookie: Annotated[str | None, Depends(_cookie)],
    bearer: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
) -> None:
    """Declares the session auth schemes in the contract. Plan 04 turns this into a real lookup
    (it will return the session and raise UNAUTHENTICATED); until then it enforces nothing."""


async def require_idempotency_key(
    key: Annotated[str | None, Header(alias="Idempotency-Key", max_length=128)] = None,
) -> str:
    if not key:
        raise IdempotencyKeyMissing()
    return key


IdempotencyKey = Annotated[str, Depends(require_idempotency_key)]


def settings_of(request: Request) -> Settings:
    """The settings this app instance was built with (tests build apps with their own)."""
    settings: Settings = request.app.state.settings
    return settings


def pool_of(request: Request) -> asyncpg.Pool:
    return get_pool(request)


@dataclass(frozen=True)
class Session:
    session_id: uuid.UUID
    sid_hash: str
    user_id: uuid.UUID
    user_public_id: str
    device_id: str | None


async def current_session(
    request: Request,
    cookie: Annotated[str | None, Depends(_cookie)],
    bearer: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
) -> Session:
    """Authenticate by session identity only: the `fd_session` cookie first, else bearer.

    The signature is checked without a database call; then the row must exist, be unrevoked and
    unexpired. IP, device and user agent are NOT part of validity (phones change networks).
    Depending on the cookie/bearer schemes here is what declares them in the OpenAPI contract.
    """
    settings = settings_of(request)
    token = cookie or (bearer.credentials if bearer else None)
    session_id = verify_session_token(settings.session_secret.get_secret_value(), token or "")
    if session_id is None:
        raise Unauthenticated("Sign in again")
    row = await get_pool(request).fetchrow(
        "SELECT s.id, s.user_id, s.device_id, u.public_id FROM sessions s"
        " JOIN users u ON u.id = s.user_id"
        " WHERE s.id = $1 AND s.revoked_at IS NULL"
        "   AND s.created_at > now() - make_interval(secs => $2)",
        session_id,
        float(settings.session_ttl_s),
    )
    if row is None:
        raise Unauthenticated("Sign in again")
    request.scope.setdefault("state", {})["user_public_id"] = row["public_id"]
    return Session(
        session_id=row["id"],
        sid_hash=sid_hash(row["id"]),
        user_id=row["user_id"],
        user_public_id=row["public_id"],
        device_id=row["device_id"],
    )


SessionDep = Annotated[Session, Depends(current_session)]


def parse_idempotency_uuid(raw: str) -> uuid.UUID:
    try:
        return uuid.UUID(raw)
    except ValueError:
        raise ValidationFailed("Idempotency-Key must be a UUID") from None


async def require_idempotency_uuid(key: IdempotencyKey) -> uuid.UUID:
    return parse_idempotency_uuid(key)


IdempotencyUuid = Annotated[uuid.UUID, Depends(require_idempotency_uuid)]


def optional_idempotency_uuid(request: Request) -> uuid.UUID | None:
    """Entries accept an Idempotency-Key but the contract does not require it: read it without
    declaring it, so the frozen OpenAPI spec is unchanged."""
    raw = request.headers.get("idempotency-key")
    return parse_idempotency_uuid(raw) if raw else None


def client_ip(request: Request) -> str:
    return netutil.client_ip(request, settings_of(request))
