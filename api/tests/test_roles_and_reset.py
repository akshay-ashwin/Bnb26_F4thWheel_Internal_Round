"""Plan 02 sections 4.4 and 4.6: the application role, the append-only ledger, atomic drop
creation, and the admin reset routine."""

from __future__ import annotations

import asyncpg
import pytest

from tests.helpers import allocate, claim, make_drop, make_entry, make_user, seat_ids, sell_seat

APP_ROLE = "fairdrop_app"


async def test_app_role_is_a_login_role_without_superuser_rights(
    app_db: asyncpg.Connection,
) -> None:
    row = await app_db.fetchrow(
        "SELECT rolsuper, rolcreatedb, rolcreaterole, rolbypassrls FROM pg_roles"
        " WHERE rolname = current_user"
    )
    assert await app_db.fetchval("SELECT current_user") == APP_ROLE
    assert not any(row.values()), dict(row)


async def test_app_role_cannot_run_ddl(app_db: asyncpg.Connection) -> None:
    with pytest.raises(asyncpg.InsufficientPrivilegeError):
        await app_db.execute("CREATE TABLE sneaky (id int)")
    with pytest.raises(asyncpg.InsufficientPrivilegeError):
        await app_db.execute("ALTER TABLE allocations DISABLE TRIGGER ALL")


async def test_app_role_cannot_update_or_delete_or_truncate_allocations(
    db: asyncpg.Connection, app_db: asyncpg.Connection
) -> None:
    drop_id = await make_drop(db)
    await claim(db, drop_id)
    with pytest.raises(asyncpg.InsufficientPrivilegeError):
        await app_db.execute("UPDATE allocations SET run_no = 2")
    with pytest.raises(asyncpg.InsufficientPrivilegeError):
        await app_db.execute("DELETE FROM allocations")
    with pytest.raises(asyncpg.InsufficientPrivilegeError):
        await app_db.execute("TRUNCATE allocations")
    assert await db.fetchval("SELECT count(*) FROM allocations") == 1


async def test_app_role_can_append_to_the_ledger(
    db: asyncpg.Connection, app_db: asyncpg.Connection
) -> None:
    drop_id = await make_drop(db)
    entry_id = await make_entry(db, drop_id)
    seat = (await seat_ids(db, drop_id))[0]
    async with app_db.transaction():
        await app_db.execute(
            "UPDATE seats SET status = 'sold', entry_id = $2, sold_at = now() WHERE id = $1",
            seat,
            entry_id,
        )
        await app_db.execute("UPDATE entries SET status = 'ALLOCATED' WHERE id = $1", entry_id)
        await allocate(app_db, drop_id, entry_id, seat)
    assert await db.fetchval(
        "SELECT invariant_ok FROM v_drop_integrity WHERE drop_id = $1", drop_id
    )


async def test_ledger_trigger_blocks_the_owner_too(db: asyncpg.Connection) -> None:
    """Even the schema owner (a superuser here) cannot rewrite the ledger outside the reset."""
    drop_id = await make_drop(db)
    await claim(db, drop_id)
    for statement in (
        "UPDATE allocations SET run_no = 2",
        "DELETE FROM allocations",
        "TRUNCATE allocations",
    ):
        with pytest.raises(asyncpg.IntegrityConstraintViolationError, match="append-only"):
            await db.execute(statement)
    assert await db.fetchval("SELECT count(*) FROM allocations") == 1


async def test_reset_flag_is_refused_for_the_app_role_even_with_the_privilege(
    db: asyncpg.Connection, app_db: asyncpg.Connection
) -> None:
    """The trigger checks who is asking, not only the flag. We grant DELETE to the app role for
    this test (as owner), set the flag as the app role, and still get refused."""
    drop_id = await make_drop(db)
    await claim(db, drop_id)
    await db.execute(f"GRANT DELETE ON allocations TO {APP_ROLE}")
    try:
        async with app_db.transaction():
            await app_db.execute("SET LOCAL fairdrop.resetting = 'on'")
            with pytest.raises(asyncpg.IntegrityConstraintViolationError, match="append-only"):
                await app_db.execute("DELETE FROM allocations")
    finally:
        await db.execute(f"REVOKE DELETE ON allocations FROM {APP_ROLE}")
    assert await db.fetchval("SELECT count(*) FROM allocations") == 1


