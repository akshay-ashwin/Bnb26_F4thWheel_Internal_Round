"""Plan 02 test 10: v_drop_integrity at about 52,000 entries / 500 seats (polled every second by
the dashboard).

CI must not flake on a shared runner's clock, so the assertions are (1) the query plan never
scans `entries` sequentially and (2) a loose regression ceiling. The real numbers are printed
(run with `uv run fd test-api -- -s -k perf`) and recorded in the Plan 02 review log.
"""

from __future__ import annotations

import json
import os
import statistics
import time
import uuid
from typing import Any

import asyncpg

from tests.helpers import make_drop

ENTRIES = 52_000
CAPACITY = 500
OTHER_DROP_ENTRIES = 2_000
RUNS = 50
WARMUP = 3
# Loose on purpose: the target is p95 < 10 ms, this only catches an order-of-magnitude regression.
CEILING_MS = 100.0
QUERY = "SELECT * FROM v_drop_integrity WHERE drop_id = $1"


async def _load(db: asyncpg.Connection) -> tuple[uuid.UUID, uuid.UUID]:
    drop_id = await make_drop(db, capacity=CAPACITY, mode="fair")
    other = await make_drop(db, capacity=CAPACITY)
    await db.execute(
        "INSERT INTO users (public_id, phone_hash)"
        " SELECT 'u' || i, md5(i::text) FROM generate_series(1, $1::int) AS i",
        ENTRIES,
    )
    # Mixed statuses like a real drop: mostly REGISTERED, some DISQUALIFIED.
    await db.execute(
        "INSERT INTO entries (drop_id, user_id, status, run_no)"
        " SELECT $1, u.id,"
        "  CASE WHEN u.public_id LIKE '%7' THEN 'DISQUALIFIED' ELSE 'REGISTERED' END, 1"
        " FROM users u",
        drop_id,
    )
    await db.execute(
        "INSERT INTO entries (drop_id, user_id, status, run_no)"
        " SELECT $1, u.id, 'REGISTERED', 1 FROM users u LIMIT $2",
        other,
        OTHER_DROP_ENTRIES,
    )
    return drop_id, other


async def _allocate_up_to(db: asyncpg.Connection, drop_id: uuid.UUID, total: int) -> None:
    """Make `total` seats sold in one consistent step (entry, seat and ledger row together)."""
    async with db.transaction():
        await db.execute(
            "CREATE TEMP TABLE pairs ON COMMIT DROP AS"
            " SELECT e.id AS entry_id, s.id AS seat_id FROM"
            "  (SELECT id, row_number() OVER (ORDER BY id) AS rn FROM entries"
            "   WHERE drop_id = $1 AND status = 'REGISTERED' ORDER BY id LIMIT $2) e"
            " JOIN (SELECT id, row_number() OVER (ORDER BY seat_no) AS rn FROM seats"
            "       WHERE drop_id = $1 AND status = 'free' ORDER BY seat_no LIMIT $2) s USING (rn)",
            drop_id,
            total,
        )
        await db.execute(
            "UPDATE seats SET status = 'sold', entry_id = p.entry_id, sold_at = now()"
            " FROM pairs p WHERE seats.id = p.seat_id"
        )
        await db.execute(
            "UPDATE entries SET status = 'ALLOCATED', allocated_at = now()"
            " FROM pairs p WHERE entries.id = p.entry_id"
        )
        await db.execute(
            "INSERT INTO allocations (drop_id, entry_id, seat_id, idempotency_key, run_no)"
            " SELECT $1, entry_id, seat_id, gen_random_uuid(), 1 FROM pairs",
            drop_id,
        )
    await db.execute("ANALYZE entries, seats, allocations")


def _nodes(plan: dict[str, Any]) -> list[dict[str, Any]]:
    found = [plan]
    for child in plan.get("Plans", []):
        found.extend(_nodes(child))
    return found


async def _measure(db: asyncpg.Connection, drop_id: uuid.UUID, label: str) -> None:
    for _ in range(WARMUP):
        await db.fetchrow(QUERY, drop_id)

    client_ms: list[float] = []
    for _ in range(RUNS):
        started = time.perf_counter()
        row = await db.fetchrow(QUERY, drop_id)
        client_ms.append((time.perf_counter() - started) * 1000)
    assert row is not None and row["invariant_ok"] is True

    server_ms: list[float] = []
    planning_ms: list[float] = []
    plan: dict[str, Any] = {}
    for _ in range(RUNS):
        raw = await db.fetchval(
            f"EXPLAIN (ANALYZE, FORMAT JSON) {QUERY.replace('$1', repr(str(drop_id)))}"
        )
        doc = json.loads(raw) if isinstance(raw, str) else raw
        server_ms.append(doc[0]["Execution Time"])
        planning_ms.append(doc[0]["Planning Time"])
        plan = doc[0]["Plan"]

    def stats(values: list[float]) -> str:
        ordered = sorted(values)
        return (
            f"min={ordered[0]:6.2f}  p50={statistics.median(ordered):6.2f}  "
            f"p95={ordered[int(len(ordered) * 0.95) - 1]:6.2f}  max={ordered[-1]:6.2f} ms"
        )

    print(
        f"\n[integrity view @ {ENTRIES} entries, {label}] sold={row['sold']}"
        f"\n    asyncpg round trip : {stats(client_ms)}"
        f"\n    server execution   : {stats(server_ms)}"
        f"\n    server planning    : {stats(planning_ms)}  (paid once per prepared statement)"
    )

    if os.environ.get("PERF_PLAN"):  # exploration aid: show the plan at every state
        text = await db.fetch(
            f"EXPLAIN (ANALYZE, BUFFERS) {QUERY.replace('$1', repr(str(drop_id)))}"
        )
        print("\n".join(r[0] for r in text))

    seq_scanned = {
        n["Relation Name"]
        for n in _nodes(plan)
        if n.get("Node Type") == "Seq Scan" and n.get("Relation Name") == "entries"
    }
    assert not seq_scanned, f"sequential scan on entries in the plan ({label})"
    assert sorted(client_ms)[int(RUNS * 0.95) - 1] < CEILING_MS


async def test_integrity_view_stays_fast_at_52k_entries(db: asyncpg.Connection) -> None:
    drop_id, other = await _load(db)
    await db.execute("ANALYZE users, entries, seats")
    assert await db.fetchval("SELECT count(*) FROM entries") == ENTRIES + OTHER_DROP_ENTRIES

    await _measure(db, drop_id, "empty: 0 sold")
    await _allocate_up_to(db, drop_id, CAPACITY // 2)
    await _measure(db, drop_id, "half sold: 250 sold")
    await _allocate_up_to(db, drop_id, CAPACITY - CAPACITY // 2)
    await _measure(db, drop_id, "fully sold: 500 sold")
    await _measure(db, other, "other drop (2,000 entries, nothing sold)")

    # the human-readable plan, for the review log
    plan = await db.fetch(f"EXPLAIN (ANALYZE, BUFFERS) {QUERY.replace('$1', repr(str(drop_id)))}")
    print("\n[EXPLAIN (ANALYZE, BUFFERS) at 500 sold]\n" + "\n".join(r[0] for r in plan))
