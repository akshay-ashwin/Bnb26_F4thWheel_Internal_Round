"""Chunk 6: admission tokens and the Fair claim path, plus the sweeper that returns expired
offers to the pool. Real Postgres, real Redis; stress cases run 500-1000 concurrent requests."""

from __future__ import annotations

import asyncio
import base64
import contextlib
import json
import random
import time
import uuid

import asyncpg
import httpx
import pytest
from fastapi import FastAPI

from app.config import Settings
from app.security import TokenInvalidError, decode_token, encode_token, sid_hash
from app.services.sweeper import sweep_drop
from tests.app_helpers import (
    TestUser,
    claim_with,
    fair_drawn_drop,
    percentiles,
    token_of,
    tokens_of,
)


def forge(settings: Settings, drop: str, entry_id: str, user: TestUser, **over: object) -> str:
    now = int(time.time())
    claims = {
        "drop_id": drop,
        "entry_id": entry_id,
        "sid_hash": sid_hash(user.session_id),
        "jti": uuid.uuid4().hex,
        "iat": now,
        "exp": now + 60,
        "run": 1,
    }
    claims.update(over)
    return encode_token(settings.token_signing_key, claims)


async def _entry_id(db: asyncpg.Connection, user: TestUser) -> str:
    return str(await db.fetchval("SELECT id FROM entries WHERE user_id = $1", user.user_id))


async def _integrity(db: asyncpg.Connection, drop: str, *, sold: int) -> None:
    row = await db.fetchrow("SELECT * FROM v_drop_integrity WHERE drop_id = $1", uuid.UUID(drop))
    assert row["invariant_ok"] is True, dict(row)
    assert (row["sold"], row["oversold"], row["duplicate_entries_with_seats"]) == (sold, 0, 0)
    assert row["allocations_count"] == sold


# --- token content ---


async def test_token_claims_binding_and_expiry(
    client: httpx.AsyncClient, db: asyncpg.Connection, settings: Settings
) -> None:
    drop, users = await fair_drawn_drop(
        client, db, settings, capacity=3, entrants=3, claim_window_s=30
    )
    u = users[0]
    token = await token_of(client, u, drop)
    assert token and token.count(".") == 2 and len(token) < 400
    claims = decode_token(settings.token_signing_key, token)
    assert claims["drop_id"] == drop and claims["entry_id"] == await _entry_id(db, u)
    assert claims["sid_hash"] == sid_hash(u.session_id) and claims["run"] == 1
    offer = await db.fetchval(
        "SELECT extract(epoch FROM offer_expires_at) FROM entries WHERE user_id = $1", u.user_id
    )
    assert claims["exp"] <= int(offer) + 1  # never beyond the offer
    assert claims["exp"] - claims["iat"] <= settings.token_ttl_s
    assert token != await token_of(client, u, drop)  # fresh jti on every /me


async def test_header_pins_the_algorithm(settings: Settings) -> None:
    good = encode_token(
        settings.token_signing_key,
        {
            "drop_id": "d",
            "entry_id": "e",
            "sid_hash": "s",
            "jti": "j",
            "iat": 1,
            "exp": int(time.time()) + 60,
            "run": 1,
        },
    )
    header, payload, _ = good.split(".")
    for alg in ("none", "HS512", "RS256"):
        bad_header = (
            base64.urlsafe_b64encode(json.dumps({"alg": alg, "typ": "JWT"}).encode())
            .rstrip(b"=")
            .decode()
        )
        for sig in ("", "AAAA"):
            with pytest.raises(TokenInvalidError):
                decode_token(settings.token_signing_key, f"{bad_header}.{payload}.{sig}")


# --- valid, invalid, wrong session ---


