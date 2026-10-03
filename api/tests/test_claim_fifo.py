"""Chunk 4: the FIFO allocation engine (baseline mode) under concurrency, on real Postgres.

The invariants checked after every storm: allocated <= capacity, allocation ids and seat numbers
unique, every sold seat has exactly one ledger row, and `v_drop_integrity` says invariant_ok.
"""

from __future__ import annotations

import asyncio
import time
import uuid

import asyncpg
import httpx

from app.config import Settings
from app.security import encode_token, sid_hash
from tests.app_helpers import (
    TestUser,
    claim_with,
    enter_all,
    login,
    make_user,
    make_users,
    percentiles,
    strip_volatile,
    token_of,
    tokens_of,
)
from tests.helpers import make_drop, open_drop


async def _fifo(db: asyncpg.Connection, capacity: int = 500) -> str:
    drop_id = await make_drop(db, capacity=capacity, mode="fifo")
    await open_drop(db, drop_id)
    return str(drop_id)


async def _assert_integrity(db: asyncpg.Connection, drop: str, *, sold: int) -> None:
    row = await db.fetchrow("SELECT * FROM v_drop_integrity WHERE drop_id = $1", uuid.UUID(drop))
    assert row["invariant_ok"] is True, dict(row)
    assert (row["sold"], row["oversold"], row["duplicate_entries_with_seats"]) == (sold, 0, 0)
    assert row["allocations_count"] == sold == row["entries_allocated_count"]
    ids = await db.fetch("SELECT id, seat_id FROM allocations WHERE drop_id = $1", uuid.UUID(drop))
    assert len({r["id"] for r in ids}) == len({r["seat_id"] for r in ids}) == sold
    seats = await db.fetch(
        "SELECT seat_no FROM seats WHERE drop_id = $1 AND status = 'sold'", uuid.UUID(drop)
    )
    assert len({r["seat_no"] for r in seats}) == sold


async def _storm(
    client: httpx.AsyncClient,
    db: asyncpg.Connection,
    settings: Settings,
    *,
    capacity: int,
    claimants: int,
) -> tuple[str, list[TestUser], list[httpx.Response], list[float]]:
    drop = await _fifo(db, capacity)
    users = await make_users(db, settings, claimants)
    assert {r.status_code for r in await enter_all(client, users, drop)} == {201}
    tokens = await tokens_of(client, users, drop)
    assert all(tokens)
    latencies: list[float] = []

    async def one(u: TestUser, t: str) -> httpx.Response:
        started = time.perf_counter()
        r = await claim_with(client, u, drop, t)
        latencies.append(time.perf_counter() - started)
        return r

    rs = await asyncio.gather(*[one(u, t) for u, t in zip(users, tokens, strict=True) if t])
    return drop, users, list(rs), latencies


# --- single user ---


