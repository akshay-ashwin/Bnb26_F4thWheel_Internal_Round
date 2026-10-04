"""Periodic jobs, run by every worker (all of them are idempotent and guarded):

* auto-close: OPEN Fair drops whose window ended become CLOSED
* sweeper: expire offers, promote the waitlist, detect DONE (per CLAIMING drop)
* finish: DONE drops get leftover entries marked NOT_SELECTED
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import uuid

import asyncpg

from app.cache import Cache
from app.config import Settings
from app.metrics import Metrics
from app.services.claim import finalize_unselected
from app.services.drops import public_cache
from app.services.sweeper import sweep_drop

log = logging.getLogger("fairdrop.jobs")


async def auto_close(pool: asyncpg.Pool) -> int:
    result = await pool.execute(
        "UPDATE drops SET phase = 'CLOSED', closed_at = now()"
        " WHERE phase = 'OPEN' AND mode = 'fair' AND reg_closes_at <= now()"
    )
    closed = int(result.split()[-1])
    if closed:
        public_cache.clear()
    return closed


async def finish_done_drops(pool: asyncpg.Pool) -> None:
    rows = await pool.fetch(
        "SELECT d.id FROM drops d WHERE d.phase = 'DONE' AND EXISTS ("
        " SELECT 1 FROM entries e WHERE e.drop_id = d.id"
        " AND e.status IN ('REGISTERED', 'WAITLISTED'))"
    )
    for r in rows:
        await finalize_unselected(pool, r["id"], retries=1)


async def tick(
    pool: asyncpg.Pool, cache: Cache, settings: Settings, metrics: Metrics, *, finish: bool = True
) -> None:
    await auto_close(pool)
    drops = await pool.fetch("SELECT id FROM drops WHERE phase = 'CLAIMING' AND mode = 'fair'")
    for r in drops:
        drop_id: uuid.UUID = r["id"]
        await sweep_drop(pool, cache, settings, metrics, drop_id)
    if finish:
        await finish_done_drops(pool)


class Jobs:
    def __init__(
        self, pool: asyncpg.Pool, cache: Cache, settings: Settings, metrics: Metrics
    ) -> None:
        self._args = (pool, cache, settings, metrics)
        self._interval = settings.job_interval_s
        self._task: asyncio.Task[None] | None = None
        self._n = 0

    async def _loop(self) -> None:
        while True:
            await asyncio.sleep(self._interval)
            self._n += 1
            try:
                await tick(*self._args, finish=self._n % 5 == 0)
            except Exception:
                log.exception("job tick failed")  # a failing job never kills the runner

    def start(self) -> None:
        self._task = asyncio.create_task(self._loop())

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
