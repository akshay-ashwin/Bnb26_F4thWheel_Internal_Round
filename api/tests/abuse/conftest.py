"""Shared fixtures: a real Redis (database 15, flushed per test) and a tiny app behind the
abuse middleware whose handler counts how often it ran (the stand-in for Postgres work)."""

from __future__ import annotations

import hashlib
import hmac
import os
from base64 import urlsafe_b64encode
from collections.abc import AsyncIterator

import httpx
import pytest
from fastapi import FastAPI
from redis.asyncio import Redis

from app.abuse.config import AbuseConfig, ConfigStore
from app.abuse.limiter import Limiter
from app.abuse.middleware import AbuseMiddleware

SECRET = b"test-session-secret"
DROP = "3f6c2a1e-1111-4222-8333-944455556666"


def redis_url() -> str:
    base = os.environ.get("REDIS_URL", "redis://redis:6379/0")
    return base.rsplit("/", 1)[0] + "/15"


def token(sid: str) -> str:
    sig = urlsafe_b64encode(hmac.new(SECRET, sid.encode(), hashlib.sha256).digest())
    return f"{sid}.{sig.rstrip(b'=').decode()}"


def hdr(ip: str = "10.0.0.1", sid: str | None = None) -> dict[str, str]:
    h = {"X-Sim-Client-IP": ip}
    if sid:
        h["Authorization"] = f"Bearer {token(sid)}"
    return h


@pytest.fixture
async def redis() -> AsyncIterator[Redis]:
    r = Redis.from_url(redis_url(), decode_responses=True)
    await r.flushdb()
    yield r
    await r.flushdb()
    await r.aclose()


class Harness:
    def __init__(
        self, client: httpx.AsyncClient, store: ConfigStore, limiter: Limiter, hits: dict[str, int]
    ) -> None:
        self.client, self.store, self.limiter, self.hits = client, store, limiter, hits


def build_app(
    redis: Redis | None, cfg: AbuseConfig | None = None
) -> tuple[FastAPI, ConfigStore, Limiter, dict[str, int]]:
    os.environ["SIM_MODE"] = "true"
    hits = {"n": 0}
    app = FastAPI()

    @app.get("/api/drops/{drop_id}/me")
    async def me(drop_id: str) -> dict[str, object]:
        hits["n"] += 1
        return {"phase": "OPEN", "poll_after_ms": 2000}

    @app.post("/api/drops/{drop_id}/entries")
    async def entries(drop_id: str) -> dict[str, str]:
        hits["n"] += 1
        return {"entry_id": "e1", "status": "REGISTERED"}

    @app.get("/api/admin/drops/{drop_id}/metrics")
    async def metrics(drop_id: str) -> dict[str, bool]:
        return {"ok": True}

    @app.get("/api/healthz")
    async def healthz() -> dict[str, str]:
        return {"status": "ok"}

    store = ConfigStore(redis, initial=cfg)
    limiter = Limiter(redis, session_secret=SECRET, workers=1)
    app.add_middleware(AbuseMiddleware, limiter=limiter, config=store)
    return app, store, limiter, hits


@pytest.fixture
async def harness(redis: Redis) -> AsyncIterator[Harness]:
    app, store, limiter, hits = build_app(redis)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
        yield Harness(c, store, limiter, hits)
