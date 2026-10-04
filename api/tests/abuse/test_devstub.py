"""End-to-end contract flows through the dev stub (real abuse middleware, real Redis)."""

from __future__ import annotations

import asyncio
import os
import uuid
from collections.abc import AsyncIterator
from http.cookiejar import DefaultCookiePolicy
from typing import Any

import httpx
import pytest
from redis.asyncio import Redis

from app.abuse.config import AbuseConfig

os.environ["SIM_MODE"] = "true"

import app.abuse.devstub.app as stub  # noqa: E402

ADMIN = {"X-Admin-Key": stub.ADMIN_KEY}


@pytest.fixture
async def client(redis: Redis) -> AsyncIterator[httpx.AsyncClient]:
    stub.S = stub.Store()
    stub.REDIS = redis
    stub.LIMITER.redis = redis
    stub.CONFIG.redis = redis
    stub.CONFIG._cfg = AbuseConfig()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=stub.app), base_url="http://t"
    ) as c:
        # Many identities share this client: never let one user's session cookie leak into
        # another user's requests (cookies take precedence over the bearer token).
        c.cookies.jar.set_policy(DefaultCookiePolicy(allowed_domains=[]))
        yield c


class User:
    def __init__(self, c: httpx.AsyncClient, i: int) -> None:
        self.c, self.ip = c, f"21.{i // 250}.{i % 250}.7"
        self.phone = f"+91{7_000_000_000 + (i * 7_919_993) % 2_999_999_999}"  # not sequential
        self.device, self.token = f"dev-{i}", ""

    def h(self, **extra: str) -> dict[str, str]:
        out = {"X-Sim-Client-IP": self.ip, "User-Agent": "Mozilla/5.0 test"}
        if self.token:
            out["Authorization"] = f"Bearer {self.token}"
        return {**out, **extra}

    async def login(self) -> None:
        r = await self.c.post(
            "/api/auth/otp/request",
            json={"phone": self.phone, "device_id": self.device},
            headers=self.h(),
        )
        b = r.json()
        await asyncio.sleep(0)
        r = await self.c.post(
            "/api/auth/otp/verify",
            json={"request_id": b["request_id"], "otp": b["dev_otp"], "device_id": self.device},
            headers=self.h(),
        )
        self.token = r.json()["session_token"]

    async def me(self, drop: str) -> dict[str, Any]:
        r = await self.c.get(f"/api/drops/{drop}/me", headers=self.h())
        assert r.status_code == 200, r.text
        body: dict[str, Any] = r.json()
        return body

    async def claim(self, drop: str, tok: str, key: str | None = None) -> httpx.Response:
        return await self.c.post(
            f"/api/drops/{drop}/claim",
            json={"admission_token": tok},
            headers=self.h(**{"Idempotency-Key": key or str(uuid.uuid4())}),
        )


async def new_drop(c: httpx.AsyncClient, mode: str, capacity: int) -> str:
    r = await c.post(
        "/api/admin/drops",
        headers=ADMIN,
        json={
            "name": "t",
            "capacity": capacity,
            "mode": mode,
            "window_s": 60,
            "claim_window_s": 60,
        },
    )
    assert r.status_code == 201
    drop: str = r.json()["drop_id"]
    assert (
        await c.post(f"/api/admin/drops/{drop}/phase", headers=ADMIN, json={"action": "open"})
    ).json()["phase"] == "OPEN"
    return drop


async def integrity(c: httpx.AsyncClient, drop: str) -> dict[str, Any]:
    body: dict[str, Any] = (await c.get(f"/api/admin/drops/{drop}/integrity", headers=ADMIN)).json()
    return body


async def test_fifo_flow_and_no_oversell_under_concurrent_claims(client: httpx.AsyncClient) -> None:
    drop = await new_drop(client, "fifo", 10)
    users = [User(client, i) for i in range(40)]
    for u in users:
        await u.login()
        assert (await client.post(f"/api/drops/{drop}/entries", headers=u.h())).status_code == 201
    toks = [(await u.me(drop))["entry"]["admission_token"] for u in users]

    async def tabs(u: User, tok: str) -> list[int]:  # every user claims from 3 tabs at once
        rs = await asyncio.gather(*(u.claim(drop, tok) for _ in range(3)))
        return [r.status_code for r in rs]

    await asyncio.gather(*(tabs(u, t) for u, t in zip(users, toks, strict=True)))
    integ = await integrity(client, drop)
    assert integ["sold"] == 10 and integ["oversold"] == 0 and integ["invariant_ok"]


