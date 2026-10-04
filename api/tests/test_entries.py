"""Chunk 3: drop view, entries, /me and idempotency (real Postgres, real Redis)."""

from __future__ import annotations

import asyncio
import random
import time
import uuid

import asyncpg
import httpx
import pytest
from fastapi import FastAPI

import app.abuse
from app.config import Settings
from tests.app_helpers import TestUser, login, make_users, strip_volatile
from tests.helpers import make_drop, open_drop


async def _open(
    db: asyncpg.Connection, *, capacity: int = 5, mode: str = "fair", window_s: int = 300
) -> str:
    drop_id = await make_drop(db, capacity=capacity, mode=mode)
    await open_drop(db, drop_id, window_s=window_s)
    return str(drop_id)


def _entries(drop: str) -> str:
    return f"/api/drops/{drop}/entries"


async def test_first_entry_then_me(client: httpx.AsyncClient, db: asyncpg.Connection) -> None:
    drop = await _open(db)
    token, _ = await login(client)
    h = {"Authorization": f"Bearer {token}"}
    r = await client.post(_entries(drop), json={}, headers=h)
    assert r.status_code == 201
    assert r.json()["status"] == "REGISTERED" and "server_time" in r.json()
    me = await client.get(f"/api/drops/{drop}/me", headers=h)
    assert me.status_code == 200
    body = me.json()
    assert body["phase"] == "OPEN" and body["allocation"] is None
    assert body["entry"]["entry_id"] == r.json()["entry_id"]
    assert body["entry"]["status"] == "REGISTERED" and body["entry"]["step_up_required"] is False
    assert body["entry"]["dev_otp"] is None
    assert 3600 <= body["poll_after_ms"] <= 4400  # OPEN + REGISTERED: 4000 +/- 10%


async def test_duplicate_entry_returns_the_same_entry(
    client: httpx.AsyncClient, db: asyncpg.Connection
) -> None:
    drop = await _open(db)
    token, _ = await login(client)
    h = {"Authorization": f"Bearer {token}"}
    a = await client.post(_entries(drop), json={}, headers=h)
    b = await client.post(_entries(drop), json={}, headers=h)
    assert (a.status_code, b.status_code) == (201, 200)
    assert a.json()["entry_id"] == b.json()["entry_id"]
    assert await db.fetchval("SELECT count(*) FROM entries") == 1


async def test_entry_stores_network_evidence_and_run_no(
    client: httpx.AsyncClient, db: asyncpg.Connection
) -> None:
    drop = await _open(db)
    token, _ = await login(client, device_id="device-evidence-1")
    r = await client.post(
        _entries(drop),
        json={},
        headers={"Authorization": f"Bearer {token}", "X-Sim-Client-IP": "203.0.113.7"},
    )
    row = await db.fetchrow(
        "SELECT host(client_ip) AS client_ip, device_id, run_no, risk_score, risk_flags"
        " FROM entries"
    )
    assert r.status_code == 201
    assert (row["client_ip"], row["device_id"], row["run_no"]) == (
        "203.0.113.7",
        "device-evidence-1",
        1,
    )
    assert row["risk_score"] == 0 and row["risk_flags"] == "[]"


async def test_concurrent_entries_from_one_session_make_one_entry(
    client: httpx.AsyncClient, db: asyncpg.Connection
) -> None:
    drop = await _open(db)
    token, _ = await login(client)
    h = {"Authorization": f"Bearer {token}"}
    rs = await asyncio.gather(*[client.post(_entries(drop), json={}, headers=h) for _ in range(40)])
    codes = sorted(r.status_code for r in rs)
    assert codes.count(201) == 1 and codes.count(200) == 39
    assert len({r.json()["entry_id"] for r in rs}) == 1
    assert await db.fetchval("SELECT count(*) FROM entries") == 1


