"""Constraint tests for the Plan 02 schema. Run against the real Postgres (uv run fd up first).

Every test runs inside one transaction that is rolled back, so the database stays clean.
Expected failures run in a savepoint so the outer transaction survives.
"""

import os
import uuid
from collections.abc import AsyncIterator

import asyncpg
import pytest

pytestmark = pytest.mark.asyncio(loop_scope="function")


def _dsn() -> str:
    return "postgresql://{u}:{p}@{h}:5432/{d}".format(
        u=os.environ.get("POSTGRES_USER", "fairdrop"),
        p=os.environ["POSTGRES_PASSWORD"],
        h=os.environ.get("POSTGRES_HOST", "postgres"),
        d=os.environ.get("POSTGRES_DB", "fairdrop"),
    )


@pytest.fixture
async def conn() -> AsyncIterator[asyncpg.Connection]:
    c = await asyncpg.connect(_dsn())
    tx = c.transaction()
    await tx.start()
    try:
        yield c
    finally:
        await tx.rollback()
        await c.close()


async def new_drop(c: asyncpg.Connection, capacity: int = 3) -> uuid.UUID:
    drop_id: uuid.UUID = await c.fetchval(
        "INSERT INTO drops (name, capacity, mode, phase, window_s, seed_commit) "
        "VALUES ('t', $1, 'fair', 'OPEN', 60, 'x') RETURNING id",
        capacity,
    )
    await c.execute("SELECT create_drop_seats($1)", drop_id)
    return drop_id


async def new_entry(
    c: asyncpg.Connection, drop_id: uuid.UUID, status: str = "REGISTERED"
) -> uuid.UUID:
    suffix = uuid.uuid4().hex
    user_id = await c.fetchval(
        "INSERT INTO users (public_id, phone_hash) VALUES ($1, $2) RETURNING id", suffix, suffix
    )
    entry_id: uuid.UUID = await c.fetchval(
        "INSERT INTO entries (drop_id, user_id, status) VALUES ($1, $2, $3) RETURNING id",
        drop_id,
        user_id,
        status,
    )
    return entry_id


async def sell(c: asyncpg.Connection, drop_id: uuid.UUID, entry_id: uuid.UUID) -> int:
    """What the allocation engine will do: sell one free seat and write the ledger row."""
    seat_id: int = await c.fetchval(
        "UPDATE seats SET status='sold', entry_id=$2, sold_at=now() WHERE id = "
        "(SELECT id FROM seats WHERE drop_id=$1 AND status='free' ORDER BY seat_no LIMIT 1) "
        "RETURNING id",
        drop_id,
        entry_id,
    )
    await c.execute(
        "INSERT INTO allocations (drop_id, entry_id, seat_id, idempotency_key) "
        "VALUES ($1, $2, $3, gen_random_uuid())",
        drop_id,
        entry_id,
        seat_id,
    )
    await c.execute("UPDATE entries SET status='ALLOCATED' WHERE id=$1", entry_id)
    return seat_id


async def fails(c: asyncpg.Connection, sql: str, *args: object) -> type[Exception]:
    """Run sql in a savepoint and return the Postgres error class it raised."""
    with pytest.raises(asyncpg.PostgresError) as info:
        async with c.transaction():
            await c.execute(sql, *args)
    return type(info.value)


async def test_same_user_cannot_enter_a_drop_twice(conn: asyncpg.Connection) -> None:
    drop_id = await new_drop(conn)
    user_id = await conn.fetchval(
        "INSERT INTO users (public_id, phone_hash) VALUES ('a','a') RETURNING id"
    )
    await conn.execute(
        "INSERT INTO entries (drop_id, user_id, status) VALUES ($1,$2,'REGISTERED')",
        drop_id,
        user_id,
    )
    err = await fails(
        conn,
        "INSERT INTO entries (drop_id, user_id, status) VALUES ($1,$2,'REGISTERED')",
        drop_id,
        user_id,
    )
    assert err is asyncpg.UniqueViolationError


async def test_one_entry_cannot_hold_two_seats(conn: asyncpg.Connection) -> None:
    drop_id = await new_drop(conn)
    entry_id = await new_entry(conn, drop_id)
    await sell(conn, drop_id, entry_id)
    err = await fails(
        conn,
        "UPDATE seats SET status='sold', entry_id=$2 WHERE drop_id=$1 AND seat_no=2",
        drop_id,
        entry_id,
    )
    assert err is asyncpg.UniqueViolationError


async def test_seat_status_and_entry_must_agree(conn: asyncpg.Connection) -> None:
    drop_id = await new_drop(conn)
    entry_id = await new_entry(conn, drop_id)
    err = await fails(
        conn, "UPDATE seats SET status='sold' WHERE drop_id=$1 AND seat_no=1", drop_id
    )
    assert err is asyncpg.CheckViolationError
    err = await fails(
        conn, "UPDATE seats SET entry_id=$2 WHERE drop_id=$1 AND seat_no=1", drop_id, entry_id
    )
    assert err is asyncpg.CheckViolationError


