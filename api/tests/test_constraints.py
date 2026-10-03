"""Plan 02 section 5: the schema's constraints are the integrity wall.

Every test tries to create a bad state with raw SQL and expects the database to refuse it.
Where a refusal could come from a different constraint than intended, the constraint name is
asserted too.
"""

from __future__ import annotations

import asyncpg
import pytest

from tests.helpers import allocate, make_drop, make_entry, make_user, seat_ids, sell_seat


async def test_same_user_cannot_enter_a_drop_twice(db: asyncpg.Connection) -> None:
    drop_id = await make_drop(db)
    user_id = await make_user(db)
    await make_entry(db, drop_id, user_id)
    with pytest.raises(asyncpg.UniqueViolationError) as err:
        await make_entry(db, drop_id, user_id)
    assert err.value.constraint_name == "entries_drop_user_key"


async def test_same_user_can_enter_different_drops(db: asyncpg.Connection) -> None:
    user_id = await make_user(db)
    await make_entry(db, await make_drop(db), user_id)
    await make_entry(db, await make_drop(db), user_id)


async def test_one_entry_cannot_hold_two_seats(db: asyncpg.Connection) -> None:
    drop_id = await make_drop(db)
    entry_id = await make_entry(db, drop_id)
    first, second, *_ = await seat_ids(db, drop_id)
    await sell_seat(db, first, entry_id)
    with pytest.raises(asyncpg.UniqueViolationError) as err:
        await sell_seat(db, second, entry_id)
    assert err.value.constraint_name == "seats_drop_entry_key"


async def test_seat_cannot_hold_an_entry_of_another_drop(db: asyncpg.Connection) -> None:
    drop_a = await make_drop(db)
    drop_b = await make_drop(db)
    foreign_entry = await make_entry(db, drop_b)
    seat = (await seat_ids(db, drop_a))[0]
    with pytest.raises(asyncpg.ForeignKeyViolationError) as err:
        await sell_seat(db, seat, foreign_entry)
    assert err.value.constraint_name == "seats_entry_in_drop_fk"


async def test_sold_seat_needs_an_entry_and_free_seat_must_not_have_one(
    db: asyncpg.Connection,
) -> None:
    drop_id = await make_drop(db)
    entry_id = await make_entry(db, drop_id)
    seat = (await seat_ids(db, drop_id))[0]
    with pytest.raises(asyncpg.CheckViolationError) as err:
        await db.execute("UPDATE seats SET status = 'sold', sold_at = now() WHERE id = $1", seat)
    assert err.value.constraint_name == "seats_free_iff_no_entry"
    with pytest.raises(asyncpg.CheckViolationError) as err:
        await db.execute("UPDATE seats SET entry_id = $2 WHERE id = $1", seat, entry_id)
    assert err.value.constraint_name == "seats_free_iff_no_entry"


async def test_sold_at_is_set_exactly_when_sold(db: asyncpg.Connection) -> None:
    drop_id = await make_drop(db)
    entry_id = await make_entry(db, drop_id)
    seat = (await seat_ids(db, drop_id))[0]
    with pytest.raises(asyncpg.CheckViolationError) as err:
        await db.execute(
            "UPDATE seats SET status = 'sold', entry_id = $2 WHERE id = $1", seat, entry_id
        )
    assert err.value.constraint_name == "seats_sold_iff_sold_at"
    with pytest.raises(asyncpg.CheckViolationError) as err:
        await db.execute("UPDATE seats SET sold_at = now() WHERE id = $1", seat)
    assert err.value.constraint_name == "seats_sold_iff_sold_at"


async def test_seat_status_must_be_known(db: asyncpg.Connection) -> None:
    drop_id = await make_drop(db)
    entry_id = await make_entry(db, drop_id)
    seat = (await seat_ids(db, drop_id))[0]
    # entry set and sold_at NULL keep the two "iff" checks satisfied for an unknown status
    with pytest.raises(asyncpg.CheckViolationError) as err:
        await db.execute(
            "UPDATE seats SET status = 'held', entry_id = $2 WHERE id = $1", seat, entry_id
        )
    assert err.value.constraint_name == "seats_status_valid"