async def test_one_user_claims_a_seat(client: httpx.AsyncClient, db: asyncpg.Connection) -> None:
    drop = await _fifo(db, 5)
    token, _ = await login(client)
    h = {"Authorization": f"Bearer {token}"}
    assert (await client.post(f"/api/drops/{drop}/entries", headers=h)).status_code == 201
    me = (await client.get(f"/api/drops/{drop}/me", headers=h)).json()
    assert me["entry"]["status"] == "REGISTERED" and me["entry"]["admission_token"]
    r = await client.post(
        f"/api/drops/{drop}/claim",
        json={"admission_token": me["entry"]["admission_token"]},
        headers={**h, "Idempotency-Key": str(uuid.uuid4())},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert (
        body["seat_no"] == 1
        and body["allocation_id"]
        and body["confirmed_at"]
        and "server_time" in body
    )
    after = (await client.get(f"/api/drops/{drop}/me", headers=h)).json()
    assert after["entry"]["status"] == "ALLOCATED" and after["entry"]["admission_token"] is None
    assert after["allocation"] == {k: body[k] for k in ("allocation_id", "seat_no", "confirmed_at")}
    await _assert_integrity(db, drop, sold=1)


async def test_seats_are_given_out_in_claim_order(
    client: httpx.AsyncClient, db: asyncpg.Connection, settings: Settings
) -> None:
    drop = await _fifo(db, 5)
    users = await make_users(db, settings, 3)
    await enter_all(client, users, drop)
    seats = []
    for u in users:
        t = await token_of(client, u, drop)
        assert t
        seats.append((await claim_with(client, u, drop, t)).json()["seat_no"])
    assert seats == [1, 2, 3]


async def test_claim_needs_a_key_a_valid_token_and_an_entry(
    client: httpx.AsyncClient, db: asyncpg.Connection, settings: Settings
) -> None:
    drop = await _fifo(db, 5)
    user = await make_user(db, settings)
    await client.post(f"/api/drops/{drop}/entries", headers=user.headers)
    token = await token_of(client, user, drop)
    no_key = await client.post(
        f"/api/drops/{drop}/claim", json={"admission_token": token}, headers=user.headers
    )
    assert no_key.status_code == 400 and no_key.json()["error"]["code"] == "IDEMPOTENCY_KEY_MISSING"
    junk = await claim_with(client, user, drop, "not.a.token")
    assert junk.status_code == 401 and junk.json()["error"]["code"] == "TOKEN_INVALID"
    # a correctly signed token for an entry that does not exist -> no offer
    ghost = encode_token(
        settings.token_signing_key,
        {
            "drop_id": drop,
            "entry_id": str(uuid.uuid4()),
            "sid_hash": sid_hash(user.session_id),
            "jti": "x",
            "iat": int(time.time()),
            "exp": int(time.time()) + 60,
            "run": 1,
        },
    )
    r = await claim_with(client, user, drop, ghost)
    assert r.status_code == 403 and r.json()["error"]["code"] == "NOT_OFFERED"
    assert await db.fetchval("SELECT count(*) FROM allocations") == 0


async def test_a_user_without_an_entry_has_no_token(
    client: httpx.AsyncClient, db: asyncpg.Connection, settings: Settings
) -> None:
    drop = await _fifo(db, 5)
    user = await make_user(db, settings)
    me = (await client.get(f"/api/drops/{drop}/me", headers=user.headers)).json()
    assert me["entry"] is None


# --- idempotency and replays ---


async def test_same_key_new_key_and_replayed_token_return_the_same_seat(
    client: httpx.AsyncClient, db: asyncpg.Connection, settings: Settings
) -> None:
    drop = await _fifo(db, 5)
    user = await make_user(db, settings)
    await client.post(f"/api/drops/{drop}/entries", headers=user.headers)
    token = await token_of(client, user, drop)
    key = str(uuid.uuid4())
    first = await claim_with(client, user, drop, token, key)
    same_key = await claim_with(client, user, drop, token, key)
    new_key = await claim_with(client, user, drop, token, str(uuid.uuid4()))
    other_token = await claim_with(
        client,
        user,
        drop,
        encode_token(
            settings.token_signing_key,
            {
                "drop_id": drop,
                "entry_id": (await db.fetchval("SELECT id FROM entries"))
                and str(await db.fetchval("SELECT id FROM entries")),
                "sid_hash": sid_hash(user.session_id),
                "jti": "other",
                "iat": int(time.time()),
                "exp": int(time.time()) + 60,
                "run": 1,
            },
        ),
        str(uuid.uuid4()),
    )
    assert [r.status_code for r in (first, same_key, new_key, other_token)] == [200] * 4
    bodies = [strip_volatile(r.json()) for r in (first, same_key, new_key, other_token)]
    assert bodies[0] == bodies[1] == bodies[2] == bodies[3]
    await _assert_integrity(db, drop, sold=1)


async def test_same_key_three_times_at_once_is_one_write(
    client: httpx.AsyncClient, db: asyncpg.Connection, settings: Settings
) -> None:
    drop = await _fifo(db, 5)
    user = await make_user(db, settings)
    await client.post(f"/api/drops/{drop}/entries", headers=user.headers)
    token = await token_of(client, user, drop)
    key = str(uuid.uuid4())
    rs = await asyncio.gather(*[claim_with(client, user, drop, token, key) for _ in range(3)])
    assert {r.status_code for r in rs} == {200}
    assert len({r.json()["allocation_id"] for r in rs}) == 1
    await _assert_integrity(db, drop, sold=1)


async def test_a_key_reused_for_another_entry_is_422(
    client: httpx.AsyncClient, db: asyncpg.Connection, settings: Settings
) -> None:
    drop_a, drop_b = await _fifo(db, 5), await _fifo(db, 5)
    user = await make_user(db, settings)
    for d in (drop_a, drop_b):
        await client.post(f"/api/drops/{d}/entries", headers=user.headers)
    key = str(uuid.uuid4())
    assert (
        await claim_with(client, user, drop_a, await token_of(client, user, drop_a), key)
    ).status_code == 200
    r = await claim_with(client, user, drop_b, await token_of(client, user, drop_b), key)
    assert r.status_code == 422 and r.json()["error"]["code"] == "IDEMPOTENCY_KEY_REUSED"


async def test_a_token_from_another_session_is_rejected(
    client: httpx.AsyncClient, db: asyncpg.Connection, settings: Settings
) -> None:
    drop = await _fifo(db, 5)
    victim, thief = await make_user(db, settings), await make_user(db, settings)
    await enter_all(client, [victim, thief], drop)
    stolen = await token_of(client, victim, drop)
    r = await claim_with(client, thief, drop, stolen)
    assert r.status_code == 401 and r.json()["error"]["code"] == "TOKEN_INVALID"
    assert await token_of(client, thief, drop)  # the thief's own entry is untouched
    assert await db.fetchval("SELECT count(*) FROM allocations") == 0


# --- concurrency ---


async def test_last_seat_race_has_exactly_one_winner(
    client: httpx.AsyncClient, db: asyncpg.Connection, settings: Settings
) -> None:
    drop, _, rs, _ = await _storm(client, db, settings, capacity=1, claimants=50)
    codes = sorted(r.status_code for r in rs)
    assert codes.count(200) == 1 and codes.count(409) == 49
    assert {r.json()["error"]["code"] for r in rs if r.status_code == 409} == {"SOLD_OUT"}
    await _assert_integrity(db, drop, sold=1)


async def test_100_concurrent_claims_all_get_distinct_seats(
    client: httpx.AsyncClient, db: asyncpg.Connection, settings: Settings
) -> None:
    drop, _, rs, lat = await _storm(client, db, settings, capacity=500, claimants=100)
    assert {r.status_code for r in rs} == {200}
    assert len({r.json()["seat_no"] for r in rs}) == 100
    await _assert_integrity(db, drop, sold=100)
    print(f"\n[100 claims / 500 seats: {percentiles(lat)}]")


async def test_500_concurrent_claims_fill_exactly_500_seats(
    client: httpx.AsyncClient, db: asyncpg.Connection, settings: Settings
) -> None:
    drop, _, rs, lat = await _storm(client, db, settings, capacity=500, claimants=500)
    assert {r.status_code for r in rs} == {200}
    assert sorted(r.json()["seat_no"] for r in rs) == list(range(1, 501))
    await _assert_integrity(db, drop, sold=500)
    print(f"\n[500 claims / 500 seats: {percentiles(lat)}]")


async def test_1000_concurrent_claims_against_500_seats_never_oversell(
    client: httpx.AsyncClient, db: asyncpg.Connection, settings: Settings
) -> None:
    drop, users, rs, lat = await _storm(client, db, settings, capacity=500, claimants=1000)
    ok = [r for r in rs if r.status_code == 200]
    sold_out = [r for r in rs if r.status_code == 409]
    assert len(ok) == 500 and len(sold_out) == 500, (len(ok), len(sold_out))
    assert {r.json()["error"]["code"] for r in sold_out} == {"SOLD_OUT"}
    assert len({r.json()["seat_no"] for r in ok}) == 500
    await _assert_integrity(db, drop, sold=500)
    # FIFO finish: the last claim moved the drop to DONE and the losers became NOT_SELECTED
    assert await db.fetchval("SELECT phase FROM drops WHERE id = $1", uuid.UUID(drop)) == "DONE"
    statuses: dict[str, int] = {}
    for _ in range(40):  # the finishing task runs in the background
        statuses = {
            r["status"]: r["n"]
            for r in await db.fetch("SELECT status, count(*) AS n FROM entries GROUP BY status")
        }
        if "REGISTERED" not in statuses:
            break
        await asyncio.sleep(0.25)
    assert statuses == {"ALLOCATED": 500, "NOT_SELECTED": 500}
    print(f"\n[1000 claims / 500 seats: {percentiles(lat)}]")


async def test_five_tabs_per_user_never_double_book(
    client: httpx.AsyncClient, db: asyncpg.Connection, settings: Settings
) -> None:
    drop = await _fifo(db, 500)
    users = await make_users(db, settings, 100)
    await enter_all(client, users, drop)
    tokens = await tokens_of(client, users, drop)
    jobs = [
        claim_with(client, u, drop, t) for u, t in zip(users, tokens, strict=True) for _ in range(5)
    ]
    rs = await asyncio.gather(*jobs)
    assert {r.status_code for r in rs} == {200}
    by_user = [rs[i : i + 5] for i in range(0, 500, 5)]
    for tabs in by_user:
        assert len({r.json()["seat_no"] for r in tabs}) == 1  # all five tabs got the same seat
    await _assert_integrity(db, drop, sold=100)


async def test_the_integrity_view_stays_ok_while_the_storm_runs(
    client: httpx.AsyncClient, db: asyncpg.Connection, settings: Settings
) -> None:
    drop = await _fifo(db, 200)
    users = await make_users(db, settings, 400)
    await enter_all(client, users, drop)
    tokens = await tokens_of(client, users, drop)
    stop = False
    seen: list[bool] = []

    async def watch() -> None:
        async with asyncio.timeout(60):
            conn = await asyncpg.connect(settings.database_url)
            try:
                while not stop:
                    seen.append(
                        await conn.fetchval(
                            "SELECT invariant_ok FROM v_drop_integrity WHERE drop_id = $1",
                            uuid.UUID(drop),
                        )
                    )
                    await asyncio.sleep(0.05)
            finally:
                await conn.close()

    watcher = asyncio.create_task(watch())
    await asyncio.gather(
        *[claim_with(client, u, drop, t) for u, t in zip(users, tokens, strict=True) if t]
    )
    stop = True
    await watcher
    assert seen and all(seen), f"{seen.count(False)} of {len(seen)} samples failed"
    await _assert_integrity(db, drop, sold=200)
