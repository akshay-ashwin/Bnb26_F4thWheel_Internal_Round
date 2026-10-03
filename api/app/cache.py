"""Redis client with a tiny circuit breaker.

Redis holds only things that are safe to lose (rate-limit buckets, OTP requests, counters).
After three consecutive failures the breaker opens for a few seconds so callers fail fast instead
of waiting for socket timeouts during a burst.
"""

from __future__ import annotations

import time
from collections.abc import Awaitable, Callable
from typing import Any

import redis.asyncio as aioredis
from redis.exceptions import RedisError

from app.config import Settings


class RedisUnavailableError(Exception):
    pass


class Cache:
    def __init__(self, settings: Settings) -> None:
        self.client: aioredis.Redis = aioredis.from_url(
            settings.redis_url,
            socket_timeout=settings.redis_timeout_ms / 1000,
            socket_connect_timeout=settings.redis_timeout_ms / 1000,
            decode_responses=True,
        )
        self._failures = 0
        self._open_until = 0.0

    @property
    def available(self) -> bool:
        return time.monotonic() >= self._open_until

    async def run(self, fn: Callable[[aioredis.Redis], Awaitable[Any]]) -> Any:
        """Run one Redis operation through the breaker; raises RedisUnavailableError."""
        if not self.available:
            raise RedisUnavailableError("circuit open")
        try:
            result = await fn(self.client)
        except (RedisError, OSError, TimeoutError) as exc:
            self._failures += 1
            if self._failures >= 3:
                self._open_until = time.monotonic() + 5.0
                self._failures = 0
            raise RedisUnavailableError(str(exc)) from exc
        self._failures = 0
        return result

    async def ping(self) -> bool:
        try:
            await self.run(lambda r: r.ping())
        except RedisUnavailableError:
            return False
        return True

    async def close(self) -> None:
        await self.client.aclose()
