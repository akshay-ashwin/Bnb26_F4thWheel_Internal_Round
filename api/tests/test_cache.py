import asyncio
import time

import pytest
from redis.exceptions import ConnectionError as RedisConnectionError
from redis.exceptions import ResponseError

from app.cache import Cache, CircuitBreaker
from app.config import Settings


class Clock:
    def __init__(self) -> None:
        self.now = 100.0

    def __call__(self) -> float:
        return self.now


def test_breaker_opens_after_n_failures_and_probes_once() -> None:
    clock = Clock()
    b = CircuitBreaker(3, 2.0, clock)
    for _ in range(2):
        assert b.allow()
        b.record_failure()
    assert not b.is_open
    assert b.allow()
    b.record_failure()
    assert b.is_open and not b.allow()
    clock.now += 2.1
    assert b.allow()  # the single half-open probe
    assert not b.allow()  # everyone else still takes the fallback
    b.record_failure()  # probe failed: open again for another cool-off
    assert b.is_open and not b.allow()
    clock.now += 2.1
    assert b.allow()
    b.record_success()
    assert not b.is_open and b.allow()


def test_success_resets_the_failure_count() -> None:
    b = CircuitBreaker(3, 2.0, Clock())
    b.record_failure()
    b.record_failure()
    b.record_success()
    b.record_failure()
    assert not b.is_open


async def test_dead_redis_trips_breaker_and_fallback_is_instant(test_settings: Settings) -> None:
    cache = Cache(test_settings.model_copy(update={"redis_url": "redis://127.0.0.1:1/0"}))
    calls = 0

    async def op(r: object) -> str:
        nonlocal calls
        calls += 1
        raise RedisConnectionError("down")

    for _ in range(3):
        assert await cache.run(op, fallback=lambda: "fb") == "fb"
    assert calls == 3 and not cache.redis_available()
    started = time.perf_counter()
    for _ in range(1000):
        assert await cache.run(op, fallback=lambda: "fb") == "fb"
    assert calls == 3  # Redis was not touched while open
    assert time.perf_counter() - started < 0.5
    await cache.close()


async def test_command_errors_do_not_trip_the_breaker(test_settings: Settings) -> None:
    cache = Cache(test_settings)

    async def bad(r: object) -> str:
        raise ResponseError("WRONGTYPE")

    for _ in range(5):
        with pytest.raises(ResponseError):
            await cache.run(bad, fallback=lambda: "fb")
    assert cache.redis_available()
    assert await cache.ping()
    await cache.close()


async def test_warm_opens_connections_ahead_of_traffic(test_settings: Settings) -> None:
    cache = Cache(test_settings)
    probe = Cache(test_settings)
    try:
        before = int((await probe.client.info("clients"))["connected_clients"])
        assert await cache.warm(12) == 24  # 12 for the fast client, 12 for the required one
        after = int((await probe.client.info("clients"))["connected_clients"])
        assert after - before == 24
        # They are pooled and reusable: a burst of 12 needs no new connections.
        await asyncio.gather(*(cache.ping() for _ in range(12)))
        await asyncio.gather(*(cache.run_required(lambda r: r.ping()) for _ in range(12)))
        assert int((await probe.client.info("clients"))["connected_clients"]) == after
    finally:
        await cache.close()
        await probe.close()


async def test_warm_never_raises_when_redis_is_down(test_settings: Settings) -> None:
    cache = Cache(test_settings.model_copy(update={"redis_url": "redis://127.0.0.1:1/0"}))
    try:
        assert await cache.warm(5) == 0
    finally:
        await cache.close()


async def test_required_client_is_tolerant_and_ignores_the_breaker(test_settings: Settings) -> None:
    cache = Cache(test_settings)
    try:
        assert cache.required_client is not cache.client
        for _ in range(3):
            cache.breaker.record_failure()
        assert cache.breaker.is_open
        assert await cache.run_required(lambda r: r.ping()) is True
    finally:
        await cache.close()


async def test_required_operation_on_dead_redis_is_a_503(test_settings: Settings) -> None:
    from app.errors import ServiceUnavailable

    cache = Cache(test_settings.model_copy(update={"redis_url": "redis://127.0.0.1:1/0"}))
    try:
        with pytest.raises(ServiceUnavailable):
            await cache.run_required(lambda r: r.ping())
    finally:
        await cache.close()
