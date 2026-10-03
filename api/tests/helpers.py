"""SQL helpers shared by the database tests, plus a concurrency helper for later plans."""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import Awaitable, Callable

import asyncpg


async def make_drop(
    conn: asyncpg.Connection, *, capacity: int = 5, mode: str = "fifo", name: str = "Test drop"
) -> uuid.UUID:
    drop_id: uuid.UUID = await conn.fetchval(
        "SELECT create_drop($1, $2, $3, 60, 120, $4)", name, capacity, mode, "ab" * 32
    )
    return drop_id


async def make_user(conn: asyncpg.Connection) -> uuid.UUID:
    token = uuid.uuid4().hex
    user_id: uuid.UUID = await conn.fetchval(
        "INSERT INTO users (public_id, phone_hash) VALUES ($1, $2) RETURNING id",
        f"pub_{token[:12]}",
        token,
    )
    return user_id


async def make_entry(
    conn: asyncpg.Connection,
    drop_id: uuid.UUID,
    user_id: uuid.UUID | None = None,
    status: str = "REGISTERED",
    draw_rank: int | None = None,
) -> uuid.UUID:
    user_id = user_id or await make_user(conn)
    entry_id: uuid.UUID = await conn.fetchval(
        "INSERT INTO entries (drop_id, user_id, status, draw_rank, run_no)"
        " VALUES ($1, $2, $3, $4, 1) RETURNING id",
        drop_id,
        user_id,
        status,
        draw_rank,
    )
    return entry_id


async def seat_ids(conn: asyncpg.Connection, drop_id: uuid.UUID) -> list[int]:
    rows = await conn.fetch("SELECT id FROM seats WHERE drop_id = $1 ORDER BY seat_no", drop_id)
    return [r["id"] for r in rows]


async def sell_seat(conn: asyncpg.Connection, seat_id: int, entry_id: uuid.UUID) -> None:
    await conn.execute(
        "UPDATE seats SET status = 'sold', entry_id = $2, sold_at = now() WHERE id = $1",
        seat_id,
        entry_id,
    )


async def allocate(
    conn: asyncpg.Connection, drop_id: uuid.UUID, entry_id: uuid.UUID, seat_id: int
) -> None:
    await conn.execute(
        "INSERT INTO allocations (drop_id, entry_id, seat_id, idempotency_key, run_no)"
        " VALUES ($1, $2, $3, gen_random_uuid(), 1)",
        drop_id,
        entry_id,
        seat_id,
    )


async def claim(conn: asyncpg.Connection, drop_id: uuid.UUID) -> uuid.UUID:
    """A full, consistent claim: new entry takes the next free seat; entry ALLOCATED; ledger row."""
    entry_id = await make_entry(conn, drop_id)
    async with conn.transaction():
        seat_id = await conn.fetchval(
            "UPDATE seats SET status = 'sold', entry_id = $1, sold_at = now()"
            " WHERE id = (SELECT id FROM seats WHERE drop_id = $2 AND status = 'free'"
            "             ORDER BY seat_no FOR UPDATE SKIP LOCKED LIMIT 1) RETURNING id",
            entry_id,
            drop_id,
        )
        await conn.execute(
            "UPDATE entries SET status = 'ALLOCATED', allocated_at = now() WHERE id = $1", entry_id
        )
        await allocate(conn, drop_id, entry_id, seat_id)
    return entry_id


async def fire_concurrently[T](n: int, make_request: Callable[[int], Awaitable[T]]) -> list[T]:
    """Run make_request(0..n-1) so that all start together (released by one barrier event)."""
    start = asyncio.Event()

    async def runner(i: int) -> T:
        await start.wait()
        return await make_request(i)

    tasks = [asyncio.create_task(runner(i)) for i in range(n)]
    await asyncio.sleep(0)  # let every task reach the barrier
    start.set()
    return await asyncio.gather(*tasks)