async def test_a_valid_token_gets_the_seat(
    client: httpx.AsyncClient, db: asyncpg.Connection, settings: Settings
) -> None:
    drop, users = await fair_drawn_drop(client, db, settings, capacity=5, entrants=5)
    token = await token_of(client, users[0], drop)
    r = await claim_with(client, users[0], drop, token)
    assert r.status_code == 200 and 1 <= r.json()["seat_no"] <= 5
    me = (await client.get(f"/api/drops/{drop}/me", headers=users[0].headers)).json()
    assert (
        me["entry"]["status"] == "ALLOCATED" and me["allocation"]["seat_no"] == r.json()["seat_no"]
    )
    await _integrity(db, drop, sold=1)


async def test_waitlisted_and_unranked_users_cannot_claim(
    client: httpx.AsyncClient, db: asyncpg.Connection, settings: Settings
) -> None:
    drop, users = await fair_drawn_drop(client, db, settings, capacity=2, entrants=5)
    waitlisted = [u for u in users if await token_of(client, u, drop) is None]
    assert len(waitlisted) == 3
    for u in waitlisted:  # even a correctly signed token for their own entry is refused
        r = await claim_with(client, u, drop, forge(settings, drop, await _entry_id(db, u), u))
        assert r.status_code == 403 and r.json()["error"]["code"] == "NOT_OFFERED"
    assert await db.fetchval("SELECT count(*) FROM allocations") == 0


@pytest.mark.parametrize(
    "case", ["expired", "bad_signature", "tampered", "wrong_drop", "stale_run", "garbage"]
)
async def test_invalid_tokens_are_401(
    client: httpx.AsyncClient, db: asyncpg.Connection, settings: Settings, case: str
) -> None:
    drop, users = await fair_drawn_drop(client, db, settings, capacity=3, entrants=3)
    u = users[0]
    entry = await _entry_id(db, u)
    now = int(time.time())
    if case == "expired":
        token = forge(settings, drop, entry, u, iat=now - 120, exp=now - 60)
    elif case == "bad_signature":
        token = encode_token(
            "x" * 40,
            {
                "drop_id": drop,
                "entry_id": entry,
                "sid_hash": sid_hash(u.session_id),
                "jti": "j",
                "iat": now,
                "exp": now + 60,
                "run": 1,
            },
        )
    elif case == "tampered":
        good = await token_of(client, u, drop)
        assert good
        h, p, sig = good.split(".")
        claims = json.loads(base64.urlsafe_b64decode(p + "=" * (-len(p) % 4)))
        claims["entry_id"] = str(uuid.uuid4())
        p2 = base64.urlsafe_b64encode(json.dumps(claims).encode()).rstrip(b"=").decode()
        token = f"{h}.{p2}.{sig}"
    elif case == "wrong_drop":
        token = forge(settings, str(uuid.uuid4()), entry, u)
    elif case == "stale_run":
        token = forge(settings, drop, entry, u, run=99)
    else:
        token = "not-a-token"
    r = await claim_with(client, u, drop, token)
    assert r.status_code == 401 and r.json()["error"]["code"] == "TOKEN_INVALID", r.text
    assert await db.fetchval("SELECT count(*) FROM allocations") == 0
    assert (await claim_with(client, u, drop, await token_of(client, u, drop))).status_code == 200


async def test_an_expired_token_costs_nothing_while_the_offer_is_valid(
    client: httpx.AsyncClient, db: asyncpg.Connection, settings: Settings
) -> None:
    drop, users = await fair_drawn_drop(client, db, settings, capacity=3, entrants=3)
    u = users[0]
    now = int(time.time())
    stale = forge(settings, drop, await _entry_id(db, u), u, iat=now - 300, exp=now - 200)
    assert (await claim_with(client, u, drop, stale)).status_code == 401
    fresh = await token_of(client, u, drop)  # /me reissues
    assert fresh and (await claim_with(client, u, drop, fresh)).status_code == 200