async def test_many_free_seats_with_null_entry_coexist(db: asyncpg.Connection) -> None:
    """NULLs are distinct in a unique constraint: all 500 free seats have entry_id NULL."""
    drop_id = await make_drop(db, capacity=500)
    free = await db.fetchval(
        "SELECT count(*) FROM seats WHERE drop_id = $1 AND entry_id IS NULL AND status = 'free'",
        drop_id,
    )
    assert free == 500


async def test_seats_unique_index_keeps_nulls_distinct(db: asyncpg.Connection) -> None:
    """Metadata check: the index was NOT created with NULLS NOT DISTINCT (that would allow only
    one free seat per drop)."""
    rows = await db.fetch(
        "SELECT c.conname, i.indnullsnotdistinct"
        " FROM pg_constraint c JOIN pg_index i ON i.indexrelid = c.conindid"
        " WHERE c.conrelid = 'seats'::regclass AND c.contype = 'u'"
    )
    flags = {r["conname"]: r["indnullsnotdistinct"] for r in rows}
    assert "seats_drop_entry_key" in flags
    assert not any(flags.values()), flags


async def test_two_sold_seats_with_the_same_entry_are_rejected_unlike_null(
    db: asyncpg.Connection,
) -> None:
    """Negative control for the NULL test: non-NULL duplicates are still caught."""
    drop_id = await make_drop(db)
    entry_id = await make_entry(db, drop_id)
    seats = await seat_ids(db, drop_id)
    await sell_seat(db, seats[0], entry_id)
    with pytest.raises(asyncpg.UniqueViolationError):
        await sell_seat(db, seats[1], entry_id)
    free = await db.fetchval(
        "SELECT count(*) FROM seats WHERE drop_id = $1 AND entry_id IS NULL", drop_id
    )
    assert free == len(seats) - 1


async def test_one_allocation_per_entry(db: asyncpg.Connection) -> None:
    drop_id = await make_drop(db)
    entry_id = await make_entry(db, drop_id)
    seat = (await seat_ids(db, drop_id))[0]
    await sell_seat(db, seat, entry_id)
    await allocate(db, drop_id, entry_id, seat)
    with pytest.raises(asyncpg.UniqueViolationError):
        await allocate(db, drop_id, entry_id, seat)


async def test_a_seat_cannot_be_allocated_to_a_second_entry(db: asyncpg.Connection) -> None:
    drop_id = await make_drop(db)
    first_entry = await make_entry(db, drop_id)
    second_entry = await make_entry(db, drop_id)
    seat = (await seat_ids(db, drop_id))[0]
    await sell_seat(db, seat, first_entry)
    await allocate(db, drop_id, first_entry, seat)
    # One ledger row per seat: refused by UNIQUE(seat_id) (and, were that absent, the composite FK
    # would refuse it because the seat row holds first_entry).
    with pytest.raises(asyncpg.UniqueViolationError) as err:
        await allocate(db, drop_id, second_entry, seat)
    assert err.value.constraint_name == "allocations_seat_id_key"


async def test_allocation_must_name_a_seat_that_holds_that_entry(db: asyncpg.Connection) -> None:
    drop_id = await make_drop(db)
    entry_id = await make_entry(db, drop_id)
    free_seat = (await seat_ids(db, drop_id))[0]
    with pytest.raises(asyncpg.ForeignKeyViolationError) as err:
        await allocate(db, drop_id, entry_id, free_seat)
    assert err.value.constraint_name == "allocations_seat_entry_fk"


async def test_allocation_drop_must_match_the_entry_drop(db: asyncpg.Connection) -> None:
    drop_a = await make_drop(db)
    drop_b = await make_drop(db)
    entry_id = await make_entry(db, drop_a)
    seat = (await seat_ids(db, drop_a))[0]
    await sell_seat(db, seat, entry_id)
    with pytest.raises(asyncpg.ForeignKeyViolationError) as err:
        await allocate(db, drop_b, entry_id, seat)
    assert err.value.constraint_name == "allocations_entry_in_drop_fk"