async def test_idempotent_retry_with_the_same_key(
    client: httpx.AsyncClient, db: asyncpg.Connection
) -> None:
    drop = await _open(db)
    token, _ = await login(client)
    h = {"Authorization": f"Bearer {token}", "Idempotency-Key": str(uuid.uuid4())}
    a = await client.post(_entries(drop), json={}, headers=h)
    b = await client.post(_entries(drop), json={}, headers=h)
    assert a.status_code == 201 and b.status_code == 201  # the stored response is replayed
    assert {k: v for k, v in a.json().items() if k != "server_time"} == {
        k: v for k, v in b.json().items() if k != "server_time"
    }
    assert await db.fetchval("SELECT count(*) FROM entries") == 1
    assert await db.fetchval("SELECT count(*) FROM idempotency_records") == 1


async def test_concurrent_requests_with_one_key_write_once(
    client: httpx.AsyncClient, db: asyncpg.Connection
) -> None:
    drop = await _open(db)
    token, _ = await login(client)
    h = {"Authorization": f"Bearer {token}", "Idempotency-Key": str(uuid.uuid4())}
    rs = await asyncio.gather(*[client.post(_entries(drop), json={}, headers=h) for _ in range(12)])
    assert {r.status_code for r in rs} <= {200, 201}
    assert len({r.json()["entry_id"] for r in rs}) == 1
    assert await db.fetchval("SELECT count(*) FROM entries") == 1
    assert await db.fetchval("SELECT count(*) FROM idempotency_records") == 1


async def test_key_reused_for_a_different_request_is_422(
    client: httpx.AsyncClient, db: asyncpg.Connection
) -> None:
    first, second = await _open(db), await _open(db)
    token, _ = await login(client)
    h = {"Authorization": f"Bearer {token}", "Idempotency-Key": str(uuid.uuid4())}
    assert (await client.post(_entries(first), json={}, headers=h)).status_code == 201
    r = await client.post(_entries(second), json={}, headers=h)
    assert r.status_code == 422 and r.json()["error"]["code"] == "IDEMPOTENCY_KEY_REUSED"
    assert await db.fetchval("SELECT count(*) FROM entries") == 1


async def test_keys_are_scoped_per_user(client: httpx.AsyncClient, db: asyncpg.Connection) -> None:
    drop = await _open(db)
    key = str(uuid.uuid4())
    for _ in range(2):
        token, _ = await login(client)
        r = await client.post(
            _entries(drop),
            json={},
            headers={"Authorization": f"Bearer {token}", "Idempotency-Key": key},
        )
        assert r.status_code == 201
    assert await db.fetchval("SELECT count(*) FROM entries") == 2


async def test_malformed_idempotency_key_is_400(
    client: httpx.AsyncClient, db: asyncpg.Connection
) -> None:
    drop = await _open(db)
    token, _ = await login(client)
    r = await client.post(
        _entries(drop),
        json={},
        headers={"Authorization": f"Bearer {token}", "Idempotency-Key": "nope"},
    )
    assert r.status_code == 400 and r.json()["error"]["code"] == "VALIDATION_ERROR"


async def test_window_errors_and_unknown_drops(
    client: httpx.AsyncClient, db: asyncpg.Connection
) -> None:
    token, _ = await login(client)
    h = {"Authorization": f"Bearer {token}"}
    scheduled = str(await make_drop(db))
    r = await client.post(_entries(scheduled), json={}, headers=h)
    assert r.status_code == 403 and r.json()["error"]["code"] == "WINDOW_NOT_OPEN"
    closed = await _open(db)
    await db.execute(
        "UPDATE drops SET reg_opens_at = now() - interval '1 hour',"
        " reg_closes_at = now() - interval '1 second' WHERE id = $1",
        uuid.UUID(closed),
    )
    r = await client.post(_entries(closed), json={}, headers=h)
    assert r.status_code == 403 and r.json()["error"]["code"] == "WINDOW_CLOSED"
    await db.execute("UPDATE drops SET phase = 'CLOSED' WHERE id = $1", uuid.UUID(closed))
    assert (await client.post(_entries(closed), json={}, headers=h)).json()["error"][
        "code"
    ] == "WINDOW_CLOSED"
    r = await client.post(_entries(str(uuid.uuid4())), json={}, headers=h)
    assert r.status_code == 404 and r.json()["error"]["code"] == "NOT_FOUND"
    r = await client.post(_entries("not-a-uuid"), json={}, headers=h)  # the contract types it UUID
    assert r.status_code == 400 and r.json()["error"]["code"] == "VALIDATION_ERROR"
    assert await db.fetchval("SELECT count(*) FROM entries") == 0


