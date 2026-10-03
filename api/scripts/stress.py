"""Real-HTTP stress run against a RUNNING stack (multi-worker uvicorn, real Postgres and Redis).

    docker compose up -d --wait api
    docker compose run --rm --no-deps -e PYTHONPATH=/app api python scripts/stress.py [scenario ...]

Not part of the unit-test suite (it needs the whole stack and takes a minute). Each scenario builds
its own drop, drives it over HTTP, then asks the admin API for the integrity view and checks the
invariants: allocated <= capacity, unique seats and allocation ids, integrity ok. Every figure it
prints is measured by this run; nothing is estimated.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import time
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

import asyncpg
import httpx

from app.security import encode_token, new_public_id, sid_hash, sign_session

BASE = os.environ.get("STRESS_BASE", "http://api:8000")
ADMIN = {"X-Admin-Key": os.environ["ADMIN_KEY"]}
SESSION_SECRET = os.environ["SESSION_SECRET"]
TOKEN_KEY = os.environ["TOKEN_SIGNING_KEY"]


@dataclass
class User:
    user_id: uuid.UUID
    public_id: str
    session_id: uuid.UUID
    token: str

    @property
    def headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.token}"}


def pct(samples: list[float]) -> str:
    if not samples:
        return "n/a"
    s = sorted(samples)

    def pick(p: float) -> float:
        return s[min(len(s) - 1, int(len(s) * p))] * 1000

    return f"p50={pick(0.5):.0f}ms p95={pick(0.95):.0f}ms p99={pick(0.99):.0f}ms max={s[-1] * 1000:.0f}ms"


# At most this many requests are in flight. It must not be smaller than the connection pool: httpx
# slows down badly (the client, not the server) when thousands of tasks queue for connections.
MAX_IN_FLIGHT = 1200
_gate = asyncio.Semaphore(MAX_IN_FLIGHT)


class Timed:
    def __init__(self, client: httpx.AsyncClient) -> None:
        self.client = client
        self.lat: list[float] = []
        self.codes: dict[int, int] = {}

    async def request(self, method: str, url: str, **kw: object) -> httpx.Response:
        async with _gate:
            started = time.perf_counter()
            try:
                r = await self.client.request(method, url, **kw)  # type: ignore[arg-type]
            except httpx.HTTPError:
                self.codes[0] = self.codes.get(0, 0) + 1
                raise
        self.lat.append(time.perf_counter() - started)
        self.codes[r.status_code] = self.codes.get(r.status_code, 0) + 1
        return r


async def make_users(conn: asyncpg.Connection, count: int) -> list[User]:
    public_ids = [new_public_id() for _ in range(count)]
    rows = await conn.fetch(
        "INSERT INTO users (public_id, phone_hash, first_device_id)"
        " SELECT p, md5(p) || md5(p || 'stress'), 'stress-device' FROM unnest($1::text[]) p"
        " RETURNING id, public_id",
        public_ids,
    )
    by_public = {r["public_id"]: r["id"] for r in rows}
    sessions = await conn.fetch(
        "INSERT INTO sessions (user_id, device_id) SELECT u, 'stress-device' FROM unnest($1::uuid[]) u"
        " RETURNING id, user_id",
        [by_public[p] for p in public_ids],
    )
    session_of = {r["user_id"]: r["id"] for r in sessions}
    return [
        User(
            by_public[p],
            p,
            session_of[by_public[p]],
            sign_session(SESSION_SECRET, session_of[by_public[p]]),
        )
        for p in public_ids
    ]


async def admin_create(
    c: httpx.AsyncClient, mode: str, capacity: int, claim_window_s: int = 300
) -> str:
    r = await c.post(
        "/api/admin/drops",
        headers=ADMIN,
        json={
            "name": f"stress-{mode}",
            "capacity": capacity,
            "mode": mode,
            "window_s": 600,
            "claim_window_s": claim_window_s,
        },
    )
    r.raise_for_status()
    return str(r.json()["drop_id"])


async def phase(c: httpx.AsyncClient, drop: str, action: str) -> str:
    r = await c.post(f"/api/admin/drops/{drop}/phase", headers=ADMIN, json={"action": action})
    r.raise_for_status()
    return str(r.json()["phase"])


async def integrity(c: httpx.AsyncClient, drop: str) -> dict[str, object]:
    r = await c.get(f"/api/admin/drops/{drop}/integrity", headers=ADMIN)
    r.raise_for_status()
    body: dict[str, object] = r.json()
    return body


def forge(drop: str, entry_id: str, u: User) -> str:
    now = int(time.time())
    return encode_token(
        TOKEN_KEY,
        {
            "drop_id": drop,
            "entry_id": entry_id,
            "sid_hash": sid_hash(u.session_id),
            "jti": uuid.uuid4().hex[:16],
            "iat": now,
            "exp": now + 60,
            "run": 1,
        },
    )


async def enter_and_token(t: Timed, drop: str, users: list[User]) -> list[str | None]:
    rs = await asyncio.gather(
        *[t.request("POST", f"/api/drops/{drop}/entries", headers=u.headers) for u in users]
    )
    assert all(r.status_code in (200, 201) for r in rs), {r.status_code for r in rs}

    async def token(u: User) -> str | None:
        me = await t.request("GET", f"/api/drops/{drop}/me", headers=u.headers)
        entry = me.json()["entry"]
        return None if entry is None else entry["admission_token"]

    return list(await asyncio.gather(*[token(u) for u in users]))


def check(report: dict[str, object], inv: dict[str, object], *, sold: int, capacity: int) -> None:
    extra = inv["extra"]
    assert isinstance(extra, dict)
    assert inv["invariant_ok"] is True, inv
    assert inv["oversold"] == 0 and inv["duplicate_entries_with_seats"] == 0, inv
    assert inv["sold"] == sold <= capacity, (inv["sold"], sold, capacity)
    assert extra["allocations_count"] == sold
    report["integrity"] = (
        f"sold={inv['sold']} free={inv['free']} oversold={inv['oversold']} dup={inv['duplicate_entries_with_seats']} ok={inv['invariant_ok']}"
    )


# --- scenarios ---


async def a_fifo_1000_claims_500_seats(
    c: httpx.AsyncClient, db: asyncpg.Connection
) -> dict[str, object]:
    drop = await admin_create(c, "fifo", 500)
    await phase(c, drop, "open")
    users = await make_users(db, 1000)
    t = Timed(c)
    tokens = await enter_and_token(t, drop, users)
    claim = Timed(c)
    started = time.perf_counter()
    rs = await asyncio.gather(
        *[
            claim.request(
                "POST",
                f"/api/drops/{drop}/claim",
                json={"admission_token": tok},
                headers={**u.headers, "Idempotency-Key": str(uuid.uuid4())},
            )
            for u, tok in zip(users, tokens, strict=True)
        ]
    )
    wall = time.perf_counter() - started
    ok = [r for r in rs if r.status_code == 200]
    out = [r for r in rs if r.status_code == 409]
    assert len(ok) == 500 and len(out) == 500, (len(ok), len(out), claim.codes)
    assert len({r.json()["seat_no"] for r in ok}) == 500
    rep: dict[str, object] = {
        "claims": f"200={len(ok)} 409={len(out)} wall={wall:.2f}s {pct(claim.lat)}",
        "codes": claim.codes,
    }
    check(rep, await integrity(c, drop), sold=500, capacity=500)
    return rep


async def fair_drop(
    c: httpx.AsyncClient, db: asyncpg.Connection, capacity: int, entrants: int
) -> tuple[str, list[User], list[str | None], Timed]:
    drop = await admin_create(c, "fair", capacity)
    await phase(c, drop, "open")
    users = await make_users(db, entrants)
    t = Timed(c)
    rs = await asyncio.gather(
        *[t.request("POST", f"/api/drops/{drop}/entries", headers=u.headers) for u in users]
    )
    assert all(r.status_code == 201 for r in rs)
    await phase(c, drop, "close")
    assert await phase(c, drop, "draw") == "CLAIMING"

    async def token(u: User) -> str | None:
        me = await t.request("GET", f"/api/drops/{drop}/me", headers=u.headers)
        entry = me.json()["entry"]
        return None if entry is None else entry["admission_token"]

    return drop, users, list(await asyncio.gather(*[token(u) for u in users])), t


async def b_fair_500_winners_claim(
    c: httpx.AsyncClient, db: asyncpg.Connection
) -> dict[str, object]:
    drop, users, tokens, _ = await fair_drop(c, db, 500, 700)
    winners = [(u, t) for u, t in zip(users, tokens, strict=True) if t]
    assert len(winners) == 500
    claim = Timed(c)
    started = time.perf_counter()
    rs = await asyncio.gather(
        *[
            claim.request(
                "POST",
                f"/api/drops/{drop}/claim",
                json={"admission_token": tok},
                headers={**u.headers, "Idempotency-Key": str(uuid.uuid4())},
            )
            for u, tok in winners
        ]
    )
    wall = time.perf_counter() - started
    assert {r.status_code for r in rs} == {200}, claim.codes
    assert sorted(r.json()["seat_no"] for r in rs) == list(range(1, 501))
    rep: dict[str, object] = {"claims": f"200=500 wall={wall:.2f}s {pct(claim.lat)}"}
    check(rep, await integrity(c, drop), sold=500, capacity=500)
    return rep


async def c_fair_1000_attempts_500_seats(
    c: httpx.AsyncClient, db: asyncpg.Connection
) -> dict[str, object]:
    drop, users, tokens, _ = await fair_drop(c, db, 500, 1000)
    entry_ids = {
        r["user_id"]: str(r["id"])
        for r in await db.fetch(
            "SELECT id, user_id FROM entries WHERE drop_id = $1", uuid.UUID(drop)
        )
    }
    claim = Timed(c)
    rs = await asyncio.gather(
        *[
            claim.request(
                "POST",
                f"/api/drops/{drop}/claim",
                json={"admission_token": tok or forge(drop, entry_ids[u.user_id], u)},
                headers={**u.headers, "Idempotency-Key": str(uuid.uuid4())},
            )
            for u, tok in zip(users, tokens, strict=True)
        ]
    )
    ok = [r for r in rs if r.status_code == 200]
    refused = [r for r in rs if r.status_code == 403]
    assert len(ok) == 500 and len(refused) == 500, claim.codes
    rep: dict[str, object] = {
        "claims": f"200={len(ok)} 403(NOT_OFFERED)={len(refused)} {pct(claim.lat)}"
    }
    check(rep, await integrity(c, drop), sold=500, capacity=500)
    return rep


async def d_five_tabs(c: httpx.AsyncClient, db: asyncpg.Connection) -> dict[str, object]:
    drop, users, tokens, _ = await fair_drop(c, db, 300, 300)
    claim = Timed(c)
    rs = await asyncio.gather(
        *[
            claim.request(
                "POST",
                f"/api/drops/{drop}/claim",
                json={"admission_token": tok},
                headers={**u.headers, "Idempotency-Key": str(uuid.uuid4())},
            )
            for u, tok in zip(users, tokens, strict=True)
            for _ in range(5)
        ]
    )
    assert {r.status_code for r in rs} == {200}, claim.codes
    for i in range(0, len(rs), 5):
        assert len({r.json()["seat_no"] for r in rs[i : i + 5]}) == 1
    rep: dict[str, object] = {
        "claims": f"1500 requests from 300 users x 5 tabs, all 200 {pct(claim.lat)}"
    }
    check(rep, await integrity(c, drop), sold=300, capacity=300)
    return rep


async def e_idempotent_retry_and_f_replay(
    c: httpx.AsyncClient, db: asyncpg.Connection
) -> dict[str, object]:
    drop, users, tokens, _ = await fair_drop(c, db, 20, 20)
    u, tok = users[0], tokens[0]
    key = str(uuid.uuid4())
    first = await c.post(
        f"/api/drops/{drop}/claim",
        json={"admission_token": tok},
        headers={**u.headers, "Idempotency-Key": key},
    )
    retry = await c.post(
        f"/api/drops/{drop}/claim",
        json={"admission_token": tok},
        headers={**u.headers, "Idempotency-Key": key},
    )
    new_key = await c.post(
        f"/api/drops/{drop}/claim",
        json={"admission_token": tok},
        headers={**u.headers, "Idempotency-Key": str(uuid.uuid4())},
    )
    assert {first.status_code, retry.status_code, new_key.status_code} == {200}
    assert (
        first.json()["allocation_id"]
        == retry.json()["allocation_id"]
        == new_key.json()["allocation_id"]
    )
    thief = users[1]
    stolen = await c.post(
        f"/api/drops/{drop}/claim",
        json={"admission_token": tokens[2]},
        headers={**thief.headers, "Idempotency-Key": str(uuid.uuid4())},
    )
    assert stolen.status_code == 401 and stolen.json()["error"]["code"] == "TOKEN_INVALID"
    t = Timed(c)
    conc = await asyncio.gather(
        *[
            t.request(
                "POST",
                f"/api/drops/{drop}/claim",
                json={"admission_token": tokens[3]},
                headers={**users[3].headers, "Idempotency-Key": str(uuid.uuid4())},
            )
            for _ in range(40)
        ]
    )
    assert {r.status_code for r in conc} == {200} and len(
        {r.json()["allocation_id"] for r in conc}
    ) == 1
    rep: dict[str, object] = {
        "retry": "same key / new key / replayed token -> one allocation; cross-session token -> 401; 40 concurrent replays -> one seat"
    }
    check(rep, await integrity(c, drop), sold=2, capacity=20)
    return rep


async def g_network_change(c: httpx.AsyncClient, db: asyncpg.Connection) -> dict[str, object]:
    drop = await admin_create(c, "fair", 5)
    await phase(c, drop, "open")
    [u] = await make_users(db, 1)
    seen = []
    for ip, ua in [
        ("198.51.100.4", "Mozilla/5.0 (Wi-Fi)"),
        ("203.0.113.88", "Mozilla/5.0 (mobile data)"),
        ("2001:db8::7", "Mozilla/5.0 (new device)"),
    ]:
        r = await c.post(
            f"/api/drops/{drop}/entries",
            headers={**u.headers, "X-Sim-Client-IP": ip, "User-Agent": ua},
        )
        seen.append(r.status_code)
    assert seen == [201, 200, 200], seen
    return {
        "network": f"one session across 3 IPs / user agents: {seen} (one entry, never logged out)"
    }


async def h_repeated_entries(c: httpx.AsyncClient, db: asyncpg.Connection) -> dict[str, object]:
    drop = await admin_create(c, "fair", 100)
    await phase(c, drop, "open")
    users = await make_users(db, 500)
    t = Timed(c)
    started = time.perf_counter()
    rs = await asyncio.gather(
        *[
            t.request("POST", f"/api/drops/{drop}/entries", headers=u.headers)
            for u in users
            for _ in range(5)
        ]
    )
    wall = time.perf_counter() - started
    assert t.codes.get(201) == 500 and t.codes.get(200) == 2000 and len(rs) == 2500, t.codes
    n = await db.fetchval("SELECT count(*) FROM entries WHERE drop_id = $1", uuid.UUID(drop))
    assert n == 500
    return {
        "entries": f"500 users x 5 attempts = 2500 requests -> 500 entries (201={t.codes[201]} 200={t.codes[200]}) wall={wall:.2f}s {pct(t.lat)}"
    }


async def j_genuine_users_during_a_flood(
    c: httpx.AsyncClient, db: asyncpg.Connection
) -> dict[str, object]:
    """Backend capacity only: the default limiter allows everything, so this shows what the API
    itself does when 20,000 junk requests arrive at the same time as genuine entries."""
    drop = await admin_create(c, "fair", 100)
    await phase(c, drop, "open")
    users = await make_users(db, 200)
    genuine = Timed(c)
    flood = Timed(c)

    async def bot(i: int) -> None:
        kind = i % 4
        try:
            if kind == 0:
                await flood.request("POST", f"/api/drops/{drop}/entries")  # no session
            elif kind == 1:
                await flood.request(
                    "POST",
                    f"/api/drops/{drop}/entries",
                    headers={"Authorization": "Bearer forged.token"},
                )
            elif kind == 2:
                await flood.request(
                    "POST",
                    "/api/auth/otp/verify",
                    json={"request_id": "x" * 16, "otp": "000000", "device_id": "bot-device-01"},
                )
            else:
                await flood.request("GET", f"/api/drops/{drop}")
        except httpx.HTTPError:
            pass

    async def human(u: User) -> int:
        await asyncio.sleep(0.2)
        last = 0
        for _ in range(5):  # a genuine user retries up to five times
            r = await genuine.request("POST", f"/api/drops/{drop}/entries", headers=u.headers)
            last = r.status_code
            await asyncio.sleep(0.05)
        return last

    started = time.perf_counter()
    results = await asyncio.gather(*[bot(i) for i in range(20_000)], *[human(u) for u in users])
    wall = time.perf_counter() - started
    humans = results[20_000:]
    entries = await db.fetchval("SELECT count(*) FROM entries WHERE drop_id = $1", uuid.UUID(drop))
    assert entries == 200, entries
    assert all(code in (200, 201) for code in humans), genuine.codes
    return {
        "flood": f"20,000 junk requests {flood.codes} {pct(flood.lat)}",
        "genuine": f"200 users x 5 attempts: statuses {genuine.codes}, every user entered; wall={wall:.2f}s {pct(genuine.lat)}",
    }


SCENARIOS: dict[
    str, Callable[[httpx.AsyncClient, asyncpg.Connection], Awaitable[dict[str, object]]]
] = {
    "A_fifo_1000_claims_500_seats": a_fifo_1000_claims_500_seats,
    "B_fair_500_winners_claim": b_fair_500_winners_claim,
    "C_fair_1000_attempts_500_seats": c_fair_1000_attempts_500_seats,
    "D_five_tabs": d_five_tabs,
    "EF_idempotent_retry_and_token_replay": e_idempotent_retry_and_f_replay,
    "G_network_change": g_network_change,
    "H_repeated_entries": h_repeated_entries,
    "J_genuine_users_during_a_flood": j_genuine_users_during_a_flood,
}


async def main(names: list[str]) -> int:
    chosen = [n for n in SCENARIOS if not names or any(n.startswith(x) for x in names)]
    failures = 0
    db = await asyncpg.connect(os.environ["DATABASE_URL"])
    limits = httpx.Limits(max_connections=MAX_IN_FLIGHT, max_keepalive_connections=MAX_IN_FLIGHT)
    async with httpx.AsyncClient(base_url=BASE, limits=limits, timeout=httpx.Timeout(60.0)) as c:
        health = (await c.get("/api/readyz")).json()
        print(
            f"stack: {health}  workers={os.environ.get('UVICORN_WORKERS')} reload={os.environ.get('API_RELOAD')}"
        )
        for name in chosen:
            started = time.perf_counter()
            try:
                report = await SCENARIOS[name](c, db)
                print(f"\nPASS {name} ({time.perf_counter() - started:.1f}s)")
            except AssertionError as exc:
                failures += 1
                print(f"\nFAIL {name}: {exc}")
                continue
            for k, v in report.items():
                print(f"   {k}: {v if not isinstance(v, dict) else json.dumps(v)}")
        print(f"\n{len(chosen) - failures}/{len(chosen)} scenarios passed")
    await db.close()
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main(sys.argv[1:])))
