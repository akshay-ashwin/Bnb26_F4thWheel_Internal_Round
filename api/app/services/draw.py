"""Freeze the entry set, then draw.

The draw is one transaction and a pure function of (seed, drop id, frozen set of public ids), so a
crash before commit leaves the phase CLOSED and a re-run produces identical ranks.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from typing import Any

import asyncpg

from app import abuse
from app.cache import Cache, RedisUnavailableError
from app.config import Settings
from app.db import transaction
from app.draw_math import ALGORITHM, entry_set_hash, rank_entries
from app.errors import InvalidTransition, NotFound
from app.metrics import Metrics
from app.services.drops import public_cache

log = logging.getLogger("fairdrop.draw")

ELIGIBLE = (
    "SELECT e.id, u.public_id, e.risk_score FROM entries e JOIN users u ON u.id = e.user_id"
    " WHERE e.drop_id = $1 AND e.status = 'REGISTERED'"
)


async def freeze(pool: asyncpg.Pool, settings: Settings, drop_id: uuid.UUID) -> str:
    """Wait out the close grace period, then compute and publish `entry_set_hash`.

    An insert that started before `reg_closes_at` may still be committing, so the entry set is not
    read until `reg_closes_at + grace` (grace > the entries statement timeout).
    """
    wait = await pool.fetchval(
        "SELECT extract(epoch FROM (reg_closes_at + make_interval(secs => $2) - now()))"
        " FROM drops WHERE id = $1",
        drop_id,
        settings.draw_grace_s,
    )
    if wait is not None and wait > 0:
        await asyncio.sleep(min(float(wait), 30.0))
    existing: str | None = await pool.fetchval(
        "SELECT entry_set_hash FROM drops WHERE id = $1", drop_id
    )
    if existing:
        return existing
    rows = await pool.fetch(ELIGIBLE, drop_id)
    digest = entry_set_hash(r["public_id"] for r in rows)
    await pool.execute(
        "UPDATE drops SET entry_set_hash = $2"
        " WHERE id = $1 AND phase = 'CLOSED' AND entry_set_hash IS NULL",
        drop_id,
        digest,
    )
    stored: str = await pool.fetchval("SELECT entry_set_hash FROM drops WHERE id = $1", drop_id)
    public_cache.clear()
    return stored


async def run_draw(
    pool: asyncpg.Pool,
    cache: Cache,
    settings: Settings,
    metrics: Metrics,
    drop_id: uuid.UUID,
) -> str:
    """Run the draw for a CLOSED Fair drop. Returns the resulting phase (CLAIMING).

    Calling it again after the draw returns the same phase without changing anything.
    """
    await freeze(pool, settings, drop_id)
    async with transaction(
        pool, statement_timeout_ms=60_000, acquire_timeout_s=settings.pool_acquire_timeout_s
    ) as conn:
        drop = await conn.fetchrow(
            "SELECT phase, mode, capacity, claim_window_s, seed, entry_set_hash"
            " FROM drops WHERE id = $1 FOR UPDATE",
            drop_id,
        )
        if drop is None:
            raise NotFound("Drop not found")
        if drop["mode"] != "fair":
            raise InvalidTransition("FIFO drops have no draw")
        if drop["phase"] in ("DRAWN", "CLAIMING", "DONE"):
            return str(drop["phase"])  # idempotent
        if drop["phase"] != "CLOSED":
            raise InvalidTransition("Close registration before drawing")

        await abuse.rescore_eligible(conn, str(drop_id))
        rows = await conn.fetch(ELIGIBLE, drop_id)
        if entry_set_hash(r["public_id"] for r in rows) != drop["entry_set_hash"]:
            raise RuntimeError("entry set changed after it was frozen; aborting the draw")
        risk = {str(r["id"]): r["risk_score"] for r in rows}
        ranked = rank_entries(
            drop["seed"], str(drop_id), ((str(r["id"]), r["public_id"]) for r in rows)
        )
        capacity = drop["capacity"]
        records = []
        for item in ranked:
            if item.rank > capacity:
                new_status = "WAITLISTED"
            elif risk[item.entry_id] >= settings.step_up_threshold:
                new_status = "STEP_UP_REQUIRED"
            else:
                new_status = "OFFERED"
            records.append((uuid.UUID(item.entry_id), item.rank, new_status))
        if records:
            await conn.execute(
                "CREATE TEMP TABLE draw_ranks (entry_id uuid PRIMARY KEY, rank int NOT NULL,"
                " new_status text NOT NULL) ON COMMIT DROP"
            )
            await conn.copy_records_to_table(
                "draw_ranks", records=records, columns=["entry_id", "rank", "new_status"]
            )
            updated = await conn.execute(
                "UPDATE entries e SET status = t.new_status, draw_rank = t.rank,"
                " offered_at = CASE WHEN t.new_status <> 'WAITLISTED' THEN now() END,"
                " offer_expires_at = CASE WHEN t.new_status <> 'WAITLISTED'"
                "   THEN now() + make_interval(secs => $2) END,"
                " status_changed_at = now()"
                " FROM draw_ranks t WHERE e.id = t.entry_id AND e.drop_id = $1"
                " AND e.status = 'REGISTERED'",
                drop_id,
                float(drop["claim_window_s"]),
            )
            if updated != f"UPDATE {len(records)}":
                raise RuntimeError(f"draw wrote {updated} rows for {len(records)} entries")
        moved = await conn.execute(
            "UPDATE drops SET phase = 'CLAIMING', drawn_at = now()"
            " WHERE id = $1 AND phase = 'CLOSED'",
            drop_id,
        )
        if moved != "UPDATE 1":
            raise RuntimeError("drop phase changed during the draw")

    try:
        await cache.run(
            lambda r: r.set(f"drop:{drop_id}:offer_head", min(capacity, len(ranked)), ex=3600)
        )
    except RedisUnavailableError:
        pass
    public_cache.clear()
    metrics.incr("draws")
    return "CLAIMING"


async def proof(pool: asyncpg.Pool, drop_id: uuid.UUID, *, public: bool) -> dict[str, Any]:
    """Everything a judge needs to re-run the draw. Only after the seed is revealed."""
    drop = await pool.fetchrow(
        "SELECT id, phase, run_no, seed, seed_commit, entry_set_hash FROM drops WHERE id = $1",
        drop_id,
    )
    if drop is None:
        raise NotFound("Drop not found")
    if drop["phase"] not in ("DRAWN", "CLAIMING", "DONE"):
        raise InvalidTransition("The draw has not happened yet")
    out: dict[str, Any] = {
        "seed_commit": drop["seed_commit"],
        "seed": drop["seed"],
        "entry_set_hash": drop["entry_set_hash"],
        "algorithm": ALGORITHM,
        "drop_id": str(drop["id"]),
        "run_no": drop["run_no"],
    }
    if public:
        rows = await pool.fetch(
            "SELECT u.public_id, e.draw_rank FROM entries e JOIN users u ON u.id = e.user_id"
            " WHERE e.drop_id = $1 AND e.draw_rank IS NOT NULL ORDER BY e.draw_rank",
            drop_id,
        )
        out["ranked_public_ids"] = [r["public_id"] for r in rows]
        out["eligible_public_ids"] = sorted(out["ranked_public_ids"])
    return out
