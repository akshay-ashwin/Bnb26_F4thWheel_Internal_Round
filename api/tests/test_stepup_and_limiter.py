"""Chunk 7: step-up (L8) and the abuse integration boundary (limiter slot, risk hooks)."""

from __future__ import annotations

import asyncio
import dataclasses
import uuid

import asyncpg
import httpx
import pytest
from fastapi import FastAPI
from starlette.requests import Request

import app.abuse
from app.abuse import Decision
from app.config import Settings
from app.main import create_app
from app.services.sweeper import sweep_drop
from tests.app_helpers import (
    TestUser,
    admin_create,
    admin_headers,
    admin_phase,
    claim_with,
    enter_all,
    fair_drawn_drop,
    make_users,
)


async def _flagged(
    client: httpx.AsyncClient, db: asyncpg.Connection, settings: Settings, entrants: int = 4
) -> tuple[str, list[TestUser], TestUser]:
    """A drawn Fair drop where entrant 0 carries a high risk score and wins -> STEP_UP_REQUIRED."""
    drop, users = await fair_drawn_drop(
        client, db, settings, capacity=entrants, entrants=entrants, risk={0: 90}
    )
    return drop, users, users[0]


async def _otp(client: httpx.AsyncClient, drop: str, user: TestUser) -> str:
    entry = (await client.get(f"/api/drops/{drop}/me", headers=user.headers)).json()["entry"]
    assert entry["status"] == "STEP_UP_REQUIRED" and entry["step_up_required"] is True
    assert entry["admission_token"] is None and entry["offer_expires_at"]
    return str(entry["dev_otp"])


async def _step(
    client: httpx.AsyncClient, drop: str, user: TestUser, otp: str, key: str | None = None
) -> httpx.Response:
    return await client.post(
        f"/api/drops/{drop}/step-up",
        json={"otp": otp},
        headers={**user.headers, "Idempotency-Key": key or str(uuid.uuid4())},
    )


async def test_flagged_winner_passes_step_up_then_claims(
    client: httpx.AsyncClient, db: asyncpg.Connection, settings: Settings
) -> None:
    drop, users, flagged = await _flagged(client, db, settings)
    otp = await _otp(client, drop, flagged)
    assert len(otp) == 6
    # no token and no seat until step-up passes
    assert (await claim_with(client, flagged, drop, "not-a-real-token")).status_code == 401
    r = await _step(client, drop, flagged, otp)
    assert r.status_code == 200 and r.json()["status"] == "OFFERED"
    me = (await client.get(f"/api/drops/{drop}/me", headers=flagged.headers)).json()["entry"]
    assert me["status"] == "OFFERED" and me["admission_token"] and "dev_otp" not in me
    assert (await claim_with(client, flagged, drop, me["admission_token"])).status_code == 200
    assert await db.fetchval(
        "SELECT step_up_passed_at IS NOT NULL FROM entries WHERE user_id = $1", flagged.user_id
    )


async def test_claim_before_step_up_is_423(
    client: httpx.AsyncClient, db: asyncpg.Connection, settings: Settings
) -> None:
    from tests.test_fair_claim import forge

    drop, users, flagged = await _flagged(client, db, settings)
    entry = str(await db.fetchval("SELECT id FROM entries WHERE user_id = $1", flagged.user_id))
    r = await claim_with(client, flagged, drop, forge(settings, drop, entry, flagged))
    assert r.status_code == 423 and r.json()["error"]["code"] == "STEP_UP_REQUIRED"
    assert await db.fetchval("SELECT count(*) FROM allocations") == 0


async def test_wrong_codes_count_and_three_strikes_release_the_seat(
    client: httpx.AsyncClient, db: asyncpg.Connection, settings: Settings, app: FastAPI
) -> None:
    drop, users = await fair_drawn_drop(
        client, db, settings, capacity=1, entrants=3, risk={0: 90, 1: 90, 2: 90}
    )
    winner = users[0]
    for u in users:
        if (
            await db.fetchval("SELECT status FROM entries WHERE user_id = $1", u.user_id)
            == "STEP_UP_REQUIRED"
        ):
            winner = u
            break
    otp = await _otp(client, drop, winner)
    wrong = "000000" if otp != "000000" else "111111"
    for _ in range(2):
        r = await _step(client, drop, winner, wrong)
        assert r.status_code == 401 and r.json()["error"]["code"] == "OTP_INVALID"
        assert (
            await db.fetchval("SELECT status FROM entries WHERE user_id = $1", winner.user_id)
            == "STEP_UP_REQUIRED"
        )
    third = await _step(client, drop, winner, wrong)
    assert third.status_code == 401
    assert (
        await db.fetchval("SELECT status FROM entries WHERE user_id = $1", winner.user_id)
        == "OFFER_EXPIRED"
    )
    after = await _step(client, drop, winner, otp)  # even the right code is too late now
    assert after.status_code == 409 and after.json()["error"]["code"] == "OFFER_EXPIRED"
    # the released seat goes to the next in rank at the next sweep
    res = await sweep_drop(
        app.state.pool, app.state.cache, settings, app.state.metrics, uuid.UUID(drop)
    )
    assert res.promoted == 1