async def test_draw_rank_is_unique_per_drop_but_many_nulls_are_fine(db: asyncpg.Connection) -> None:
    drop_id = await make_drop(db)
    for _ in range(5):
        await make_entry(db, drop_id)  # five NULL ranks
    await make_entry(db, drop_id, draw_rank=1)
    with pytest.raises(asyncpg.UniqueViolationError) as err:
        await make_entry(db, drop_id, draw_rank=1)
    assert err.value.constraint_name == "entries_drop_draw_rank_key"
    other_drop = await make_drop(db)
    await make_entry(db, other_drop, draw_rank=1)  # same rank in another drop is fine


@pytest.mark.parametrize("rank", [0, -3])
async def test_draw_rank_must_be_positive(db: asyncpg.Connection, rank: int) -> None:
    drop_id = await make_drop(db)
    with pytest.raises(asyncpg.CheckViolationError) as err:
        await make_entry(db, drop_id, draw_rank=rank)
    assert err.value.constraint_name == "entries_draw_rank_positive"


async def test_entry_status_must_be_known(db: asyncpg.Connection) -> None:
    drop_id = await make_drop(db)
    with pytest.raises(asyncpg.CheckViolationError) as err:
        await make_entry(db, drop_id, status="WINNER")
    assert err.value.constraint_name == "entries_status_valid"


# --- structural seat cap (D-004): a drop can never have more seat rows than its capacity ---


@pytest.mark.parametrize("seat_no", [0, 6, 500])
async def test_seat_number_outside_capacity_is_rejected(
    db: asyncpg.Connection, seat_no: int
) -> None:
    drop_id = await make_drop(db, capacity=5)
    with pytest.raises(asyncpg.CheckViolationError) as err:
        await db.execute(
            "INSERT INTO seats (drop_id, capacity, seat_no, status) VALUES ($1, 5, $2, 'free')",
            drop_id,
            seat_no,
        )
    assert err.value.constraint_name == "seats_seat_no_within_capacity"


async def test_a_sixth_seat_row_cannot_be_inserted_for_a_capacity_five_drop(
    db: asyncpg.Connection,
) -> None:
    drop_id = await make_drop(db, capacity=5)
    for seat_no in range(1, 6):  # every number in range is already taken
        with pytest.raises(asyncpg.UniqueViolationError) as err:
            await db.execute(
                "INSERT INTO seats (drop_id, capacity, seat_no, status) VALUES ($1, 5, $2, 'free')",
                drop_id,
                seat_no,
            )
        assert err.value.constraint_name == "seats_drop_seat_no_key"
    assert await db.fetchval("SELECT count(*) FROM seats WHERE drop_id = $1", drop_id) == 5


async def test_seat_cannot_claim_a_different_capacity_than_its_drop(db: asyncpg.Connection) -> None:
    drop_id = await make_drop(db, capacity=5)
    await db.execute("DELETE FROM seats WHERE drop_id = $1 AND seat_no = 5", drop_id)
    with pytest.raises(asyncpg.ForeignKeyViolationError) as err:
        await db.execute(
            "INSERT INTO seats (drop_id, capacity, seat_no, status) VALUES ($1, 99, 5, 'free')",
            drop_id,
        )
    assert err.value.constraint_name == "seats_drop_capacity_fk"


async def test_capacity_cannot_change_while_seats_exist(db: asyncpg.Connection) -> None:
    drop_id = await make_drop(db, capacity=5)
    with pytest.raises(asyncpg.ForeignKeyViolationError):
        await db.execute("UPDATE drops SET capacity = 3 WHERE id = $1", drop_id)
    with pytest.raises(asyncpg.ForeignKeyViolationError):
        await db.execute("UPDATE drops SET capacity = 9 WHERE id = $1", drop_id)


async def test_drop_fields_are_validated(db: asyncpg.Connection) -> None:
    for capacity, mode, window in [(0, "fifo", 60), (5, "lottery", 60), (5, "fifo", 0)]:
        with pytest.raises(asyncpg.CheckViolationError):
            await db.fetchval(
                "SELECT create_drop('x', $1, $2, $3, 120, 'c')", capacity, mode, window
            )
    # nothing was left behind by the failed calls
    assert await db.fetchval("SELECT count(*) FROM drops") == 0
    assert await db.fetchval("SELECT count(*) FROM seats") == 0
