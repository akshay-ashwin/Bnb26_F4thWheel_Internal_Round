"""Per-second counters aggregated in memory and flushed to Redis (`m:{epoch_s}` hashes).

Counters are evidence for the dashboard, never inputs to a decision. A crash loses at most one
flush interval. Hash fields: req_total, accepted, rate_limited, token_rejected, duplicate,
errors_5xx, claims_ok, claims_sold_out, step_up_issued/passed/failed, and latency buckets
`lat:<upper_bound_ms>`.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from collections import defaultdict

import redis.asyncio as aioredis

from app.cache import Cache, RedisUnavailableError

log = logging.getLogger("fairdrop.metrics")

LATENCY_BUCKETS_MS = (1, 2, 5, 10, 20, 50, 100, 200, 300, 500, 750, 1000, 2000, 5000)
KEY_TTL_S = 3600


def bucket_for(ms: float) -> str:
    for bound in LATENCY_BUCKETS_MS:
        if ms <= bound:
            return f"lat:{bound}"
    return "lat:inf"


class Metrics:
    def __init__(self, cache: Cache | None, flush_interval_s: float = 0.5) -> None:
        self._cache = cache
        self._interval = flush_interval_s
        self._pending: dict[int, dict[str, int]] = defaultdict(lambda: defaultdict(int))
        self._task: asyncio.Task[None] | None = None
        self.dropped = 0

    def reset(self) -> None:
        """Forget counts that have not been flushed yet (used by tests between cases)."""
        self._pending.clear()
        self.dropped = 0

    def set_cache(self, cache: Cache) -> None:
        self._cache = cache

    def incr(self, name: str, amount: int = 1) -> None:
        self._pending[int(time.time())][name] += amount

    def on_request(self, route: str, method: str, status: int, latency_ms: float) -> None:
        """Hook for `observability.metrics.request_hooks` (route template, method, status, ms)."""
        if not route.startswith("/api/") or route in ("/api/healthz", "/api/readyz"):
            return
        self.incr("req_total")
        self.observe_latency(latency_ms)
        if status >= 500:
            self.incr("errors_5xx")

    def observe_latency(self, ms: float) -> None:
        self.incr(bucket_for(ms))

    async def flush(self) -> None:
        if self._cache is None or not self._pending:
            return
        batch, self._pending = self._pending, defaultdict(lambda: defaultdict(int))

        async def write(r: aioredis.Redis) -> None:
            pipe = r.pipeline(transaction=False)
            for sec, fields in batch.items():
                key = f"m:{sec}"
                for name, value in fields.items():
                    pipe.hincrby(key, name, value)
                pipe.expire(key, KEY_TTL_S)
            await pipe.execute()

        try:
            await self._cache.run(write)
        except RedisUnavailableError:
            self.dropped += sum(len(f) for f in batch.values())

    async def _loop(self) -> None:
        while True:
            await asyncio.sleep(self._interval)
            try:
                await self.flush()
            except Exception:
                log.exception("metrics flush failed")

    def start(self) -> None:
        self._task = asyncio.create_task(self._loop())

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
        await self.flush()
