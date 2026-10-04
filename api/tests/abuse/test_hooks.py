"""The backend boundary (`app.abuse.check` and the L6/L7 hooks) driven through the REAL backend
app (Postgres + Redis db 15, the shared `client` fixture), with ABUSE_ENFORCE on and off."""

from __future__ import annotations

import asyncio
import uuid

import asyncpg
import httpx
import pytest

from app.config import Settings
from tests.app_helpers import admin_create, admin_phase, enter_all, fresh_phone, make_users

ANON_GETS = 60  # L2 anonymous budget per IP is 20/s with burst 40


@pytest.fixture
def enforce(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ABUSE_ENFORCE", "true")


async def _flood(client: httpx.AsyncClient, ip: str) -> list[httpx.Response]:
    drop = uuid.uuid4()
    return [
        await client.get(f"/api/drops/{drop}", headers={"X-Sim-Client-IP": ip})
        for _ in range(ANON_GETS)
    ]


async def test_switched_off_the_hooks_allow_everything(client: httpx.AsyncClient) -> None:
    assert {r.status_code for r in await _flood(client, "7.7.7.7")} == {404}


async def test_an_anonymous_flood_gets_the_contract_429(
    client: httpx.AsyncClient, settings: Settings, enforce: None
) -> None:
    rs = await _flood(client, "7.7.7.8")
    limited = [r for r in rs if r.status_code == 429]
    assert limited and rs[0].status_code == 404
    body = limited[0].json()
    assert body["error"]["code"] == "RATE_LIMITED" and body["error"]["retry_after_ms"] > 0
    assert "server_time" in body and "retry-after" in limited[0].headers
    # another IP is unaffected, and health / admin are never limited
    assert (await client.get(f"/api/drops/{uuid.uuid4()}")).status_code == 404
    assert (await client.get("/api/healthz")).status_code == 200
    h = {"X-Admin-Key": settings.admin_key.get_secret_value(), "X-Sim-Client-IP": "7.7.7.8"}
    assert (await client.get("/api/admin/drops", headers=h)).status_code == 200


async def test_repeated_codes_for_one_phone_are_otp_throttled(
    client: httpx.AsyncClient, enforce: None
) -> None:
    phone = fresh_phone()
    codes = []
    for i in range(4):
        r = await client.post(
            "/api/auth/otp/request",
            json={"phone": phone, "device_id": f"device-hooks-{i:04d}"},
            headers={"X-Sim-Client-IP": f"7.7.{i}.9"},
        )
        codes.append(r.status_code)
        if r.status_code == 429:
            assert r.json()["error"]["code"] == "OTP_THROTTLED"
            assert int(r.headers["retry-after"]) >= 1
    assert codes[-1] == 429


async def test_the_draw_rescore_flags_early_members_of_a_shared_device(
    client: httpx.AsyncClient, db: asyncpg.Connection, settings: Settings, enforce: None
) -> None:
    """Four accounts on one device: at entry time only the 4th crosses R_DEVICE (> 3 users);
    the re-score inside the draw transaction flags all four. Score only, never rank."""
    drop = await admin_create(client, settings, capacity=10)
    await admin_phase(client, settings, drop, "open")
    users = await make_users(db, settings, 4, device_prefix="shared")
    for u in users:  # sequential, so entry-time scores grow with the cluster
        r = await enter_all(client, [u], drop)
        assert r[0].status_code == 201
    at_entry = sorted(r["risk_score"] for r in await db.fetch("SELECT risk_score FROM entries"))
    assert at_entry == [0, 0, 0, 40]
    await admin_phase(client, settings, drop, "close")
    assert (await admin_phase(client, settings, drop, "draw")).status_code == 200
    rows = await db.fetch("SELECT risk_score, risk_flags::text AS f, draw_rank FROM entries")
    assert all(r["risk_score"] == 40 and "device_shared" in r["f"] for r in rows)
    assert sorted(r["draw_rank"] for r in rows) == [1, 2, 3, 4]


async def test_a_flood_the_limiter_rejects_cannot_take_sign_in_down(
    client: httpx.AsyncClient, enforce: None
) -> None:
    """Regression (real-backend run, seed 101): the limiter shared the backend's Redis pool, a
    flood exhausted it, and concurrent sign-ins got 503 (MaxConnectionsError opened the
    breaker). The limiter now has its own pool; flood and sign-ins run at the same time."""
    drop = uuid.uuid4()

    async def flood(i: int) -> httpx.Response:
        return await client.get(f"/api/drops/{drop}", headers={"X-Sim-Client-IP": f"6.6.{i % 4}.6"})

    async def sign_in(i: int) -> httpx.Response:
        return await client.post(
            "/api/auth/otp/request",
            json={"phone": fresh_phone(), "device_id": f"device-flood-{i:04d}"},
            headers={"X-Sim-Client-IP": f"8.8.{i}.8"},
        )

    rs = await asyncio.gather(*(flood(i) for i in range(400)), *(sign_in(i) for i in range(20)))
    floods, sign_ins = rs[:400], rs[400:]
    assert any(r.status_code == 429 for r in floods)  # the limiter still enforces
    assert [r.status_code for r in sign_ins] == [200] * 20
