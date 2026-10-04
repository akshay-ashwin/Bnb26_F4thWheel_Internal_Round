"""Chunk 5: Fair Draw: freeze, entry-set hash, seed commitment, deterministic ranking, offers,
waitlist and the proof. The reference ranking below uses only hashlib/hmac (no backend imports),
exactly as a judge's own script would."""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import inspect
import random
import uuid

import asyncpg
import httpx
from fastapi import FastAPI

from app import draw_math
from app.config import Settings
from app.services import draw as draw_service
from tests.app_helpers import (
    admin_create,
    admin_headers,
    admin_phase,
    enter_all,
    fair_drawn_drop,
    make_users,
    strip_volatile,
)


def reference_ranking(seed_hex: str, drop_id: str, public_ids: list[str]) -> list[str]:
    seed = bytes.fromhex(seed_hex)
    keyed = sorted(
        (hmac.new(seed, f"{drop_id}|{p}".encode(), hashlib.sha256).hexdigest(), p)
        for p in public_ids
    )
    return [p for _, p in keyed]


# --- pure functions ---


def test_commitment_and_set_hash_follow_the_written_definition() -> None:
    seed = "00" * 31 + "ff"
    assert draw_math.seed_commit(seed) == hashlib.sha256(bytes.fromhex(seed)).hexdigest()
    assert draw_math.entry_set_hash(["b", "a", "c"]) == hashlib.sha256(b"a\nb\nc").hexdigest()
    assert draw_math.entry_set_hash([]) == hashlib.sha256(b"").hexdigest()
    assert len(draw_math.new_seed()) == 64 and draw_math.new_seed() != draw_math.new_seed()


def test_ranking_is_a_pure_function_of_seed_drop_and_identity_set() -> None:
    seed = draw_math.new_seed()
    drop = str(uuid.uuid4())
    people = [(f"e{i}", f"user{i:04d}") for i in range(300)]
    a = draw_math.rank_entries(seed, drop, people)
    shuffled = people[:]
    random.Random(3).shuffle(shuffled)
    b = draw_math.rank_entries(seed, drop, shuffled)
    assert a == b  # arrival / insertion order is not an input
    assert [r.public_id for r in a] == reference_ranking(seed, drop, [p for _, p in people])
    assert [r.rank for r in a] == list(range(1, 301))
    other_seed = draw_math.rank_entries(draw_math.new_seed(), drop, people)
    other_drop = draw_math.rank_entries(seed, str(uuid.uuid4()), people)
    assert [r.public_id for r in a] != [r.public_id for r in other_seed]
    assert [r.public_id for r in a] != [r.public_id for r in other_drop]


def test_a_known_vector() -> None:
    seed = "11" * 32
    drop = "00000000-0000-0000-0000-000000000001"
    expected = hmac.new(bytes.fromhex(seed), f"{drop}|abc".encode(), hashlib.sha256).hexdigest()
    assert draw_math.rank_key(seed, drop, "abc") == expected


def test_a_three_point_eight_percent_group_wins_about_its_share() -> None:
    """Statistical sanity: 200 'bot' identities among 5,200 entries, 50 seats: expected wins
    50 * 200/5200 ~= 1.92. Averaged over 150 independent seeds the mean must sit close to it."""
    people = [(f"e{i}", f"{'bot' if i < 200 else 'hum'}{i:05d}") for i in range(5200)]
    drop = str(uuid.uuid4())
    wins = 0
    draws = 150
    for _ in range(draws):
        ranked = draw_math.rank_entries(draw_math.new_seed(), drop, people)
        wins += sum(1 for r in ranked[:50] if r.public_id.startswith("bot"))
    mean = wins / draws
    assert 1.5 < mean < 2.4, mean


def test_the_draw_module_never_reads_arrival_time_or_network_fields() -> None:
    source = inspect.getsource(draw_service) + inspect.getsource(draw_math)
    for forbidden in ("entered_at", "client_ip", "device_id", "ua_hash", "request_count"):
        assert forbidden not in source, forbidden


# --- admin: create and phases ---


