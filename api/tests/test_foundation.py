"""Chunk 1: settings, pools, startup/shutdown, health, readiness and the error envelope."""

from __future__ import annotations

import asyncio

import httpx
import pytest
import redis.asyncio as aioredis
from fastapi import FastAPI

from app.config import Settings
from app.errors import AppError
from app.main import create_app


async def test_health_is_alive_and_has_server_time(client: httpx.AsyncClient) -> None:
    r = await client.get("/api/healthz")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok" and "server_time" in body
    assert "x-request-id" in r.headers


async def test_ready_checks_postgres_and_redis(client: httpx.AsyncClient) -> None:
    r = await client.get("/api/readyz")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"
    assert r.json()["postgres"] is True and r.json()["redis"] is True


async def test_pool_connects_as_the_restricted_role(app: FastAPI) -> None:
    async with app.state.pool.acquire() as conn:
        assert await conn.fetchval("SELECT current_user") == "fairdrop_app"
        row = await conn.fetchrow("SELECT rolsuper FROM pg_roles WHERE rolname = current_user")
        assert row["rolsuper"] is False
        assert await conn.fetchval("SHOW statement_timeout") == "5s"


async def test_unknown_route_uses_the_envelope(client: httpx.AsyncClient) -> None:
    r = await client.get("/api/nope")
    assert r.status_code == 404
    body = r.json()
    assert body["error"]["code"] == "NOT_FOUND" and "server_time" in body


async def test_app_error_envelope_and_retry_after_header(settings: Settings) -> None:
    application = create_app(settings)

    @application.get("/api/_boom")
    async def boom() -> None:
        raise AppError("RATE_LIMITED", "Slow down", retry_after_ms=2500)

    @application.get("/api/_crash")
    async def crash() -> None:
        raise RuntimeError("secret detail must not leak")

    transport = httpx.ASGITransport(app=application, raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as c:
        r = await c.get("/api/_boom")
        assert r.status_code == 429
        assert r.json()["error"] == {
            "code": "RATE_LIMITED",
            "message": "Slow down",
            "retry_after_ms": 2500,
        }
        assert r.headers["retry-after"] == "3"
        failed = await c.get("/api/_crash")
        assert failed.status_code == 500
        assert failed.json()["error"]["code"] == "INTERNAL"
        assert "secret detail" not in failed.text
        assert "request_id" in failed.json()["error"]


async def test_validation_errors_use_the_envelope(client: httpx.AsyncClient) -> None:
    r = await client.post("/api/auth/otp/request", json={"phone": "x"})
    # the auth router arrives in chunk 2; until then this is a 404 in the same envelope
    assert r.status_code in (400, 404)
    assert r.json()["error"]["code"] in ("VALIDATION_ERROR", "NOT_FOUND")


async def test_startup_and_shutdown_release_resources(settings: Settings) -> None:
    application = create_app(settings)
    async with application.router.lifespan_context(application):
        assert application.state.pool is not None
        pool = application.state.pool
    assert pool.is_closing() or pool._closed  # noqa: SLF001 - asyncpg exposes no public flag


def test_settings_reject_short_secrets_and_prod_with_sim_mode() -> None:
    base = {
        "DATABASE_URL": "postgresql://x",
        "REDIS_URL": "redis://x",
        "PHONE_PEPPER": "p" * 32,
        "SESSION_SECRET": "s" * 32,
        "TOKEN_SIGNING_KEY": "t" * 32,
        "ADMIN_KEY": "a" * 32,
    }
    assert Settings.from_env(base).app_env == "dev"
    with pytest.raises(ValueError, match="at least 32 bytes"):
        Settings.from_env({**base, "ADMIN_KEY": "short"})
    with pytest.raises(ValueError, match="SIM_MODE"):
        Settings.from_env({**base, "APP_ENV": "prod", "SIM_MODE": "true"})
    with pytest.raises(ValueError, match="missing"):
        Settings.from_env({k: v for k, v in base.items() if k != "REDIS_URL"})


async def test_redis_breaker_fails_fast(settings: Settings) -> None:
    from app.cache import Cache, RedisUnavailableError

    dead = Cache(
        Settings.from_env(
            {
                **__import__("os").environ,
                "REDIS_URL": "redis://127.0.0.1:1/0",
                "DATABASE_URL": settings.database_url,
            }
        )
    )

    async def ping(r: aioredis.Redis) -> bool:
        return bool(await r.ping())

    for _ in range(3):
        with pytest.raises(RedisUnavailableError):
            await dead.run(ping)
    assert dead.available is False
    start = asyncio.get_event_loop().time()
    with pytest.raises(RedisUnavailableError):
        await dead.run(ping)
    assert asyncio.get_event_loop().time() - start < 0.05  # open breaker: no socket wait
    await dead.close()