async def test_app_role_cannot_create_seats_or_drops_directly(
    db: asyncpg.Connection, app_db: asyncpg.Connection
) -> None:
    drop_id = await make_drop(db, capacity=5)
    with pytest.raises(asyncpg.InsufficientPrivilegeError):
        await app_db.execute(
            "INSERT INTO seats (drop_id, capacity, seat_no, status) VALUES ($1, 5, 5, 'free')",
            drop_id,
        )
    with pytest.raises(asyncpg.InsufficientPrivilegeError):
        await app_db.execute(
            "INSERT INTO drops (name, capacity, mode, phase, window_s, seed_commit)"
            " VALUES ('x', 5, 'fifo', 'SCHEDULED', 60, 'c')"
        )


async def test_app_role_cannot_delete_entries_or_seats_or_change_capacity(
    db: asyncpg.Connection, app_db: asyncpg.Connection
) -> None:
    drop_id = await make_drop(db)
    await make_entry(db, drop_id)
    with pytest.raises(asyncpg.InsufficientPrivilegeError):
        await app_db.execute("DELETE FROM entries")
    with pytest.raises(asyncpg.InsufficientPrivilegeError):
        await app_db.execute("DELETE FROM seats")
    with pytest.raises(asyncpg.InsufficientPrivilegeError):
        await app_db.execute("UPDATE drops SET capacity = capacity")
    with pytest.raises(asyncpg.InsufficientPrivilegeError):
        await app_db.execute("UPDATE seats SET seat_no = seat_no")
    with pytest.raises(asyncpg.InsufficientPrivilegeError):
        await app_db.execute("UPDATE drops SET run_no = run_no + 1")


async def test_security_definer_functions_have_a_pinned_search_path_and_no_public_execute(
    db: asyncpg.Connection,
) -> None:
    rows = await db.fetch(
        "SELECT proname, prosecdef, proconfig,"
        "       (SELECT bool_or(grantee = 0) FROM aclexplode(proacl)) AS public_can_execute"
        " FROM pg_proc WHERE proname IN ('create_drop', 'admin_reset_drop')"
    )
    assert {r["proname"] for r in rows} == {"create_drop", "admin_reset_drop"}
    for r in rows:
        assert r["prosecdef"] is True
        assert any(c.startswith("search_path=") for c in r["proconfig"]), r["proconfig"]
        assert r["public_can_execute"] is False, r["proname"]


async def test_function_is_executable_by_the_app_role_only_through_the_grant(
    db: asyncpg.Connection,
) -> None:
    for fn in (
        "create_drop(text,int,text,int,int,text,text,timestamptz,timestamptz)",
        "admin_reset_drop(uuid)",
    ):
        assert await db.fetchval("SELECT has_function_privilege($1, $2, 'EXECUTE')", APP_ROLE, fn)


# --- atomic creation ---


async def test_app_role_creates_a_drop_with_all_its_seats(app_db: asyncpg.Connection) -> None:
    drop_id = await make_drop(app_db, capacity=500)
    row = await app_db.fetchrow(
        "SELECT seats_total, free, capacity, invariant_ok FROM v_drop_integrity WHERE drop_id = $1",
        drop_id,
    )
    assert (row["seats_total"], row["free"], row["capacity"], row["invariant_ok"]) == (
        500,
        500,
        500,
        True,
    )


async def test_failure_while_creating_seats_leaves_no_drop_and_no_seats(
    db: asyncpg.Connection,
) -> None:
    """Fault injection: a trigger makes the third seat insert fail. The whole create_drop call
    must roll back, including the drops row it had already inserted."""
    await db.execute(
        "CREATE FUNCTION test_boom() RETURNS trigger LANGUAGE plpgsql AS $$"
        " BEGIN IF NEW.seat_no = 3 THEN RAISE EXCEPTION 'injected failure at seat 3'; END IF;"
        " RETURN NEW; END $$"
    )
    await db.execute(
        "CREATE TRIGGER test_boom BEFORE INSERT ON seats FOR EACH ROW EXECUTE FUNCTION test_boom()"
    )
    try:
        with pytest.raises(asyncpg.RaiseError, match="injected failure"):
            await make_drop(db, capacity=5)
    finally:
        await db.execute("DROP TRIGGER test_boom ON seats")
        await db.execute("DROP FUNCTION test_boom()")
    assert await db.fetchval("SELECT count(*) FROM drops") == 0
    assert await db.fetchval("SELECT count(*) FROM seats") == 0
    # and creation works again once the fault is gone
    assert await make_drop(db, capacity=5)


# --- reset ---