async def test_create_publishes_a_commitment_to_the_hidden_seed(
    client: httpx.AsyncClient, settings: Settings, db: asyncpg.Connection
) -> None:
    drop = await admin_create(client, settings, capacity=20)
    row = await db.fetchrow("SELECT seed, seed_commit, phase, mode, capacity, window_s FROM drops")
    assert hashlib.sha256(bytes.fromhex(row["seed"])).hexdigest() == row["seed_commit"]
    assert (row["phase"], row["mode"], row["capacity"], row["window_s"]) == (
        "SCHEDULED",
        "fair",
        20,
        60,
    )
    assert await db.fetchval("SELECT count(*) FROM seats") == 20
    public = (await client.get(f"/api/drops/{drop}")).json()
    assert public["seed_commit"] == row["seed_commit"] and public["seed"] is None


async def test_admin_endpoints_require_the_admin_key(client: httpx.AsyncClient) -> None:
    for headers in ({}, {"X-Admin-Key": "wrong"}):
        r = await client.post(
            "/api/admin/drops",
            json={"name": "x", "mode": "fair", "window_s": 5, "capacity": 5},
            headers=headers,
        )
        assert r.status_code == 401 and r.json()["error"]["code"] == "UNAUTHENTICATED"


async def test_invalid_transitions_and_idempotent_repeats(
    client: httpx.AsyncClient, settings: Settings
) -> None:
    drop = await admin_create(client, settings, capacity=5)
    assert (await admin_phase(client, settings, drop, "close")).status_code == 409  # SCHEDULED
    assert (await admin_phase(client, settings, drop, "draw")).status_code == 409
    assert (await admin_phase(client, settings, drop, "open")).json()["phase"] == "OPEN"
    assert (await admin_phase(client, settings, drop, "open")).json()[
        "phase"
    ] == "OPEN"  # repeat: 200
    early = await admin_phase(client, settings, drop, "draw")  # window still open
    assert early.status_code == 409 and early.json()["error"]["code"] == "INVALID_TRANSITION"
    assert (await admin_phase(client, settings, drop, "close")).json()["phase"] == "CLOSED"
    assert (await admin_phase(client, settings, drop, "close")).json()["phase"] == "CLOSED"
    assert (await admin_phase(client, settings, drop, "open")).status_code == 409
    fifo = await admin_create(client, settings, mode="fifo", capacity=5)
    await admin_phase(client, settings, fifo, "open")
    assert (await admin_phase(client, settings, fifo, "draw")).status_code == 409
    unknown = await admin_phase(client, settings, str(uuid.uuid4()), "open")
    assert unknown.status_code == 404
    mode_misuse = await admin_phase(client, settings, drop, "open", mode="fifo")
    assert mode_misuse.status_code == 400


async def test_twenty_concurrent_opens_make_one_transition(
    client: httpx.AsyncClient, settings: Settings
) -> None:
    drop = await admin_create(client, settings, capacity=5)
    rs = await asyncio.gather(*[admin_phase(client, settings, drop, "open") for _ in range(20)])
    assert {r.status_code for r in rs} == {200} and {r.json()["phase"] for r in rs} == {"OPEN"}


# --- the draw ---


async def test_draw_ranks_everyone_and_splits_offers_from_the_waitlist(
    client: httpx.AsyncClient, db: asyncpg.Connection, settings: Settings
) -> None:
    drop, users = await fair_drawn_drop(client, db, settings, capacity=20, entrants=60)
    rows = await db.fetch(
        "SELECT status, draw_rank, offer_expires_at, offered_at FROM entries ORDER BY draw_rank"
    )
    assert [r["draw_rank"] for r in rows] == list(range(1, 61))
    assert [r["status"] for r in rows] == ["OFFERED"] * 20 + ["WAITLISTED"] * 40
    assert all(r["offer_expires_at"] and r["offered_at"] for r in rows[:20])
    assert all(r["offer_expires_at"] is None for r in rows[20:])
    public = (await client.get(f"/api/drops/{drop}")).json()
    assert public["phase"] == "CLAIMING" and public["seed"] and public["entry_set_hash"]
    assert hashlib.sha256(bytes.fromhex(public["seed"])).hexdigest() == public["seed_commit"]


