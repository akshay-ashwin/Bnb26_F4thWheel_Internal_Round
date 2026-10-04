"""Helpers for tests that talk to the real application."""

from __future__ import annotations

import ipaddress
import uuid
from dataclasses import dataclass

import asyncpg
import httpx

from app.config import Settings
from app.security import new_public_id, sign_session

_phone_counter = 0


def fresh_phone() -> str:
    """A valid, unique Indian mobile number."""
    global _phone_counter
    _phone_counter += 1
    return f"9{(_phone_counter + int(uuid.uuid4().int % 10**5) * 10_000) % 10**9:09d}"


@dataclass(frozen=True)
class TestUser:
    __test__ = False  # not a pytest class
    user_id: uuid.UUID
    public_id: str
    session_id: uuid.UUID
    token: str

    @property
    def headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.token}"}


async def login(
    client: httpx.AsyncClient, phone: str | None = None, device_id: str = "device-test-0001"
) -> tuple[str, str]:
    """Run the real OTP flow; returns (session_token, user_public_id)."""
    phone = phone or fresh_phone()
    r = await client.post("/api/auth/otp/request", json={"phone": phone, "device_id": device_id})
    assert r.status_code == 200, r.text
    body = r.json()
    v = await client.post(
        "/api/auth/otp/verify",
        json={"request_id": body["request_id"], "otp": body["dev_otp"], "device_id": device_id},
    )
    assert v.status_code == 200, v.text
    client.cookies.clear()  # tests choose bearer or cookie explicitly; the jar must not decide
    return v.json()["session_token"], v.json()["user_public_id"]


async def make_user(
    conn: asyncpg.Connection, settings: Settings, *, device: str = "dev-fast-0001"
) -> TestUser:
    """Insert a user and a session directly (fast path for stress tests)."""
    public_id = new_public_id()
    user_id = await conn.fetchval(
        "INSERT INTO users (public_id, phone_hash, first_device_id)"
        " VALUES ($1, $2, $3) RETURNING id",
        public_id,
        uuid.uuid4().hex + uuid.uuid4().hex,
        device,
    )
    session_id = await conn.fetchval(
        "INSERT INTO sessions (user_id, device_id, ip) VALUES ($1, $2, $3) RETURNING id",
        user_id,
        device,
        ipaddress.ip_address("10.0.0.1"),
    )
    return TestUser(
        user_id,
        public_id,
        session_id,
        sign_session(settings.session_secret.get_secret_value(), session_id),
    )


async def make_users(
    conn: asyncpg.Connection, settings: Settings, count: int, *, device_prefix: str = "bulk"
) -> list[TestUser]:
    """Bulk version of make_user: two statements for any `count`."""
    public_ids = [new_public_id() for _ in range(count)]
    rows = await conn.fetch(
        "INSERT INTO users (public_id, phone_hash, first_device_id)"
        " SELECT p, md5(p) || md5(p || 'x'), $2 FROM unnest($1::text[]) AS p"
        " RETURNING id, public_id",
        public_ids,
        f"{device_prefix}-device",
    )
    by_public = {r["public_id"]: r["id"] for r in rows}
    sessions = await conn.fetch(
        "INSERT INTO sessions (user_id, device_id) SELECT u, $2 FROM unnest($1::uuid[]) AS u"
        " RETURNING id, user_id",
        [by_public[p] for p in public_ids],
        f"{device_prefix}-device",
    )
    session_of = {r["user_id"]: r["id"] for r in sessions}
    return [
        TestUser(
            by_public[p],
            p,
            session_of[by_public[p]],
            sign_session(settings.session_secret.get_secret_value(), session_of[by_public[p]]),
        )
        for p in public_ids
    ]


# --- drop flow helpers ---


async def enter_all(
    client: httpx.AsyncClient, users: list[TestUser], drop: str
) -> list[httpx.Response]:
    import asyncio

    return await asyncio.gather(
        *[client.post(f"/api/drops/{drop}/entries", json={}, headers=u.headers) for u in users]
    )