async def test_step_up_is_idempotent(
    client: httpx.AsyncClient, db: asyncpg.Connection, settings: Settings
) -> None:
    drop, users, flagged = await _flagged(client, db, settings)
    otp = await _otp(client, drop, flagged)
    key = str(uuid.uuid4())
    a = await _step(client, drop, flagged, otp, key)
    b = await _step(client, drop, flagged, otp, key)  # same key: stored response
    c = await _step(client, drop, flagged, otp)  # new key after success: still 200 OFFERED
    assert (a.status_code, b.status_code, c.status_code) == (200, 200, 200)
    assert {a.json()["status"], b.json()["status"], c.json()["status"]} == {"OFFERED"}
    different = await _step(client, drop, flagged, "123456", key)  # same key, different body
    assert (
        different.status_code == 422
        and different.json()["error"]["code"] == "IDEMPOTENCY_KEY_REUSED"
    )


async def test_concurrent_step_ups_with_one_key_write_once(
    client: httpx.AsyncClient, db: asyncpg.Connection, settings: Settings
) -> None:
    drop, users, flagged = await _flagged(client, db, settings)
    otp = await _otp(client, drop, flagged)
    key = str(uuid.uuid4())
    rs = await asyncio.gather(*[_step(client, drop, flagged, otp, key) for _ in range(10)])
    assert {r.status_code for r in rs} == {200}
    assert (
        await db.fetchval("SELECT count(*) FROM idempotency_records WHERE endpoint = 'step-up'")
        == 1
    )


async def test_step_up_errors(
    client: httpx.AsyncClient, db: asyncpg.Connection, settings: Settings
) -> None:
    drop, users, flagged = await _flagged(client, db, settings)
    plain = users[1]  # unflagged winner: nothing to step up
    r = await _step(client, drop, plain, "123456")
    assert r.status_code == 403 and r.json()["error"]["code"] == "NOT_OFFERED"
    no_key = await client.post(
        f"/api/drops/{drop}/step-up", json={"otp": "123456"}, headers=flagged.headers
    )
    assert no_key.status_code == 400 and no_key.json()["error"]["code"] == "IDEMPOTENCY_KEY_MISSING"
    anon = await client.post(
        f"/api/drops/{drop}/step-up",
        json={"otp": "123456"},
        headers={"Idempotency-Key": str(uuid.uuid4())},
    )
    assert anon.status_code == 401


async def test_a_step_up_after_the_offer_window_is_409_and_releases_the_entry(
    client: httpx.AsyncClient, db: asyncpg.Connection, settings: Settings
) -> None:
    drop, users, flagged = await _flagged(client, db, settings)
    otp = await _otp(client, drop, flagged)
    await db.execute(
        "UPDATE entries SET offer_expires_at = now() - interval '1 second' WHERE user_id = $1",
        flagged.user_id,
    )
    r = await _step(client, drop, flagged, otp)
    assert r.status_code == 409 and r.json()["error"]["code"] == "OFFER_EXPIRED"
    assert (
        await db.fetchval("SELECT status FROM entries WHERE user_id = $1", flagged.user_id)
        == "OFFER_EXPIRED"
    )


async def test_a_lost_challenge_is_reissued_not_fatal(
    client: httpx.AsyncClient, db: asyncpg.Connection, settings: Settings, app: FastAPI
) -> None:
    drop, users, flagged = await _flagged(client, db, settings)
    await _otp(client, drop, flagged)
    await app.state.cache.client.flushdb()  # Redis lost everything
    r = await _step(client, drop, flagged, "123456")
    assert r.status_code == 401  # "a new code was sent"
    fresh = await _otp(client, drop, flagged)
    assert (await _step(client, drop, flagged, fresh)).status_code == 200


async def test_dev_otp_never_appears_outside_sim_mode(
    client: httpx.AsyncClient, db: asyncpg.Connection, settings: Settings
) -> None:
    drop, users, flagged = await _flagged(client, db, settings)
    prod_like = create_app(dataclasses.replace(settings, sim_mode=False))
    async with prod_like.router.lifespan_context(prod_like):
        transport = httpx.ASGITransport(app=prod_like)
        async with httpx.AsyncClient(transport=transport, base_url="http://t") as c:
            me = (await c.get(f"/api/drops/{drop}/me", headers=flagged.headers)).json()["entry"]
            assert me["status"] == "STEP_UP_REQUIRED" and "dev_otp" not in me
            stored = await prod_like.state.cache.client.hgetall(f"stepup:{me['entry_id']}")
            assert "dev_otp" not in stored  # not even stored