async def test_a_retry_after_close_still_returns_the_existing_entry(
    client: httpx.AsyncClient, db: asyncpg.Connection
) -> None:
    drop = await _open(db)
    token, _ = await login(client)
    h = {"Authorization": f"Bearer {token}"}
    first = await client.post(_entries(drop), json={}, headers=h)
    await db.execute("UPDATE drops SET phase = 'CLOSED' WHERE id = $1", uuid.UUID(drop))
    again = await client.post(_entries(drop), json={}, headers=h)
    assert again.status_code == 200 and again.json()["entry_id"] == first.json()["entry_id"]


async def test_entries_require_a_session(client: httpx.AsyncClient, db: asyncpg.Connection) -> None:
    drop = await _open(db)
    r = await client.post(_entries(drop), json={})
    assert r.status_code == 401 and r.json()["error"]["code"] == "UNAUTHENTICATED"
    assert (await client.get(f"/api/drops/{drop}/me")).status_code == 401


async def test_entry_straddling_the_close_never_lands_after_it(
    client: httpx.AsyncClient, db: asyncpg.Connection, settings: Settings
) -> None:
    drop = await _open(db, window_s=2)
    users = await make_users(db, settings, 150)
    rng = random.Random(7)

    async def enter(u: TestUser) -> httpx.Response:
        await asyncio.sleep(rng.uniform(0.0, 3.0))
        return await client.post(_entries(drop), json={}, headers=u.headers)

    rs = await asyncio.gather(*[enter(u) for u in users])
    ok = [r for r in rs if r.status_code == 201]
    closed = [r for r in rs if r.status_code == 403]
    assert len(ok) + len(closed) == 150 and ok and closed
    assert {r.json()["error"]["code"] for r in closed} == {"WINDOW_CLOSED"}
    late = await db.fetchval(
        "SELECT count(*) FROM entries e JOIN drops d ON d.id = e.drop_id"
        " WHERE e.entered_at >= d.reg_closes_at"
    )
    assert late == 0 and await db.fetchval("SELECT count(*) FROM entries") == len(ok)


@pytest.mark.parametrize("mode", ["fair", "fifo"])
async def test_five_hundred_users_enter_concurrently(
    client: httpx.AsyncClient, db: asyncpg.Connection, settings: Settings, mode: str
) -> None:
    drop = await _open(db, mode=mode)
    users = await make_users(db, settings, 500)
    started = time.perf_counter()
    rs = await asyncio.gather(
        *[client.post(_entries(drop), json={}, headers=u.headers) for u in users]
    )
    elapsed = time.perf_counter() - started
    assert {r.status_code for r in rs} == {201}
    assert await db.fetchval("SELECT count(*) FROM entries") == 500
    assert elapsed < 30  # generous ceiling; the real figure is printed with -s
    print(f"\n[500 concurrent {mode} entries: {elapsed:.2f}s]")