async def test_allocation_unique_per_entry_and_per_seat(conn: asyncpg.Connection) -> None:
    drop_id = await new_drop(conn)
    e1, e2 = await new_entry(conn, drop_id), await new_entry(conn, drop_id)
    seat1 = await sell(conn, drop_id, e1)
    insert = (
        "INSERT INTO allocations (drop_id, entry_id, seat_id, idempotency_key) "
        "VALUES ($1,$2,$3,gen_random_uuid())"
    )
    other_seat = await conn.fetchval(
        "SELECT id FROM seats WHERE drop_id=$1 AND status='free' LIMIT 1", drop_id
    )
    assert await fails(conn, insert, drop_id, e1, other_seat) is asyncpg.UniqueViolationError
    assert await fails(conn, insert, drop_id, e2, seat1) is asyncpg.UniqueViolationError


async def test_many_free_seats_with_null_entry_coexist(conn: asyncpg.Connection) -> None:
    drop_id = await new_drop(conn, capacity=500)
    free = await conn.fetchval(
        "SELECT count(*) FROM seats WHERE drop_id=$1 AND status='free' AND entry_id IS NULL",
        drop_id,
    )
    assert free == 500


async def test_draw_rank_unique_per_drop_but_null_ranks_ok(conn: asyncpg.Connection) -> None:
    drop_id = await new_drop(conn)
    e1, e2, e3 = [await new_entry(conn, drop_id) for _ in range(3)]  # all ranks NULL: fine
    await conn.execute("UPDATE entries SET draw_rank=1 WHERE id=$1", e1)
    assert (
        await fails(conn, "UPDATE entries SET draw_rank=1 WHERE id=$1", e2)
        is asyncpg.UniqueViolationError
    )
    await conn.execute("UPDATE entries SET draw_rank=2 WHERE id=$1", e3)


async def test_drop_and_seats_are_atomic() -> None:
    c = await asyncpg.connect(_dsn())
    try:
        drop_id = uuid.uuid4()
        with pytest.raises(RuntimeError):
            async with c.transaction():
                await c.execute(
                    "INSERT INTO drops (id, name, capacity, mode, phase, window_s, seed_commit) "
                    "VALUES ($1,'atomic',5,'fair','SCHEDULED',60,'x')",
                    drop_id,
                )
                await c.execute("SELECT create_drop_seats($1)", drop_id)
                raise RuntimeError("simulated failure mid-transaction")
        assert await c.fetchval("SELECT count(*) FROM drops WHERE id=$1", drop_id) == 0
        assert await c.fetchval("SELECT count(*) FROM seats WHERE drop_id=$1", drop_id) == 0
    finally:
        await c.close()


async def integrity(c: asyncpg.Connection, drop_id: uuid.UUID) -> asyncpg.Record:
    row = await c.fetchrow("SELECT * FROM v_drop_integrity WHERE drop_id=$1", drop_id)
    assert row is not None
    return row


async def test_integrity_view_ok_empty_and_fully_sold(conn: asyncpg.Connection) -> None:
    drop_id = await new_drop(conn, capacity=3)
    row = await integrity(conn, drop_id)
    assert row["invariant_ok"] and row["sold"] == 0 and row["free"] == 3
    for _ in range(3):
        await sell(conn, drop_id, await new_entry(conn, drop_id))
    row = await integrity(conn, drop_id)
    assert row["invariant_ok"] and row["sold"] == 3 and row["free"] == 0 and row["oversold"] == 0


async def test_integrity_view_flags_inconsistency(conn: asyncpg.Connection) -> None:
    drop_id = await new_drop(conn, capacity=3)
    entry_id = await new_entry(conn, drop_id)
    # A seat marked sold without an allocation row or an ALLOCATED entry.
    await conn.execute(
        "UPDATE seats SET status='sold', entry_id=$2 WHERE drop_id=$1 AND seat_no=1",
        drop_id,
        entry_id,
    )
    row = await integrity(conn, drop_id)
    assert not row["invariant_ok"] and row["sold_without_allocation"] == 1


async def test_reset_drop_frees_everything_and_bumps_run(conn: asyncpg.Connection) -> None:
    drop_id = await new_drop(conn, capacity=2)
    await sell(conn, drop_id, await new_entry(conn, drop_id))
    assert await conn.fetchval("SELECT admin_reset_drop($1)", drop_id) == 2
    row = await integrity(conn, drop_id)
    assert row["invariant_ok"] and row["sold"] == 0 and row["allocations_count"] == 0
    assert await conn.fetchval("SELECT count(*) FROM entries WHERE drop_id=$1", drop_id) == 0