async def test_a_token_from_another_session_never_works(
    client: httpx.AsyncClient, db: asyncpg.Connection, settings: Settings
) -> None:
    drop, users = await fair_drawn_drop(client, db, settings, capacity=4, entrants=4)
    victim, thief = users[0], users[1]
    stolen = await token_of(client, victim, drop)
    r = await claim_with(client, thief, drop, stolen)
    assert r.status_code == 401 and r.json()["error"]["code"] == "TOKEN_INVALID"
    # the same human on a second device is a second session: tokens bind to sessions, not users
    from app.security import sign_session

    session2 = await db.fetchval(
        "INSERT INTO sessions (user_id, device_id) VALUES ($1, 'dev-second-01') RETURNING id",
        victim.user_id,
    )
    other = TestUser(
        victim.user_id, victim.public_id, session2, sign_session(settings.session_secret, session2)
    )
    assert (await claim_with(client, other, drop, stolen)).status_code == 401
    own = await token_of(client, other, drop)  # its own /me issues a valid token
    assert own and (await claim_with(client, other, drop, own)).status_code == 200
    assert await db.fetchval("SELECT count(*) FROM allocations") == 1


# --- replay and concurrency ---


async def test_replaying_a_used_token_returns_the_same_seat_and_never_a_second(
    client: httpx.AsyncClient, db: asyncpg.Connection, settings: Settings
) -> None:
    drop, users = await fair_drawn_drop(client, db, settings, capacity=5, entrants=5)
    token = await token_of(client, users[0], drop)
    first = await claim_with(client, users[0], drop, token)
    replays = [await claim_with(client, users[0], drop, token) for _ in range(3)]
    assert {r.status_code for r in replays} == {200}
    assert {r.json()["allocation_id"] for r in replays} == {first.json()["allocation_id"]}
    await _integrity(db, drop, sold=1)


async def test_concurrent_replay_of_one_token_is_one_seat(
    client: httpx.AsyncClient, db: asyncpg.Connection, settings: Settings
) -> None:
    drop, users = await fair_drawn_drop(client, db, settings, capacity=5, entrants=5)
    token = await token_of(client, users[0], drop)
    rs = await asyncio.gather(*[claim_with(client, users[0], drop, token) for _ in range(30)])
    assert {r.status_code for r in rs} == {200}
    assert len({r.json()["allocation_id"] for r in rs}) == 1
    await _integrity(db, drop, sold=1)


async def test_500_winners_claim_at_once_and_fill_exactly_500_seats(
    client: httpx.AsyncClient, db: asyncpg.Connection, settings: Settings
) -> None:
    drop, users = await fair_drawn_drop(client, db, settings, capacity=500, entrants=700)
    tokens = await tokens_of(client, users, drop)
    winners = [(u, t) for u, t in zip(users, tokens, strict=True) if t]
    assert len(winners) == 500
    lat: list[float] = []

    async def one(u: TestUser, t: str) -> httpx.Response:
        s = time.perf_counter()
        r = await claim_with(client, u, drop, t)
        lat.append(time.perf_counter() - s)
        return r

    rs = await asyncio.gather(*[one(u, t) for u, t in winners])
    assert {r.status_code for r in rs} == {200}
    assert sorted(r.json()["seat_no"] for r in rs) == list(range(1, 501))
    await _integrity(db, drop, sold=500)
    print(f"\n[500 fair winners claiming at once: {percentiles(lat)}]")


async def test_1000_claim_attempts_against_500_seats_never_oversell(
    client: httpx.AsyncClient, db: asyncpg.Connection, settings: Settings
) -> None:
    drop, users = await fair_drawn_drop(client, db, settings, capacity=500, entrants=1000)
    real = await tokens_of(client, users, drop)
    entry_ids = {
        u.user_id: str(e)
        for u, e in zip(
            users,
            [
                await db.fetchval("SELECT id FROM entries WHERE user_id = $1", u.user_id)
                for u in users
            ],
            strict=True,
        )
    }
    jobs = []
    for u, t in zip(users, real, strict=True):
        # winners use their real token; the 500 waitlisted users try with a validly signed token
        jobs.append(
            claim_with(client, u, drop, t or forge(settings, drop, entry_ids[u.user_id], u))
        )
    rs = await asyncio.gather(*jobs)
    ok = [r for r in rs if r.status_code == 200]
    refused = [r for r in rs if r.status_code != 200]
    assert len(ok) == 500 and len(refused) == 500
    assert {r.json()["error"]["code"] for r in refused} == {"NOT_OFFERED"}
    assert len({r.json()["seat_no"] for r in ok}) == 500
    await _integrity(db, drop, sold=500)