async def test_admin_reset_clears_the_run_and_keeps_users_sessions_and_history(
    db: asyncpg.Connection, app_db: asyncpg.Connection
) -> None:
    drop_id = await make_drop(db, capacity=3)
    other_drop = await make_drop(db, capacity=3)
    await claim(db, drop_id)
    await claim(db, drop_id)
    await make_entry(db, drop_id)
    other_entry = await claim(db, other_drop)
    user_id = await make_user(db)
    await db.execute("INSERT INTO sessions (user_id, device_id) VALUES ($1, 'd1')", user_id)
    await db.execute(
        "INSERT INTO drop_runs (drop_id, run_no, mode) VALUES ($1, 1, 'fifo')", drop_id
    )
    await db.execute(
        "INSERT INTO idempotency_records (user_id, key, drop_id, endpoint, request_hash, response,"
        " status_code) VALUES ($1, gen_random_uuid(), $2, 'claim', 'h', '{}', 200)",
        user_id,
        drop_id,
    )

    new_run = await app_db.fetchval("SELECT admin_reset_drop($1)", drop_id)

    assert new_run == 2
    assert await db.fetchval("SELECT run_no FROM drops WHERE id = $1", drop_id) == 2
    assert await db.fetchval("SELECT count(*) FROM allocations WHERE drop_id = $1", drop_id) == 0
    assert await db.fetchval("SELECT count(*) FROM entries WHERE drop_id = $1", drop_id) == 0
    assert (
        await db.fetchval("SELECT count(*) FROM idempotency_records WHERE drop_id = $1", drop_id)
        == 0
    )
    free = await db.fetchval(
        "SELECT count(*) FROM seats WHERE drop_id = $1 AND status = 'free' AND entry_id IS NULL"
        " AND sold_at IS NULL",
        drop_id,
    )
    assert free == 3
    assert await db.fetchval(
        "SELECT invariant_ok FROM v_drop_integrity WHERE drop_id = $1", drop_id
    )
    # kept
    assert await db.fetchval("SELECT count(*) FROM users") >= 1
    assert await db.fetchval("SELECT count(*) FROM sessions") == 1
    assert await db.fetchval("SELECT count(*) FROM drop_runs") == 1
    # the other drop is untouched
    assert await db.fetchval("SELECT count(*) FROM allocations WHERE drop_id = $1", other_drop) == 1
    assert await db.fetchval("SELECT status FROM entries WHERE id = $1", other_entry) == "ALLOCATED"
    assert await db.fetchval(
        "SELECT invariant_ok FROM v_drop_integrity WHERE drop_id = $1", other_drop
    )


async def test_a_reset_drop_can_be_sold_out_again(db: asyncpg.Connection) -> None:
    drop_id = await make_drop(db, capacity=2)
    await claim(db, drop_id)
    await db.fetchval("SELECT admin_reset_drop($1)", drop_id)
    await claim(db, drop_id)
    await claim(db, drop_id)
    row = await db.fetchrow(
        "SELECT sold, free, invariant_ok FROM v_drop_integrity WHERE drop_id=$1", drop_id
    )
    assert (row["sold"], row["free"], row["invariant_ok"]) == (2, 0, True)


async def test_reset_flag_does_not_leak_after_the_reset(db: asyncpg.Connection) -> None:
    drop_id = await make_drop(db)
    await claim(db, drop_id)
    await db.fetchval("SELECT admin_reset_drop($1)", drop_id)
    await claim(db, drop_id)
    with pytest.raises(asyncpg.IntegrityConstraintViolationError, match="append-only"):
        await db.execute("DELETE FROM allocations")


async def test_reset_of_unknown_drop_fails_cleanly(db: asyncpg.Connection) -> None:
    with pytest.raises(asyncpg.NoDataFoundError):
        await db.fetchval("SELECT admin_reset_drop(gen_random_uuid())")


async def test_selling_requires_the_seat_to_be_taken_before_the_ledger_row(
    db: asyncpg.Connection,
) -> None:
    """Order inside the claim transaction: seat first, then ledger row (the FK needs the seat)."""
    drop_id = await make_drop(db)
    entry_id = await make_entry(db, drop_id)
    seat = (await seat_ids(db, drop_id))[0]
    with pytest.raises(asyncpg.ForeignKeyViolationError):
        await allocate(db, drop_id, entry_id, seat)
    await sell_seat(db, seat, entry_id)
    await allocate(db, drop_id, entry_id, seat)