# --- the limiter slot ---


@pytest.fixture
def reject_all(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    seen: list[str] = []

    async def check(request: Request) -> Decision:
        seen.append(request.url.path)
        return Decision("reject", "L2", "RATE_LIMITED", 1500)

    monkeypatch.setattr(app.abuse, "check", check)
    return seen


async def test_a_rejecting_limiter_gets_the_contract_429(
    client: httpx.AsyncClient, db: asyncpg.Connection, reject_all: list[str]
) -> None:
    r = await client.post(
        "/api/auth/otp/request", json={"phone": "9876543210", "device_id": "device-test-0001"}
    )
    assert r.status_code == 429
    assert r.json()["error"] == {
        "code": "RATE_LIMITED",
        "message": "Slow down",
        "retry_after_ms": 1500,
    }
    assert (
        "server_time" in r.json()
        and r.headers["retry-after"] == "2"
        and "x-request-id" in r.headers
    )
    assert reject_all == ["/api/auth/otp/request"]
    assert await db.fetchval("SELECT count(*) FROM users") == 0  # rejected before routing


async def test_health_admin_and_telemetry_skip_the_limiter(
    client: httpx.AsyncClient, settings: Settings, reject_all: list[str]
) -> None:
    assert (await client.get("/api/healthz")).status_code == 200
    assert (await client.get("/api/readyz")).status_code == 200
    r = await client.post(
        "/api/admin/drops",
        headers=admin_headers(settings),
        json={"name": "n", "mode": "fair", "window_s": 5, "capacity": 5},
    )
    assert r.status_code == 201
    assert reject_all == []  # never consulted


async def test_the_limiter_can_name_any_layer_and_error_code(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def check(request: object) -> Decision:
        return Decision("reject", "L4", "TOKEN_INVALID", None)

    monkeypatch.setattr(app.abuse, "check", check)
    r = await client.get(f"/api/drops/{uuid.uuid4()}")
    assert r.status_code == 401 and r.json()["error"]["code"] == "TOKEN_INVALID"
    assert "retry-after" not in r.headers and "retry_after_ms" not in r.json()["error"]


async def test_a_crashing_limiter_fails_open(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def check(request: object) -> Decision:
        raise RuntimeError("redis scripting error")

    monkeypatch.setattr(app.abuse, "check", check)
    r = await client.get(f"/api/drops/{uuid.uuid4()}")
    assert r.status_code == 404  # served normally (not found), not blocked and not a 500


async def test_default_limiter_and_risk_hooks_allow_everything(client: httpx.AsyncClient) -> None:
    from starlette.requests import Request

    scope = {"type": "http", "method": "GET", "path": "/api/x", "headers": [], "query_string": b""}
    assert (await app.abuse.check(Request(scope))).outcome == "allow"
    assert await app.abuse.score_entry(
        drop_id="d", user_id="u", device_id=None, client_ip="1.1.1.1", ua_hash="h"
    ) == (0, [])
    await app.abuse.otp_request_guard(
        phone_hash="h", phone_prefix="987654", device_id="d", client_ip="1.1.1.1"
    )


async def test_an_otp_guard_can_throttle(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.errors import AppError

    async def guard(**_: object) -> None:
        raise AppError("OTP_THROTTLED", "Too many codes", retry_after_ms=30000)

    monkeypatch.setattr(app.abuse, "otp_request_guard", guard)
    r = await client.post(
        "/api/auth/otp/request", json={"phone": "9876543210", "device_id": "device-test-0001"}
    )
    assert r.status_code == 429 and r.json()["error"]["code"] == "OTP_THROTTLED"
    assert r.headers["retry-after"] == "30"


async def test_risk_scores_from_the_hook_are_stored_and_drive_step_up(
    client: httpx.AsyncClient,
    db: asyncpg.Connection,
    settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The integration contract: whatever score_entry returns is stored with the entry, and the
    draw routes high scores to STEP_UP_REQUIRED. The backend never decides who is a bot."""

    async def score(**kw: object) -> tuple[int, list[str]]:
        return 75, ["device_shared", "subnet_burst"]

    monkeypatch.setattr(app.abuse, "score_entry", score)
    drop = await admin_create(client, settings, capacity=2)
    await admin_phase(client, settings, drop, "open")
    users = await make_users(db, settings, 2)
    await enter_all(client, users, drop)
    rows = await db.fetch("SELECT risk_score, risk_flags::text AS flags FROM entries")
    assert {(r["risk_score"], r["flags"]) for r in rows} == {
        (75, '["device_shared", "subnet_burst"]')
    }
    await admin_phase(client, settings, drop, "close")
    await admin_phase(client, settings, drop, "draw")
    assert await db.fetchval("SELECT count(*) FROM entries WHERE status = 'STEP_UP_REQUIRED'") == 2