async def test_five_tabs_per_winner_give_one_seat_each(
    client: httpx.AsyncClient, db: asyncpg.Connection, settings: Settings
) -> None:
    drop, users = await fair_drawn_drop(client, db, settings, capacity=60, entrants=60)
    tokens = await tokens_of(client, users, drop)
    rs = await asyncio.gather(
        *[
            claim_with(client, u, drop, t)
            for u, t in zip(users, tokens, strict=True)
            for _ in range(5)
        ]
    )
    assert {r.status_code for r in rs} == {200}
    for i in range(0, 300, 5):
        assert len({r.json()["seat_no"] for r in rs[i : i + 5]}) == 1
    await _integrity(db, drop, sold=60)


async def test_cancelled_requests_then_retries_leave_consistent_state(
    client: httpx.AsyncClient, db: asyncpg.Connection, settings: Settings
) -> None:
    """Kill claims mid-flight (task cancellation drops the client AND the server-side work), then
    retry everything with the same keys: no seat is lost, none is double-booked."""
    drop, users = await fair_drawn_drop(client, db, settings, capacity=120, entrants=200)
    tokens = await tokens_of(client, users, drop)
    keys = [str(uuid.uuid4()) for _ in users]
    rng = random.Random(11)

    async def attempt(u: TestUser, t: str | None, key: str, delay: float) -> None:
        if t is None:
            return
        task = asyncio.create_task(claim_with(client, u, drop, t, key))
        await asyncio.sleep(delay)
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await task

    await asyncio.gather(
        *[
            attempt(u, t, k, rng.uniform(0, 0.15))
            for u, t, k in zip(users, tokens, keys, strict=True)
        ]
    )
    await asyncio.sleep(0.3)  # let any in-flight transactions finish or roll back
    sold_mid = await db.fetchval("SELECT count(*) FROM seats WHERE status = 'sold'")
    assert sold_mid <= 120
    fresh = await tokens_of(client, users, drop)  # None for users whose cancelled claim committed
    rs = await asyncio.gather(
        *[
            claim_with(client, u, drop, t, k)
            for u, t, k in zip(users, fresh, keys, strict=True)
            if t
        ]
    )
    assert {r.status_code for r in rs} == {200}
    assert len({r.json()["seat_no"] for r in rs}) == len(rs)
    # every one of the 120 winners ends with exactly one seat, whether or not the first try died
    assert await db.fetchval("SELECT count(*) FROM entries WHERE status = 'ALLOCATED'") == 120
    assert await db.fetchval("SELECT count(*) FROM allocations") == 120
    await _integrity(db, drop, sold=120)


# --- offers expire, waitlist moves ---


