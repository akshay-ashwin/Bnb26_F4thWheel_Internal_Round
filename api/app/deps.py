"""Dependency providers: pool, cache, settings, admin/telemetry auth, session, idempotency key."""

import hmac
from typing import Annotated

import asyncpg
from fastapi import Depends, Header, Request
from fastapi.security import APIKeyCookie, APIKeyHeader, HTTPAuthorizationCredentials, HTTPBearer

from app.cache import Cache
from app.config import Settings, get_settings
from app.errors import IdempotencyKeyMissing, Unauthenticated

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
