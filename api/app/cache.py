"""Redis client with a circuit breaker.

Redis holds only things that are safe to lose (invariant 3). When it is slow or gone, callers
must not wait for a timeout on every request during a burst: after N consecutive connection
failures the breaker opens for a cool-off and every call takes its fallback immediately. After
the cool-off ONE probe call is let through (half-open); success closes the breaker.

Only connection-level failures count. Command errors (ResponseError, a bug in a script) do not,
so a coding mistake cannot make Redis look dead.
"""

import time
from collections.abc import Awaitable, Callable

import redis.asyncio as aioredis
from redis.asyncio.retry import Retry
from redis.backoff import NoBackoff
from redis.exceptions import ConnectionError as RedisConnectionError
from redis.exceptions import TimeoutError as RedisTimeoutError

from app.config import Settings

_FAILURES = (RedisConnectionError, RedisTimeoutError, OSError, TimeoutError)


class CircuitBreaker:
    def __init__(
        self, failures: int, cooloff_s: float, clock: Callable[[], float] = time.monotonic
    ) -> None:
        self._threshold = failures
        self._cooloff = cooloff_s
        self._clock = clock
        self._failures = 0
        self._open_until = 0.0
        self._probing = False

    @property
    def is_open(self) -> bool:
        return self._clock() < self._open_until or self._probing

    def allow(self) -> bool:
        """True if a call may go to Redis now. Pure state, no I/O."""
        if self._open_until == 0.0:
            return True
        if self._clock() < self._open_until:
            return False
        if self._probing:
            return False  # someone else is already probing
        self._probing = True
        return True

    def record_success(self) -> None:
        self._failures = 0
        self._open_until = 0.0
        self._probing = False

    def record_failure(self) -> None:
        self._probing = False
        self._failures += 1
        if self._failures >= self._threshold:
            self._open_until = self._clock() + self._cooloff


class Cache:
    def __init__(self, settings: Settings, client: aioredis.Redis | None = None) -> None:
        timeout = settings.redis_timeout_ms / 1000
        self.client: aioredis.Redis = client or aioredis.Redis.from_url(
            settings.redis_url,
            socket_timeout=timeout,
            socket_connect_timeout=timeout,
            retry=Retry(NoBackoff(), 0),
            max_connections=100,
            decode_responses=True,
        )
        self.breaker = CircuitBreaker(
            settings.redis_breaker_failures, settings.redis_breaker_cooloff_ms / 1000
        )

    def redis_available(self) -> bool:
        """For services that want to pick a path up front. No I/O."""
        return not self.breaker.is_open

    async def run[T](
        self,
        op: Callable[[aioredis.Redis], Awaitable[T]],
        *,
        fallback: Callable[[], Awaitable[T] | T],
    ) -> T:
        """Run one Redis operation; on breaker-open or connection failure use the fallback."""
        if not self.breaker.allow():
            return await _call(fallback)
        try:
            result = await op(self.client)
        except _FAILURES:
            self.breaker.record_failure()
            return await _call(fallback)
        self.breaker.record_success()
        return result

    async def ping(self) -> bool:
        async def _ping(r: aioredis.Redis) -> bool:
            return bool(await r.ping())

        return await self.run(_ping, fallback=lambda: False)

    async def close(self) -> None:
        await self.client.aclose()


async def _call[T](fn: Callable[[], Awaitable[T] | T]) -> T:
    result = fn()
    if isinstance(result, Awaitable):
        return await result
    return result