async def test_a_failing_risk_hook_never_blocks_entry(
    client: httpx.AsyncClient, db: asyncpg.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def boom(**_: object) -> tuple[int, list[str]]:
        raise RuntimeError("redis exploded")

    monkeypatch.setattr(app.abuse, "score_entry", boom)
    drop = await _open(db)
    token, _ = await login(client)
    r = await client.post(_entries(drop), json={}, headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 201
    assert await db.fetchval("SELECT risk_flags::text FROM entries") == '["risk_unavailable"]'


# --- GET /drops/{id} and /me ---


async def test_drop_view_hides_seed_until_the_draw(
    client: httpx.AsyncClient, db: asyncpg.Connection
) -> None:
    drop = await _open(db, capacity=7)
    await db.execute("UPDATE drops SET seed = 'ab' || md5('x')")
    body = (await client.get(f"/api/drops/{drop}")).json()
    assert body["capacity"] == 7 and body["seats_remaining"] == 7 and body["phase"] == "OPEN"
    assert body["mode"] == "fair" and body["claim_window_s"] == 120 and body["seed_commit"]
    assert body["seed"] is None and body["entry_set_hash"] is None
    await db.execute("UPDATE drops SET phase = 'CLAIMING', entry_set_hash = 'h'")
    from app.services.drops import public_cache

    public_cache.clear()
    revealed = (await client.get(f"/api/drops/{drop}")).json()
    assert (
        revealed["phase"] == "CLAIMING" and revealed["seed"] and revealed["entry_set_hash"] == "h"
    )
    assert (await client.get(f"/api/drops/{uuid.uuid4()}")).status_code == 404


async def test_effective_phase_flips_to_closed_when_the_window_ends(
    client: httpx.AsyncClient, db: asyncpg.Connection
) -> None:
    drop = await _open(db)
    await db.execute(
        "UPDATE drops SET reg_opens_at = now() - interval '1 hour',"
        " reg_closes_at = now() - interval '1 second'"
    )
    assert (await client.get(f"/api/drops/{drop}")).json()["phase"] == "CLOSED"
    token, _ = await login(client)
    me = await client.get(f"/api/drops/{drop}/me", headers={"Authorization": f"Bearer {token}"})
    assert me.json()["phase"] == "CLOSED" and me.json()["entry"] is None


async def test_me_is_stable_across_polls_and_jittered(
    client: httpx.AsyncClient, db: asyncpg.Connection
) -> None:
    drop = await _open(db)
    token, _ = await login(client)
    h = {"Authorization": f"Bearer {token}"}
    polls = [(await client.get(f"/api/drops/{drop}/me", headers=h)).json() for _ in range(20)]
    assert all(p["entry"] is None and p["phase"] == "OPEN" for p in polls)
    values = {p["poll_after_ms"] for p in polls}
    assert len(values) > 1 and all(
        2700 <= v <= 3300 for v in values
    )  # OPEN, no entry: 3000 +/- 10%
    await client.post(_entries(drop), json={}, headers=h)
    a = (await client.get(f"/api/drops/{drop}/me", headers=h)).json()
    b = (await client.get(f"/api/drops/{drop}/me", headers=h)).json()
    assert strip_volatile(a, "poll_after_ms") == strip_volatile(b, "poll_after_ms")


async def test_me_poll_policy_by_state() -> None:
    from app.services.entries import poll_after_ms

    rng = random.Random(1)
    for args, base in [
        (("SCHEDULED", "fair", None), 5000),
        (("OPEN", "fair", None), 3000),
        (("OPEN", "fair", "REGISTERED"), 4000),
        (("CLOSED", "fair", "REGISTERED"), 1500),
        (("CLAIMING", "fair", "OFFERED"), 1000),
        (("CLAIMING", "fair", "WAITLISTED"), 2000),
        (("DONE", "fair", "ALLOCATED"), 15000),
        (("OPEN", "fifo", "REGISTERED"), 1000),
    ]:
        for _ in range(30):
            v = poll_after_ms(*args, redis_down=False, rng=rng)
            assert base * 0.9 - 1 <= v <= base * 1.1 + 1, (args, v)
    assert poll_after_ms("OPEN", "fair", None, redis_down=True, rng=rng) >= 5400 - 1


async def test_two_users_two_drops_are_independent(
    client: httpx.AsyncClient, db: asyncpg.Connection, app: FastAPI
) -> None:
    a, b = await _open(db), await _open(db)
    token, _ = await login(client)
    h = {"Authorization": f"Bearer {token}"}
    ea = (await client.post(_entries(a), json={}, headers=h)).json()["entry_id"]
    eb = (await client.post(_entries(b), json={}, headers=h)).json()["entry_id"]
    assert ea != eb
    assert (await client.get(f"/api/drops/{a}/me", headers=h)).json()["entry"]["entry_id"] == ea
