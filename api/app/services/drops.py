"""Read side of drops: the public drop view and the lazily-computed effective phase."""

from __future__ import annotations

import time
import uuid
from datetime import datetime
from typing import Any

import asyncpg

from app.clock import server_time
from app.errors import NotFound
from app.schemas.drops import DropOut

REVEALED_PHASES = ("DRAWN", "CLAIMING", "DONE")
HASH_PHASES = ("CLOSED", "DRAWN", "CLAIMING", "DONE")


def parse_drop_id(raw: str) -> uuid.UUID:
    try:
        return uuid.UUID(raw)
    except ValueError:
        raise NotFound("Drop not found") from None


def effective_phase(row: asyncpg.Record | dict[str, Any], now: datetime | None = None) -> str:
    """OPEN becomes CLOSED the moment `reg_closes_at` passes (Fair drops), before any job runs."""
    phase: str = row["phase"]
    closes: datetime | None = row["reg_closes_at"]
    if phase == "OPEN" and row["mode"] == "fair" and closes is not None:
        current = now or datetime.now(closes.tzinfo)
        if current >= closes:
            return "CLOSED"
    return phase


class _TtlCache:
    """Tiny in-process cache (the public drop view may be hammered at launch; contract: 1 s)."""

    def __init__(self, ttl_s: float) -> None:
        self.ttl = ttl_s
        self._items: dict[uuid.UUID, tuple[float, DropOut]] = {}

    def get(self, key: uuid.UUID) -> DropOut | None:
        hit = self._items.get(key)
        if hit and time.monotonic() - hit[0] < self.ttl:
            return hit[1]
        return None

    def put(self, key: uuid.UUID, value: DropOut) -> None:
        self._items[key] = (time.monotonic(), value)

    def clear(self) -> None:
        self._items.clear()


public_cache = _TtlCache(1.0)


async def get_drop(pool: asyncpg.Pool, drop_id: uuid.UUID, *, use_cache: bool = True) -> DropOut:
    if use_cache and (cached := public_cache.get(drop_id)) is not None:
        return cached.model_copy(update={"server_time": server_time()})
    row = await pool.fetchrow(
        "SELECT d.id, d.name, d.capacity, d.mode, d.phase, d.reg_opens_at, d.reg_closes_at,"
        "       d.claim_window_s, d.seed_commit, d.seed, d.entry_set_hash,"
        "       (SELECT count(*) FROM seats s WHERE s.drop_id = d.id AND s.status = 'free') AS free"
        " FROM drops d WHERE d.id = $1",
        drop_id,
    )
    if row is None:
        raise NotFound("Drop not found")
    phase = effective_phase(row)
    out = DropOut(
        id=row["id"],
        name=row["name"],
        capacity=row["capacity"],
        mode=row["mode"],
        phase=phase,  # type: ignore[arg-type]
        reg_opens_at=row["reg_opens_at"],
        reg_closes_at=row["reg_closes_at"],
        claim_window_s=row["claim_window_s"],
        seats_remaining=row["free"],
        seed_commit=row["seed_commit"],
        seed=row["seed"] if phase in REVEALED_PHASES else None,
        entry_set_hash=row["entry_set_hash"] if phase in HASH_PHASES else None,
    )
    public_cache.put(drop_id, out)
    return out
