"""Plan 02 section 4.5 / test 9: v_drop_integrity.

Constraints make most bad states impossible, so to prove the view notices them the tests build
the bad state anyway, inside a transaction that is rolled back: the superuser connection turns
off FK enforcement (`session_replication_role = replica`) or drops one constraint (DDL is
transactional in PostgreSQL). UNIQUE and CHECK constraints stay on under `replica`, which is why
the cases they cover drop the constraint instead.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import asyncpg
import pytest

from tests.helpers import allocate, claim, make_drop, make_entry, seat_ids, sell_seat

REQUIRED_FIELDS = {
    "seats_total",
    "sold",
    "free",
    "capacity",
    "oversold",
    "duplicate_entries_with_seats",
    "allocations_count",
    "sold_without_allocation",
    "allocation_without_sold_seat",
    "entries_allocated_count",
    "entries_allocated_mismatch",
    "invariant_ok",
}
EXTRA_FIELDS = {"sold_seat_entry_not_allocated", "free_seat_with_sold_at"}


async def integrity(conn: asyncpg.Connection, drop_id: uuid.UUID) -> asyncpg.Record:
    row = await conn.fetchrow("SELECT * FROM v_drop_integrity WHERE drop_id = $1", drop_id)
    assert row is not None
    return row


@asynccontextmanager
async def broken_state(conn: asyncpg.Connection) -> AsyncIterator[None]:
    """A transaction with FK enforcement off that is always rolled back."""
    tx = conn.transaction()
    await tx.start()
    try:
        await conn.execute("SET LOCAL session_replication_role = replica")
        yield
    finally:
        await tx.rollback()


async def test_view_exposes_exactly_the_agreed_field_names(db: asyncpg.Connection) -> None:
    drop_id = await make_drop(db)
    row = await integrity(db, drop_id)
    assert set(row.keys()) == {"drop_id"} | REQUIRED_FIELDS | EXTRA_FIELDS


async def test_empty_drop_is_ok(db: asyncpg.Connection) -> None:
    row = await integrity(db, await make_drop(db, capacity=500))
    assert (row["seats_total"], row["sold"], row["free"], row["capacity"]) == (500, 0, 500, 500)
    assert row["oversold"] == row["allocations_count"] == row["entries_allocated_count"] == 0
    assert row["invariant_ok"] is True


async def test_fully_sold_drop_is_ok(db: asyncpg.Connection) -> None:
    drop_id = await make_drop(db, capacity=20)
    for _ in range(20):
        await claim(db, drop_id)
    row = await integrity(db, drop_id)
    assert (row["sold"], row["free"], row["allocations_count"], row["entries_allocated_count"]) == (
        20,
        0,
        20,
        20,
    )
    assert row["oversold"] == row["duplicate_entries_with_seats"] == 0
    assert row["invariant_ok"] is True


async def test_counts_are_not_multiplied_by_other_tables(db: asyncpg.Connection) -> None:
    """No fan-out: lots of entries and several sold seats must not inflate any count."""
    drop_id = await make_drop(db, capacity=10)
    other = await make_drop(db, capacity=10)
    for _ in range(3):
        await claim(db, drop_id)
    for _ in range(40):
        await make_entry(db, drop_id)  # REGISTERED, hold nothing
    for _ in range(7):
        await claim(db, other)
    row = await integrity(db, drop_id)
    assert (row["seats_total"], row["sold"], row["free"]) == (10, 3, 7)
    assert (row["allocations_count"], row["entries_allocated_count"]) == (3, 3)
    assert row["invariant_ok"] is True
    assert (await integrity(db, other))["sold"] == 7


async def test_drop_without_seat_rows_is_not_ok(db: asyncpg.Connection) -> None:
    """NULL safety: a drop with no seat rows must read as broken, not pass by accident."""
    drop_id = await db.fetchval(
        "INSERT INTO drops (name, capacity, mode, phase, window_s, seed_commit)"
        " VALUES ('bare', 500, 'fifo', 'SCHEDULED', 60, 'c') RETURNING id"
    )
    row = await integrity(db, drop_id)
    assert (row["seats_total"], row["capacity"]) == (0, 500)
    assert row["invariant_ok"] is False


async def test_sold_seat_without_allocation_is_flagged(db: asyncpg.Connection) -> None:
    drop_id = await make_drop(db, capacity=5)
    entry_id = await make_entry(db, drop_id, status="ALLOCATED")
    await sell_seat(db, (await seat_ids(db, drop_id))[0], entry_id)  # no ledger row
    row = await integrity(db, drop_id)
    assert row["sold_without_allocation"] == 1
    assert row["invariant_ok"] is False


async def test_allocated_entry_holding_no_seat_is_flagged(db: asyncpg.Connection) -> None:
    drop_id = await make_drop(db, capacity=5)
    await make_entry(db, drop_id, status="ALLOCATED")
    row = await integrity(db, drop_id)
    assert row["entries_allocated_count"] == 1
    assert row["entries_allocated_mismatch"] == 1
    assert row["invariant_ok"] is False


async def test_seat_sold_to_an_entry_that_is_not_allocated_is_flagged(
    db: asyncpg.Connection,
) -> None:
    drop_id = await make_drop(db, capacity=5)
    entry_id = await make_entry(db, drop_id, status="REGISTERED")
    seat = (await seat_ids(db, drop_id))[0]
    await sell_seat(db, seat, entry_id)
    await allocate(db, drop_id, entry_id, seat)  # consistent ledger, wrong entry status
    row = await integrity(db, drop_id)
    assert row["sold_seat_entry_not_allocated"] == 1
    assert row["entries_allocated_mismatch"] == 1
    assert row["invariant_ok"] is False


async def test_allocation_pointing_at_a_free_seat_is_flagged(db: asyncpg.Connection) -> None:
    drop_id = await make_drop(db, capacity=5)
    entry_id = await make_entry(db, drop_id, status="ALLOCATED")
    free_seat = (await seat_ids(db, drop_id))[0]
    async with broken_state(db):  # the composite FK normally refuses this row
        await allocate(db, drop_id, entry_id, free_seat)
        row = await integrity(db, drop_id)
    assert row["allocation_without_sold_seat"] == 1
    assert row["allocations_count"] == 1
    assert row["invariant_ok"] is False


async def test_oversold_is_flagged_when_capacity_is_shrunk_under_sold_seats(
    db: asyncpg.Connection,
) -> None:
    drop_id = await make_drop(db, capacity=5)
    for _ in range(5):
        await claim(db, drop_id)
    async with broken_state(db):  # the capacity FK normally refuses this change
        await db.execute("UPDATE drops SET capacity = 3 WHERE id = $1", drop_id)
        row = await integrity(db, drop_id)
    assert row["sold"] == 5 and row["capacity"] == 3
    assert row["oversold"] == 2
    assert row["invariant_ok"] is False


async def test_extra_seat_row_is_flagged(db: asyncpg.Connection) -> None:
    drop_id = await make_drop(db, capacity=5)
    tx = db.transaction()
    await tx.start()
    try:
        # D-004 normally makes this impossible; drop the constraints to prove the view still notices
        await db.execute("ALTER TABLE seats DROP CONSTRAINT seats_seat_no_within_capacity")
        await db.execute("ALTER TABLE seats DROP CONSTRAINT seats_drop_capacity_fk")
        await db.execute(
            "INSERT INTO seats (drop_id, capacity, seat_no, status) VALUES ($1, 5, 6, 'free')",
            drop_id,
        )
        row = await integrity(db, drop_id)
    finally:
        await tx.rollback()
    assert (row["seats_total"], row["capacity"]) == (6, 5)
    assert row["invariant_ok"] is False


async def test_entry_holding_two_seats_is_flagged(db: asyncpg.Connection) -> None:
    drop_id = await make_drop(db, capacity=5)
    entry_id = await make_entry(db, drop_id, status="ALLOCATED")
    first, second, *_ = await seat_ids(db, drop_id)
    tx = db.transaction()
    await tx.start()
    try:
        await db.execute("ALTER TABLE seats DROP CONSTRAINT seats_drop_entry_key")
        await sell_seat(db, first, entry_id)
        await sell_seat(db, second, entry_id)
        row = await integrity(db, drop_id)
    finally:
        await tx.rollback()
    assert row["duplicate_entries_with_seats"] == 1
    assert row["invariant_ok"] is False


async def test_free_seat_with_sold_at_is_flagged(db: asyncpg.Connection) -> None:
    drop_id = await make_drop(db, capacity=5)
    seat = (await seat_ids(db, drop_id))[0]
    tx = db.transaction()
    await tx.start()
    try:
        await db.execute("ALTER TABLE seats DROP CONSTRAINT seats_sold_iff_sold_at")
        await db.execute("UPDATE seats SET sold_at = now() WHERE id = $1", seat)
        row = await integrity(db, drop_id)
    finally:
        await tx.rollback()
    assert row["free_seat_with_sold_at"] == 1
    assert row["invariant_ok"] is False


async def test_seat_holding_an_entry_of_another_drop_is_flagged(db: asyncpg.Connection) -> None:
    drop_a = await make_drop(db, capacity=5)
    drop_b = await make_drop(db, capacity=5)
    foreign = await make_entry(db, drop_b, status="ALLOCATED")
    seat = (await seat_ids(db, drop_a))[0]
    async with broken_state(db):  # seats_entry_in_drop_fk normally refuses this
        await sell_seat(db, seat, foreign)
        row = await integrity(db, drop_a)
    # the foreign entry is not an ALLOCATED entry of *this* drop
    assert row["sold_seat_entry_not_allocated"] == 1
    assert row["invariant_ok"] is False


@pytest.mark.parametrize("capacity", [1, 2, 7])
async def test_a_view_check_never_passes_by_accident(db: asyncpg.Connection, capacity: int) -> None:
    """After selling everything, one inconsistency in any direction turns invariant_ok off."""
    drop_id = await make_drop(db, capacity=capacity)
    for _ in range(capacity):
        await claim(db, drop_id)
    assert (await integrity(db, drop_id))["invariant_ok"] is True
    async with broken_state(db):
        await db.execute(
            "UPDATE entries SET status = 'NOT_SELECTED' WHERE id = (SELECT entry_id FROM seats"
            " WHERE drop_id = $1 LIMIT 1)",
            drop_id,
        )
        row = await integrity(db, drop_id)
    assert row["invariant_ok"] is False
