"""Offer expiry and waitlist promotion for Fair drops in CLAIMING (one transaction per drop).

Safe to run in every worker: the drop row is taken with `FOR UPDATE SKIP LOCKED`, so one sweeper
works on a drop at a time, and every status change is a guarded update. The claim path rejects
expired offers by itself, so a stopped sweeper only delays promotion; it never lets anyone claim
late.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass

import asyncpg

from app.cache import Cache, RedisUnavailableError
from app.config import Settings
from app.db import transaction
from app.metrics import Metrics
from app.services.claim import finalize_unselected
from app.services.drops import public_cache

log = logging.getLogger("fairdrop.sweeper")


@dataclass(frozen=True)
class SweepResult:
    expired: int = 0
    promoted: int = 0
    done: bool = False


async def sweep_drop(
    pool: asyncpg.Pool, cache: Cache, settings: Settings, metrics: Metrics, drop_id: uuid.UUID
) -> SweepResult:
    async with transaction(
        pool, statement_timeout_ms=15_000, acquire_timeout_s=settings.pool_acquire_timeout_s
    ) as conn:
        drop = await conn.fetchrow(
            "SELECT claim_window_s FROM drops"
            " WHERE id = $1 AND phase = 'CLAIMING' AND mode = 'fair'"
            " FOR UPDATE SKIP LOCKED",
            drop_id,
        )
        if drop is None:
            return SweepResult()
        expired: int = await conn.fetchval(
            "WITH x AS (UPDATE entries SET status = 'OFFER_EXPIRED', status_changed_at = now()"
            " WHERE drop_id = $1 AND status IN ('OFFERED', 'STEP_UP_REQUIRED')"
            " AND offer_expires_at < now() RETURNING 1) SELECT count(*) FROM x",
            drop_id,
        )
        free: int = await conn.fetchval(
            "SELECT count(*) FROM seats WHERE drop_id = $1 AND status = 'free'", drop_id
        )
        active: int = await conn.fetchval(
            "SELECT count(*) FROM entries WHERE drop_id = $1"
            " AND status IN ('OFFERED', 'STEP_UP_REQUIRED') AND offer_expires_at >= now()",
            drop_id,
        )
        slots = max(0, free - active)
        promoted = 0
        head: int | None = None
        if slots:
            rows = await conn.fetch(
                "UPDATE entries SET"
                " status = CASE WHEN risk_score >= $3 THEN 'STEP_UP_REQUIRED' ELSE 'OFFERED' END,"
                " offered_at = now(), offer_expires_at = now() + make_interval(secs => $4),"
                " status_changed_at = now()"
                " WHERE id IN (SELECT id FROM entries WHERE drop_id = $1 AND status = 'WAITLISTED'"
                "   ORDER BY draw_rank LIMIT $2 FOR UPDATE SKIP LOCKED)"
                " RETURNING draw_rank",
                drop_id,
                slots,
                settings.step_up_threshold,
                float(drop["claim_window_s"]),
            )
            promoted = len(rows)
            if rows:
                head = max(r["draw_rank"] for r in rows)
        waitlisted: int = await conn.fetchval(
            "SELECT count(*) FROM entries WHERE drop_id = $1 AND status = 'WAITLISTED'", drop_id
        )
        active_after = active + promoted
        done = free == 0 or (active_after == 0 and waitlisted == 0)
        if done:
            await conn.execute(
                "UPDATE drops SET phase = 'DONE', done_at = now()"
                " WHERE id = $1 AND phase = 'CLAIMING'",
                drop_id,
            )
    if head is not None:
        try:
            await cache.run(lambda r: r.set(f"drop:{drop_id}:offer_head", head, ex=3600))
        except RedisUnavailableError:
            pass
    if expired:
        metrics.incr("offers_expired", expired)
    if promoted:
        metrics.incr("promoted", promoted)
    if done:
        public_cache.clear()
        await finalize_unselected(pool, drop_id)
    return SweepResult(expired, promoted, done)