async def test_me_shows_offer_rank_token_and_waitlist_position(
    client: httpx.AsyncClient, db: asyncpg.Connection, settings: Settings
) -> None:
    drop, users = await fair_drawn_drop(client, db, settings, capacity=5, entrants=12)
    seen = {"OFFERED": 0, "WAITLISTED": 0}
    for u in users:
        me = (await client.get(f"/api/drops/{drop}/me", headers=u.headers)).json()
        entry = me["entry"]
        seen[entry["status"]] += 1
        assert me["phase"] == "CLAIMING" and entry["rank"] is not None
        if entry["status"] == "OFFERED":
            assert (
                entry["admission_token"]
                and entry["offer_expires_at"]
                and entry["waitlist_pos"] is None
            )
            assert entry["rank"] <= 5
        else:
            assert entry["admission_token"] is None and entry["offer_expires_at"] is None
            assert entry["waitlist_pos"] == entry["rank"] - 5  # first waitlisted is #1
    assert seen == {"OFFERED": 5, "WAITLISTED": 7}


async def test_fewer_entries_than_seats_means_everyone_wins(
    client: httpx.AsyncClient, db: asyncpg.Connection, settings: Settings
) -> None:
    await fair_drawn_drop(client, db, settings, capacity=10, entrants=4)
    assert await db.fetchval("SELECT count(*) FROM entries WHERE status = 'OFFERED'") == 4


async def test_an_empty_draw_is_fine(
    client: httpx.AsyncClient, db: asyncpg.Connection, settings: Settings
) -> None:
    drop = await admin_create(client, settings, capacity=5)
    await admin_phase(client, settings, drop, "open")
    await admin_phase(client, settings, drop, "close")
    assert (await admin_phase(client, settings, drop, "draw")).json()["phase"] == "CLAIMING"


async def test_the_proof_matches_an_independent_recomputation(
    client: httpx.AsyncClient, db: asyncpg.Connection, settings: Settings
) -> None:
    drop, users = await fair_drawn_drop(client, db, settings, capacity=25, entrants=80)
    admin = (
        await client.get(f"/api/admin/drops/{drop}/draw-proof", headers=admin_headers(settings))
    ).json()
    public = (await client.get(f"/api/drops/{drop}/draw-proof")).json()
    assert admin["seed"] == public["seed"] and admin["entry_set_hash"] == public["entry_set_hash"]
    assert admin["ranked_public_ids"] is None and len(public["ranked_public_ids"]) == 80
    assert hashlib.sha256(bytes.fromhex(admin["seed"])).hexdigest() == admin["seed_commit"]
    ids = public["eligible_public_ids"]
    assert ids == sorted(u.public_id for u in users)
    assert hashlib.sha256("\n".join(ids).encode()).hexdigest() == admin["entry_set_hash"]
    expected = reference_ranking(admin["seed"], drop, ids)
    assert public["ranked_public_ids"] == expected
    db_order = [
        r["public_id"]
        for r in await db.fetch(
            "SELECT u.public_id FROM entries e JOIN users u ON u.id = e.user_id"
            " ORDER BY e.draw_rank"
        )
    ]
    assert db_order == expected
    assert admin["algorithm"].startswith("HMAC_SHA256(seed, drop_id|user_public_id)")


async def test_drawing_twice_changes_nothing(
    client: httpx.AsyncClient, db: asyncpg.Connection, settings: Settings
) -> None:
    drop, _ = await fair_drawn_drop(client, db, settings, capacity=10, entrants=30)
    before = await db.fetch(
        "SELECT id, status, draw_rank, offer_expires_at FROM entries ORDER BY id"
    )
    proof_a = (await client.get(f"/api/drops/{drop}/draw-proof")).json()
    again = await admin_phase(client, settings, drop, "draw")
    assert again.status_code == 200 and again.json()["phase"] == "CLAIMING"
    after = await db.fetch(
        "SELECT id, status, draw_rank, offer_expires_at FROM entries ORDER BY id"
    )
    assert [tuple(r) for r in before] == [tuple(r) for r in after]
    proof_b = (await client.get(f"/api/drops/{drop}/draw-proof")).json()
    assert strip_volatile(proof_a) == strip_volatile(proof_b)