async def test_entry_is_idempotent_and_needs_a_session(client: httpx.AsyncClient) -> None:
    drop = await new_drop(client, "fair", 5)
    u = User(client, 1)
    assert (await client.post(f"/api/drops/{drop}/entries", headers=u.h())).status_code == 401
    await u.login()
    codes = [
        (await client.post(f"/api/drops/{drop}/entries", headers=u.h())).status_code
        for _ in range(5)
    ]
    assert codes == [201, 200, 200, 200, 200]
    export = (await client.get(f"/api/admin/drops/{drop}/export", headers=ADMIN)).text.splitlines()
    assert len(export) == 1


async def run_fair(
    client: httpx.AsyncClient, l7: bool, step_up_score: int = 60
) -> tuple[str, dict[str, int], list[User]]:
    stub.S = stub.Store()
    await client.put(
        "/api/admin/abuse/config",
        headers=ADMIN,
        json={"layers": {"L7": l7}, "thresholds": {"step_up_score": step_up_score}},
    )
    drop = await new_drop(client, "fair", 5)
    users = [User(client, i) for i in range(20)]
    for u in users:
        await u.login()
        await client.post(f"/api/drops/{drop}/entries", headers=u.h())
    for action in ("close", "draw"):
        await client.post(f"/api/admin/drops/{drop}/phase", headers=ADMIN, json={"action": action})
    ranks = {}
    for line in (
        await client.get(f"/api/admin/drops/{drop}/export", headers=ADMIN)
    ).text.splitlines():
        import json

        row = json.loads(line)
        ranks[row["user_public_id"]] = row["rank"]
    return drop, ranks, users


def test_risk_never_changes_ranks() -> None:
    """Same drop, seed and entries drawn with step-up on and off: identical ranks; only
    OFFERED vs STEP_UP_REQUIRED differs for flagged winners (invariant 5)."""
    import copy

    from app.abuse.risk import EntryContext

    base = stub.Drop("d1", "t", 10, "fair", 60, 60, "b" * 64, phase="CLOSED")
    for i in range(50):
        ctx = EntryContext(f"e{i}", f"u{i}", "dev", "1.1.1.1", "ua", 0.0, 0.0)
        e = stub.Entry(f"e{i}", f"u{i}", f"u_pub{i:03d}", 0.0, ctx, risk_score=(i * 37) % 101)
        base.entries[e.user_id] = e
    on, off = copy.deepcopy(base), copy.deepcopy(base)
    stub.draw(on, True, 60, 1000.0)
    stub.draw(off, False, 60, 1000.0)
    assert {k: e.rank for k, e in on.entries.items()} == {k: e.rank for k, e in off.entries.items()}
    for k, e in on.entries.items():
        o = off.entries[k]
        if e.status != o.status:
            assert (e.status, o.status) == ("STEP_UP_REQUIRED", "OFFERED")
            assert e.risk_score >= 60
    assert any(e.status == "STEP_UP_REQUIRED" for e in on.entries.values())


async def test_fair_offer_claim_replay_and_step_up(client: httpx.AsyncClient) -> None:
    drop, _ranks, users = await run_fair(client, True, step_up_score=1)
    winners = [u for u in users if (await u.me(drop))["entry"]["status"] == "STEP_UP_REQUIRED"]
    assert len(winners) == 5  # everyone scores >= 1 point here, so every winner steps up
    w, other = winners[0], next(u for u in users if u not in winners)
    body = await w.me(drop)
    assert body["entry"]["admission_token"] is None  # present and null (contract)
    assert (await w.claim(drop, "x.y.z")).status_code == 401
    r = await client.post(
        f"/api/drops/{drop}/step-up",
        json={"otp": body["entry"]["dev_otp"]},
        headers=w.h(**{"Idempotency-Key": str(uuid.uuid4())}),
    )
    assert r.json()["status"] == "OFFERED"
    tok = (await w.me(drop))["entry"]["admission_token"]
    replay = await other.claim(drop, tok)  # someone else's token, from another session
    assert replay.status_code == 401 and replay.json()["error"]["code"] == "TOKEN_INVALID"
    key = str(uuid.uuid4())
    first, again = await w.claim(drop, tok, key), await w.claim(drop, tok, key)
    assert first.status_code == again.status_code == 200
    assert first.json()["seat_no"] == again.json()["seat_no"]
    assert (await integrity(client, drop))["sold"] == 1