async def test_an_expired_offer_is_refused_marked_and_the_next_in_rank_is_promoted(
    client: httpx.AsyncClient, db: asyncpg.Connection, settings: Settings, app: FastAPI
) -> None:
    drop, users = await fair_drawn_drop(client, db, settings, capacity=2, entrants=5)
    by_rank = {
        r["user_id"]: r["draw_rank"]
        for r in await db.fetch("SELECT user_id, draw_rank FROM entries")
    }
    ordered = sorted(users, key=lambda u: by_rank[u.user_id])
    first, second, third, fourth = ordered[0], ordered[1], ordered[2], ordered[3]
    token = await token_of(client, first, drop)
    await db.execute(
        "UPDATE entries SET offer_expires_at = now() - interval '1 second' WHERE user_id = $1",
        first.user_id,
    )
    late = await claim_with(client, first, drop, token)
    assert late.status_code == 409 and late.json()["error"]["code"] == "OFFER_EXPIRED"
    assert (
        await db.fetchval("SELECT status FROM entries WHERE user_id = $1", first.user_id)
        == "OFFER_EXPIRED"
    )

    pool, cache, metrics = app.state.pool, app.state.cache, app.state.metrics
    result = await sweep_drop(pool, cache, settings, metrics, uuid.UUID(drop))
    assert (result.expired, result.promoted) == (
        0,
        1,
    )  # already marked by the claim; one seat freed
    states = {
        r["user_id"]: r["status"] for r in await db.fetch("SELECT user_id, status FROM entries")
    }
    assert (
        states[third.user_id] == "OFFERED" and states[fourth.user_id] == "WAITLISTED"
    )  # rank order
    assert states[second.user_id] == "OFFERED"
    me = (await client.get(f"/api/drops/{drop}/me", headers=third.headers)).json()["entry"]
    assert me["status"] == "OFFERED" and me["admission_token"] and me["waitlist_pos"] is None
    assert (await claim_with(client, third, drop, me["admission_token"])).status_code == 200


async def test_the_sweeper_cascades_until_the_drop_is_done(
    client: httpx.AsyncClient, db: asyncpg.Connection, settings: Settings, app: FastAPI
) -> None:
    drop, users = await fair_drawn_drop(client, db, settings, capacity=3, entrants=9)
    pool, cache, metrics = app.state.pool, app.state.cache, app.state.metrics
    for _ in range(5):  # nobody claims: every offer expires, the next waitlisted get offers
        await db.execute(
            "UPDATE entries SET offer_expires_at = now() - interval '1 second'"
            " WHERE status IN ('OFFERED', 'STEP_UP_REQUIRED')"
        )
        await sweep_drop(pool, cache, settings, metrics, uuid.UUID(drop))
        if await db.fetchval("SELECT phase FROM drops") == "DONE":
            break
    assert await db.fetchval("SELECT phase FROM drops") == "DONE"
    counts = {
        r["status"]: r["n"]
        for r in await db.fetch("SELECT status, count(*) AS n FROM entries GROUP BY status")
    }
    assert counts == {"OFFER_EXPIRED": 9}  # everyone had their turn; nobody was dropped silently


async def test_sold_out_ends_the_drop_and_marks_the_rest_not_selected(
    client: httpx.AsyncClient, db: asyncpg.Connection, settings: Settings, app: FastAPI
) -> None:
    drop, users = await fair_drawn_drop(client, db, settings, capacity=2, entrants=6)
    tokens = await tokens_of(client, users, drop)
    for u, t in zip(users, tokens, strict=True):
        if t:
            assert (await claim_with(client, u, drop, t)).status_code == 200
    pool, cache, metrics = app.state.pool, app.state.cache, app.state.metrics
    res = await sweep_drop(pool, cache, settings, metrics, uuid.UUID(drop))
    assert res.done is True
    counts = {
        r["status"]: r["n"]
        for r in await db.fetch("SELECT status, count(*) AS n FROM entries GROUP BY status")
    }
    assert counts == {"ALLOCATED": 2, "NOT_SELECTED": 4}
    await _integrity(db, drop, sold=2)


async def test_claims_are_closed_after_the_drop_is_done(
    client: httpx.AsyncClient, db: asyncpg.Connection, settings: Settings
) -> None:
    from tests.app_helpers import admin_phase

    drop, users = await fair_drawn_drop(client, db, settings, capacity=3, entrants=3)
    token = await token_of(client, users[0], drop)
    assert (await admin_phase(client, settings, drop, "close")).json()["phase"] == "DONE"
    r = await claim_with(client, users[0], drop, token)
    assert r.status_code == 409 and r.json()["error"]["code"] == "OFFER_EXPIRED"
    assert await db.fetchval("SELECT count(*) FROM allocations") == 0