async def test_ranks_ignore_arrival_order_and_timing(
    client: httpx.AsyncClient, db: asyncpg.Connection, settings: Settings
) -> None:
    drop = await admin_create(client, settings, capacity=10)
    await admin_phase(client, settings, drop, "open")
    users = await make_users(db, settings, 40)
    await enter_all(client, users, drop)
    # scramble the evidence the draw must not look at
    await db.execute(
        "UPDATE entries SET entered_at = now() - (random() * interval '1 hour'),"
        " client_ip = '198.51.100.1', device_id = md5(random()::text)"
    )
    await admin_phase(client, settings, drop, "close")
    await admin_phase(client, settings, drop, "draw")
    proof = (await client.get(f"/api/drops/{drop}/draw-proof")).json()
    assert proof["ranked_public_ids"] == reference_ranking(
        proof["seed"], drop, [u.public_id for u in users]
    )


async def test_proof_is_unavailable_before_the_draw(
    client: httpx.AsyncClient, settings: Settings
) -> None:
    drop = await admin_create(client, settings, capacity=5)
    assert (await client.get(f"/api/drops/{drop}/draw-proof")).status_code == 409
    r = await client.get(f"/api/admin/drops/{drop}/draw-proof", headers=admin_headers(settings))
    assert r.status_code == 409 and r.json()["error"]["code"] == "INVALID_TRANSITION"
    assert (await client.get(f"/api/admin/drops/{drop}/draw-proof")).status_code == 401


async def test_high_risk_winners_are_held_for_step_up_not_dropped(
    client: httpx.AsyncClient, db: asyncpg.Connection, settings: Settings
) -> None:
    # everyone has the same risk-free ranking inputs; plant a high score on three entrants
    drop, users = await fair_drawn_drop(
        client, db, settings, capacity=50, entrants=50, risk={0: 80, 1: 61, 2: 59}
    )
    statuses = {
        r["user_id"]: r["status"] for r in await db.fetch("SELECT user_id, status FROM entries")
    }
    assert statuses[users[0].user_id] == statuses[users[1].user_id] == "STEP_UP_REQUIRED"
    assert statuses[users[2].user_id] == "OFFERED"  # below the threshold: no friction
    assert sum(1 for s in statuses.values() if s == "STEP_UP_REQUIRED") == 2
    row = await db.fetchrow(
        "SELECT offer_expires_at FROM entries WHERE user_id = $1", users[0].user_id
    )
    assert row["offer_expires_at"] is not None  # the seat is held while they re-verify


async def test_the_draw_waits_out_the_close_grace_period(
    client: httpx.AsyncClient, settings: Settings, app: FastAPI
) -> None:
    import time

    slow = settings.model_copy(update=dict(draw_grace_s=1.2))
    drop = await admin_create(client, settings, capacity=5)
    await admin_phase(client, settings, drop, "open")
    await admin_phase(client, settings, drop, "close")
    started = time.monotonic()
    await draw_service.freeze(app.state.pool, slow, uuid.UUID(drop))
    assert time.monotonic() - started >= 0.9


async def test_500_winners_draw_from_52_hundred_entries_quickly(
    client: httpx.AsyncClient, db: asyncpg.Connection, settings: Settings
) -> None:
    """A realistic draw size (5,200 entries, 500 seats); the 52,000 case is measured separately."""
    import time

    drop = await admin_create(client, settings, capacity=500)
    await admin_phase(client, settings, drop, "open")
    users = await make_users(db, settings, 5200)
    await db.execute(
        "INSERT INTO entries (drop_id, user_id, status, run_no)"
        " SELECT $1, id, 'REGISTERED', 1 FROM users",
        uuid.UUID(drop),
    )
    await admin_phase(client, settings, drop, "close")
    started = time.perf_counter()
    r = await admin_phase(client, settings, drop, "draw")
    elapsed = time.perf_counter() - started
    assert r.status_code == 200
    assert await db.fetchval("SELECT count(*) FROM entries WHERE status = 'OFFERED'") == 500
    assert await db.fetchval("SELECT count(*) FROM entries WHERE status = 'WAITLISTED'") == 4700
    assert elapsed < 10
    print(f"\n[draw over 5,200 entries: {elapsed:.2f}s]")
    del users
