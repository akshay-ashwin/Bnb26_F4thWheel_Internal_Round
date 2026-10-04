"""Redis client with a circuit breaker.

Redis holds only things that are safe to lose (invariant 3). When it is slow or gone, callers
must not wait for a timeout on every request during a burst: after N consecutive connection
failures the breaker opens for a cool-off and every call takes its fallback immediately. After
the cool-off ONE probe call is let through (half-open); success closes the breaker.

A second client, `run_required`, is for operations that have NO fallback (the OTP store: if Redis
is gone the user cannot log in anyway). It has its own tolerant timeout (REDIS_REQUIRED_TIMEOUT_MS)
and no breaker: during a burst the 50 ms fast path times out because the event loop is busy, and a
breaker would turn that blip into seconds of 503 for every login (measured, Plan 04).

Only connection-level failures count. Command errors (ResponseError, a bug in a script) do not,
so a coding mistake cannot make Redis look dead.
"""

import logging
import time
from collections.abc import Awaitable, Callable

import redis.asyncio as aioredis
from redis.asyncio.retry import Retry
from redis.backoff import NoBackoff
from redis.exceptions import ConnectionError as RedisConnectionError
from redis.exceptions import TimeoutError as RedisTimeoutError

from app.config import Settings
from app.errors import ServiceUnavailable

log = logging.getLogger("fairdrop.cache")
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
    max_connections = 100

    def __init__(self, settings: Settings, client: aioredis.Redis | None = None) -> None:
        self.client: aioredis.Redis = client or self._make_client(
            settings, settings.redis_timeout_ms
        )
        # Tolerant client for operations with no fallback. Never shorter than the fast one.
        self.required_client: aioredis.Redis = (
            self.client
            if client is not None
            else self._make_client(
                settings, max(settings.redis_timeout_ms, settings.redis_required_timeout_ms)
            )
        )
        self.breaker = CircuitBreaker(
            settings.redis_breaker_failures, settings.redis_breaker_cooloff_ms / 1000
        )

    @classmethod
    def _make_client(cls, settings: Settings, timeout_ms: int) -> aioredis.Redis:
        timeout = timeout_ms / 1000
        return aioredis.Redis.from_url(
            settings.redis_url,
            socket_timeout=timeout,
            socket_connect_timeout=timeout,
            retry=Retry(NoBackoff(), 0),
            max_connections=cls.max_connections,
            decode_responses=True,
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

    async def run_required[T](self, op: Callable[[aioredis.Redis], Awaitable[T]]) -> T:
        """Run an operation that has no fallback. A connection failure is SERVICE_UNAVAILABLE."""
        try:
            return await op(self.required_client)
        except _FAILURES:
            raise ServiceUnavailable(
                "Sign-in is temporarily unavailable", retry_after_ms=1000
            ) from None

    async def warm(self, count: int) -> int:
        """Open `count` connections one after another before traffic arrives; returns how many.

        Opening a connection costs ~1.5 ms of event-loop time. A burst on a cold pool opens dozens
        at once, the last ones exceed REDIS_TIMEOUT_MS, and the breaker then fails every request
        for its cool-off (measured: 100 cold connections took ~150 ms against a 50 ms timeout).
        Sequential opening keeps each one far inside the timeout. Never raises: a Redis that is
        down at boot is the breaker's problem, not a reason to refuse to start."""
        total = 0
        for client in {
            id(self.client): self.client,
            id(self.required_client): self.required_client,
        }.values():
            total += await self._warm_one(client, count)
        return total

    async def _warm_one(self, client: aioredis.Redis, count: int) -> int:
        pool = client.connection_pool
        opened = []
        try:
            for _ in range(min(count, self.max_connections)):
                opened.append(await pool.get_connection())  # type: ignore[no-untyped-call]
        except Exception:  # noqa: BLE001 (any failure: serve cold, the breaker handles the rest)
            log.warning("redis warm-up stopped early", extra={"opened": len(opened)})
        finally:
            for conn in opened:
                await pool.release(conn)
        return len(opened)

    async def ping(self) -> bool:
        async def _ping(r: aioredis.Redis) -> bool:
            return bool(await r.ping())

        return await self.run(_ping, fallback=lambda: False)

    async def close(self) -> None:
        await self.client.aclose()
        if self.required_client is not self.client:
            await self.required_client.aclose()


async def _call[T](fn: Callable[[], Awaitable[T] | T]) -> T:
    result = fn()
    if isinstance(result, Awaitable):
        return await result
    return result