async def token_of(client: httpx.AsyncClient, user: TestUser, drop: str) -> str | None:
    me = await client.get(f"/api/drops/{drop}/me", headers=user.headers)
    assert me.status_code == 200, me.text
    entry = me.json()["entry"]
    return None if entry is None else entry["admission_token"]


async def tokens_of(
    client: httpx.AsyncClient, users: list[TestUser], drop: str
) -> list[str | None]:
    import asyncio

    return list(await asyncio.gather(*[token_of(client, u, drop) for u in users]))


async def claim_with(
    client: httpx.AsyncClient,
    user: TestUser,
    drop: str,
    token: str | None,
    key: str | None = None,
) -> httpx.Response:
    """POST /claim. A missing token (None) is sent as an obviously invalid one."""
    return await client.post(
        f"/api/drops/{drop}/claim",
        json={"admission_token": token or "no-token-was-available"},
        headers={**user.headers, "Idempotency-Key": key or str(uuid.uuid4())},
    )


def percentiles(samples: list[float]) -> str:
    ordered = sorted(samples)

    def pick(p: float) -> float:
        return ordered[min(len(ordered) - 1, int(len(ordered) * p))]

    return (
        f"p50={pick(0.5) * 1000:.0f}ms p95={pick(0.95) * 1000:.0f}ms p99={pick(0.99) * 1000:.0f}ms"
    )


# --- admin / fair-drop helpers ---


def admin_headers(settings: Settings) -> dict[str, str]:
    return {"X-Admin-Key": settings.admin_key.get_secret_value()}


async def admin_create(
    client: httpx.AsyncClient,
    settings: Settings,
    *,
    mode: str = "fair",
    capacity: int = 500,
    window_s: int = 60,
    claim_window_s: int = 120,
) -> str:
    r = await client.post(
        "/api/admin/drops",
        json={
            "name": "Test drop",
            "capacity": capacity,
            "mode": mode,
            "window_s": window_s,
            "claim_window_s": claim_window_s,
        },
        headers=admin_headers(settings),
    )
    assert r.status_code == 201, r.text
    return str(r.json()["drop_id"])


async def admin_phase(
    client: httpx.AsyncClient,
    settings: Settings,
    drop: str,
    action: str,
    *,
    mode: str | None = None,
) -> httpx.Response:
    body: dict[str, str] = {"action": action}
    if mode:
        body["mode"] = mode
    return await client.post(
        f"/api/admin/drops/{drop}/phase", json=body, headers=admin_headers(settings)
    )


async def fair_drawn_drop(
    client: httpx.AsyncClient,
    db: asyncpg.Connection,
    settings: Settings,
    *,
    capacity: int,
    entrants: int,
    claim_window_s: int = 120,
    risk: dict[int, int] | None = None,
) -> tuple[str, list[TestUser]]:
    """Create a Fair drop, open it, enter `entrants` users, close and draw. `risk` maps entrant
    index -> risk_score to plant before the draw (what Saanvi's L7 would have stored)."""
    drop = await admin_create(client, settings, capacity=capacity, claim_window_s=claim_window_s)
    assert (await admin_phase(client, settings, drop, "open")).status_code == 200
    users = await make_users(db, settings, entrants)
    rs = await enter_all(client, users, drop)
    assert {r.status_code for r in rs} == {201}
    if risk:
        for idx, score in risk.items():
            await db.execute(
                "UPDATE entries SET risk_score = $2 WHERE drop_id = $1 AND user_id = $3",
                uuid.UUID(drop),
                score,
                users[idx].user_id,
            )
    assert (await admin_phase(client, settings, drop, "close")).json()["phase"] == "CLOSED"
    drawn = await admin_phase(client, settings, drop, "draw")
    assert drawn.status_code == 200 and drawn.json()["phase"] == "CLAIMING", drawn.text
    return drop, users


def strip_volatile(payload: dict[str, object], *extra: str) -> dict[str, object]:
    """A response body without `server_time` (and any `extra` fields), for equality checks."""
    return {k: v for k, v in payload.items() if k not in ("server_time", *extra)}
