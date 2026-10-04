"""Admin drop lifecycle: create, open, close, draw, reset. Every transition is one guarded update
(`... WHERE id = $1 AND phase = <expected>`); repeating an action that already took effect returns
200 with the current phase (admin actions are idempotent)."""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from typing import Any

import asyncpg

from app.cache import Cache, RedisUnavailableError
from app.config import Settings
from app.db import transaction
from app.draw_math import new_seed, seed_commit
from app.errors import InvalidTransition, NotFound, ValidationFailed
from app.metrics import Metrics
from app.schemas.drops import CreateDropIn
from app.services import draw
from app.services.claim import finalize_unselected
from app.services.drops import public_cache

log = logging.getLogger("fairdrop.lifecycle")


async def create_drop(pool: asyncpg.Pool, body: CreateDropIn) -> tuple[uuid.UUID, str]:
    """The drop and all its seats come into existence in one `create_drop()` call. The seed is
    32 CSPRNG bytes; the published commitment is SHA-256 of the raw bytes."""
    seed = new_seed()
    commit = seed_commit(seed)
    drop_id: uuid.UUID = await pool.fetchval(
        "SELECT create_drop($1, $2, $3, $4, $5, $6, $7)",
        body.name,
        body.capacity,
        body.mode,
        body.window_s,
        body.claim_window_s,
        commit,
        seed,
    )
    return drop_id, commit


async def _phase(pool: asyncpg.Pool, drop_id: uuid.UUID) -> str:
    phase: str | None = await pool.fetchval("SELECT phase FROM drops WHERE id = $1", drop_id)
    if phase is None:
        raise NotFound("Drop not found")
    return phase


async def apply_action(
    pool: asyncpg.Pool,
    cache: Cache,
    settings: Settings,
    metrics: Metrics,
    drop_id: uuid.UUID,
    action: str,
    *,
    mode: str | None,
) -> str:
    current = await _phase(pool, drop_id)  # also 404s
    if mode is not None and action != "reset":
        raise ValidationFailed("`mode` is only valid with the reset action")
    try:
        if action == "open":
            return await _open(pool, drop_id, current)
        if action == "close":
            return await _close(pool, drop_id, current)
        if action == "draw":
            return await _draw(pool, cache, settings, metrics, drop_id, current)
        return await _reset(pool, cache, drop_id, mode)
    finally:
        public_cache.clear()


async def _open(pool: asyncpg.Pool, drop_id: uuid.UUID, current: str) -> str:
    moved = await pool.execute(
        "UPDATE drops SET phase = 'OPEN', reg_opens_at = now(),"
        " reg_closes_at = now() + make_interval(secs => window_s)"
        " WHERE id = $1 AND phase = 'SCHEDULED'",
        drop_id,
    )
    if moved == "UPDATE 1":
        return "OPEN"
    phase = await _phase(pool, drop_id)
    if phase == "OPEN":
        return phase
    raise InvalidTransition(f"Cannot open a drop that is {phase}")


async def _close(pool: asyncpg.Pool, drop_id: uuid.UUID, current: str) -> str:
    row = await pool.fetchrow("SELECT mode FROM drops WHERE id = $1", drop_id)
    if row is None:
        raise NotFound("Drop not found")
    if row["mode"] == "fair":
        moved = await pool.execute(
            "UPDATE drops SET phase = 'CLOSED', closed_at = now(),"
            " reg_closes_at = LEAST(reg_closes_at, now())"
            " WHERE id = $1 AND phase = 'OPEN'",
            drop_id,
        )
        if moved == "UPDATE 1":
            return "CLOSED"
        phase = await _phase(pool, drop_id)
        if phase in ("CLOSED", "DRAWN"):
            return phase
        if phase == "CLAIMING":
            await pool.execute(
                "UPDATE drops SET phase = 'DONE', done_at = now()"
                " WHERE id = $1 AND phase = 'CLAIMING'",
                drop_id,
            )
            await finalize_unselected(pool, drop_id)
            return "DONE"
        if phase == "DONE":
            return phase
        raise InvalidTransition(f"Cannot close a drop that is {phase}")
    moved = await pool.execute(
        "UPDATE drops SET phase = 'DONE', closed_at = now(), done_at = now()"
        " WHERE id = $1 AND phase = 'OPEN'",
        drop_id,
    )
    phase = "DONE" if moved == "UPDATE 1" else await _phase(pool, drop_id)
    if phase == "DONE":
        await finalize_unselected(pool, drop_id)
        return phase
    raise InvalidTransition(f"Cannot close a drop that is {phase}")


async def _draw(
    pool: asyncpg.Pool,
    cache: Cache,
    settings: Settings,
    metrics: Metrics,
    drop_id: uuid.UUID,
    current: str,
) -> str:
    row = await pool.fetchrow("SELECT mode FROM drops WHERE id = $1", drop_id)
    if row is None:
        raise NotFound("Drop not found")
    if row["mode"] != "fair":
        raise InvalidTransition("FIFO drops have no draw")
    # A window that has ended is a CLOSED drop even if the ticker has not run yet.
    await pool.execute(
        "UPDATE drops SET phase = 'CLOSED', closed_at = now()"
        " WHERE id = $1 AND phase = 'OPEN' AND reg_closes_at <= now()",
        drop_id,
    )
    return await draw.run_draw(pool, cache, settings, metrics, drop_id)


async def _reset(pool: asyncpg.Pool, cache: Cache, drop_id: uuid.UUID, mode: str | None) -> str:
    """Archive the finished run, wipe the run's data and start the next run (SCHEDULED).

    One transaction: users, sessions and run history survive; a fresh seed and commitment are
    generated; the mode may be switched (the demo flips FIFO -> Fair on the same drop).
    """
    seed = new_seed()
    async with transaction(pool, statement_timeout_ms=30_000) as conn:
        drop = await conn.fetchrow(
            "SELECT run_no, mode, reg_opens_at FROM drops WHERE id = $1 FOR UPDATE", drop_id
        )
        if drop is None:
            raise NotFound("Drop not found")
        counts = {
            r["status"]: r["n"]
            for r in await conn.fetch(
                "SELECT status, count(*) AS n FROM entries WHERE drop_id = $1 GROUP BY status",
                drop_id,
            )
        }
        integrity = await conn.fetchrow(
            "SELECT * FROM v_drop_integrity WHERE drop_id = $1", drop_id
        )
        summary: dict[str, Any] = {
            "entries_by_status": counts,
            "integrity": {
                k: (v if not isinstance(v, uuid.UUID) else str(v))
                for k, v in dict(integrity).items()
            }
            if integrity
            else None,
        }
        await conn.execute(
            "INSERT INTO drop_runs (drop_id, run_no, mode, started_at, ended_at, summary)"
            " VALUES ($1, $2, $3, $4, now(), $5) ON CONFLICT (drop_id, run_no) DO NOTHING",
            drop_id,
            drop["run_no"],
            drop["mode"],
            drop["reg_opens_at"],
            json.loads(json.dumps(summary, default=str)),
        )
        await conn.fetchval("SELECT admin_reset_drop($1)", drop_id)
        await conn.execute(
            "UPDATE drops SET phase = 'SCHEDULED', mode = $2, seed = $3, seed_commit = $4,"
            " reg_opens_at = NULL, reg_closes_at = NULL WHERE id = $1",
            drop_id,
            mode or drop["mode"],
            seed,
            seed_commit(seed),
        )
    try:
        await cache.run(lambda r: r.delete(f"drop:{drop_id}:offer_head"))
    except RedisUnavailableError:
        pass
    await asyncio.sleep(0)
    return "SCHEDULED"
