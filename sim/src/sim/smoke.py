"""Small end-to-end smoke test of a running backend through the public contract endpoints.

  sim smoke --base-url http://api:8000 --out out/smoke_real

Meant for the REAL backend with `ABUSE_ENFORCE=true`: a handful of users walk the whole journey
(OTP request, OTP retry, wrong code, verify, entry, repeated entry, refresh, second tab, a 429
with Retry-After and a successful retry, draw, claim, duplicate and concurrent claims, token
replay and forgery) and every check is recorded as pass/fail in `smoke.json`. Users share one
NAT IP on purpose and one switches network mid-journey. No label of any kind goes on the wire.
"""

from __future__ import annotations

import asyncio
import json
import random
import uuid
from pathlib import Path
from typing import Any

import aiohttp

from sim.api import Result, make_session
from sim.clients.bot import forge
from sim.runner import admin

NAT_IP = "100.70.1.20"  # carrier-grade NAT: several real people share it
SWITCH_IP = "100.99.7.31"
UA = "Mozilla/5.0 (Linux; Android 14; Pixel 8) AppleWebKit/537.36 Chrome/127.0 Mobile Safari"


class User:
    def __init__(self, n: int, ip: str = NAT_IP) -> None:
        self.phone = f"+91{random.randrange(7_000_000_000, 9_999_999_999)}"  # noqa: S311
        self.device = f"dev-smoke-{uuid.uuid4().hex[:12]}"
        self.ip, self.token, self.public_id = ip, "", ""


class Smoke:
    def __init__(self, http: aiohttp.ClientSession, key: str) -> None:
        self.http, self.key = http, key
        self.checks: list[dict[str, Any]] = []
        self.statuses: dict[int, int] = {}

    def check(self, name: str, ok: bool, detail: Any = None) -> bool:
        self.checks.append({"check": name, "pass": bool(ok), "detail": detail})
        print(f"[{'PASS' if ok else 'FAIL'}] {name}" + (f"  {detail}" if detail else ""))
        return ok

    async def call(
        self,
        u: User | None,
        method: str,
        path: str,
        body: dict[str, Any] | None = None,
        *,
        token: str | None = None,
        idem: str | None = None,
        ip: str | None = None,
    ) -> Result:
        headers = {"User-Agent": UA, "X-Sim-Client-IP": ip or (u.ip if u else NAT_IP)}
        tok = token if token is not None else (u.token if u else "")
        if tok:
            headers["Authorization"] = f"Bearer {tok}"
        if idem:
            headers["Idempotency-Key"] = idem
        async with self.http.request(method, path, json=body, headers=headers) as r:
            try:
                data = await r.json(content_type=None)
            except (ValueError, aiohttp.ContentTypeError):
                data = {}
            res = Result(r.status, data if isinstance(data, dict) else {}, r.headers)
        self.statuses[res.status] = self.statuses.get(res.status, 0) + 1
        return res

    async def login(self, u: User) -> bool:
        r = await self.call(
            u, "POST", "/api/auth/otp/request", {"phone": u.phone, "device_id": u.device}
        )
        if not r.ok:
            return self.check("otp request", False, (r.status, r.code))
        v = await self.call(
            u,
            "POST",
            "/api/auth/otp/verify",
            {"request_id": r.body["request_id"], "otp": r.body["dev_otp"], "device_id": u.device},
        )
        u.token, u.public_id = v.body.get("session_token", ""), v.body.get("user_public_id", "")
        return v.ok


async def smoke(base_url: str, admin_key: str) -> dict[str, Any]:
    http = make_session(base_url, 50, 30)
    async with http:
        s = Smoke(http, admin_key)
        await _journey(s)
        await _fifo(s)
        await _farm(s)
    s.check("no 5xx anywhere", not any(k >= 500 for k in s.statuses), s.statuses)
    report = {
        "base_url": base_url,
        "checks": s.checks,
        "statuses": s.statuses,
        "passed": sum(c["pass"] for c in s.checks),
        "failed": sum(not c["pass"] for c in s.checks),
        "pass": all(c["pass"] for c in s.checks),
    }
    print(f"[smoke] {report['passed']} passed, {report['failed']} failed")
    return report


def write_report(report: dict[str, Any], out: Path) -> None:
    out.mkdir(parents=True, exist_ok=True)
    (out / "smoke.json").write_text(json.dumps(report, indent=2), encoding="utf-8")


async def _journey(s: Smoke) -> None:
    """Fair mode, 6 users (5 on one NAT IP, one switches network), 2 seats."""
    d = await admin(
        s.http,
        "POST",
        "/api/admin/drops",
        s.key,
        {
            "name": "smoke-fair",
            "capacity": 2,
            "mode": "fair",
            "window_s": 600,
            "claim_window_s": 120,
        },
    )
    drop = d["drop_id"]
    base = f"/api/drops/{drop}"
    users = [User(i) for i in range(5)] + [User(5, ip="49.36.12.7")]
    a = users[0]

    # --- sign-in
    r1 = await s.call(a, "POST", "/api/auth/otp/request", {"phone": a.phone, "device_id": a.device})
    s.check(
        "otp request -> 200 with request_id", r1.ok and bool(r1.body.get("request_id")), r1.status
    )
    r2 = await s.call(a, "POST", "/api/auth/otp/request", {"phone": a.phone, "device_id": a.device})
    s.check(
        "otp retry within 30 s -> same request_id",
        r2.ok and r2.body.get("request_id") == r1.body.get("request_id"),
        r2.status,
    )
    bad = await s.call(
        a,
        "POST",
        "/api/auth/otp/verify",
        {
            "request_id": r1.body["request_id"],
            "otp": "000000" if r1.body["dev_otp"] != "000000" else "111111",
            "device_id": a.device,
        },
    )
    s.check("wrong OTP -> OTP_INVALID", bad.code == "OTP_INVALID", (bad.status, bad.code))
    await asyncio.sleep(2.1)  # a person reading the SMS (also avoids the fast-OTP signal)
    v = await s.call(
        a,
        "POST",
        "/api/auth/otp/verify",
        {"request_id": r1.body["request_id"], "otp": r1.body["dev_otp"], "device_id": a.device},
    )
    a.token, a.public_id = v.body.get("session_token", ""), v.body.get("user_public_id", "")
    s.check("verify -> session token", v.ok and bool(a.token), v.status)
    reuse = await s.call(
        a,
        "POST",
        "/api/auth/otp/verify",
        {"request_id": r1.body["request_id"], "otp": r1.body["dev_otp"], "device_id": a.device},
    )
    s.check("OTP is single use", reuse.code in ("OTP_EXPIRED", "OTP_INVALID"), reuse.code)
    ok = [await s.login(u) for u in users[1:]]
    s.check("5 more users sign in (4 share one NAT IP)", all(ok), ok)

    # --- entry
    early = await s.call(a, "POST", f"{base}/entries", {})
    s.check("entry before open -> WINDOW_NOT_OPEN", early.code == "WINDOW_NOT_OPEN", early.code)
    await admin(s.http, "POST", f"/api/admin/drops/{drop}/phase", s.key, {"action": "open"})
    e1 = await s.call(a, "POST", f"{base}/entries", {})
    e2 = await s.call(a, "POST", f"{base}/entries", {})
    s.check("entry -> 201", e1.status == 201, e1.status)
    s.check(
        "repeated entry -> 200, same entry",
        e2.status == 200 and e2.body.get("entry_id") == e1.body.get("entry_id"),
        e2.status,
    )
    anon = await s.call(None, "POST", f"{base}/entries", {}, token="")
    s.check("entry without session -> 401", anon.code == "UNAUTHENTICATED", anon.code)
    fake = await s.call(None, "POST", f"{base}/entries", {}, token=f"{uuid.uuid4()}.AAAA")
    s.check("forged session token -> 401", fake.code == "UNAUTHENTICATED", fake.code)

    # --- 429: one impatient user hammering entry (L3 per-session bucket), then a polite retry
    b = users[1]
    burst = await asyncio.gather(*(s.call(b, "POST", f"{base}/entries", {}) for _ in range(20)))
    limited = [x for x in burst if x.status == 429]
    s.check("burst of 20 entry clicks -> some 429", bool(limited), [x.status for x in burst])
    s.check(
        "every 429 has Retry-After and retry_after_ms",
        all(
            x.headers.get("Retry-After") and x.body["error"].get("retry_after_ms") for x in limited
        ),
    )
    entered_ids = {x.body.get("entry_id") for x in burst if x.ok}
    s.check("burst created exactly one entry", len(entered_ids) == 1, len(entered_ids))
    wait = max(x.retry_after_s() for x in limited) if limited else 1.0
    await asyncio.sleep(wait)
    retry = await s.call(b, "POST", f"{base}/entries", {})
    s.check("retry after Retry-After -> 200", retry.status == 200, (round(wait, 2), retry.status))

    # --- the rest enter; one switches Wi-Fi -> mobile data first
    c = users[2]
    c.ip = SWITCH_IP
    for u in users[2:]:
        r = await s.call(u, "POST", f"{base}/entries", {})
        s.check(f"user {users.index(u)} entry", r.status in (200, 201), r.status)

    # --- refresh and second tab
    m1 = await s.call(a, "GET", f"{base}/me")
    m2, m3 = await asyncio.gather(s.call(a, "GET", f"{base}/me"), s.call(a, "GET", f"{base}/me"))
    s.check(
        "refresh + second tab -> same REGISTERED entry",
        all(m.ok for m in (m1, m2, m3))
        and len({m.body["entry"]["entry_id"] for m in (m1, m2, m3)}) == 1
        and m1.body["entry"]["status"] == "REGISTERED",
        [m.status for m in (m1, m2, m3)],
    )
    s.check("/me has poll_after_ms", isinstance(m1.body.get("poll_after_ms"), int))

    # --- draw
    await admin(s.http, "POST", f"/api/admin/drops/{drop}/phase", s.key, {"action": "close"})
    late = await s.call(users[3], "POST", f"{base}/entries", {})
    s.check(
        "entry after close does not create a new entry",
        late.status in (200, 403),
        (late.status, late.code),
    )
    ph = await admin(s.http, "POST", f"/api/admin/drops/{drop}/phase", s.key, {"action": "draw"})
    s.check("draw -> CLAIMING", ph.get("phase") == "CLAIMING", ph.get("phase"))
    proof = await s.call(None, "GET", f"{base}/draw-proof", token="")
    s.check("public draw proof available", proof.ok and bool(proof.body.get("seed")), proof.status)
    export = [
        json.loads(x)
        for x in (await admin(s.http, "GET", f"/api/admin/drops/{drop}/export", s.key)).splitlines()
        if x.strip()
    ]
    s.check(
        "6 entries, one per user",
        len(export) == 6 and len({e["user_public_id"] for e in export}) == 6,
        len(export),
    )
    s.check("every entry ranked by the draw", all(e.get("rank") for e in export))
    s.check(
        "re-score ran: no genuine user flagged (shared NAT, network switch)",
        all((e.get("risk_score") or 0) < 60 for e in export),
        [e.get("risk_score") for e in export],
    )

    # --- claims
    mes = {id(u): await s.call(u, "GET", f"{base}/me") for u in users}
    winners = [u for u in users if (mes[id(u)].body.get("entry") or {}).get("status") == "OFFERED"]
    losers = [u for u in users if u not in winners]
    s.check(
        "2 offers for 2 seats",
        len(winners) == 2,
        [(mes[id(u)].body.get("entry") or {}).get("status") for u in users],
    )
    if not winners:
        return
    w, w2 = winners[0], winners[-1]
    tok = mes[id(w)].body["entry"]["admission_token"]
    nokey = await s.call(w, "POST", f"{base}/claim", {"admission_token": tok})
    s.check(
        "claim without Idempotency-Key -> IDEMPOTENCY_KEY_MISSING",
        nokey.code == "IDEMPOTENCY_KEY_MISSING",
        nokey.code,
    )
    thief = losers[0]
    stolen = await s.call(
        thief, "POST", f"{base}/claim", {"admission_token": tok}, idem=str(uuid.uuid4())
    )
    s.check(
        "winner's token replayed from another session -> rejected",
        not stolen.ok,
        (stolen.status, stolen.code),
    )
    forged = await s.call(
        w, "POST", f"{base}/claim", {"admission_token": forge(tok)}, idem=str(uuid.uuid4())
    )
    s.check("forged token -> TOKEN_INVALID", forged.code == "TOKEN_INVALID", forged.code)
    key = str(uuid.uuid4())
    c1 = await s.call(w, "POST", f"{base}/claim", {"admission_token": tok}, idem=key)
    s.check("claim -> 200 seat", c1.ok and c1.body.get("seat_no") is not None, c1.status)
    c2 = await s.call(w, "POST", f"{base}/claim", {"admission_token": tok}, idem=key)
    s.check(
        "idempotent retry (same key) -> same allocation",
        c2.ok and c2.body.get("allocation_id") == c1.body.get("allocation_id"),
        c2.status,
    )
    c3 = await s.call(w, "POST", f"{base}/claim", {"admission_token": tok}, idem=str(uuid.uuid4()))
    s.check(
        "duplicate claim (new key) -> same seat, no second seat",
        c3.ok and c3.body.get("seat_no") == c1.body.get("seat_no"),
        (c3.status, c3.code),
    )
    tok2 = mes[id(w2)].body["entry"]["admission_token"]
    many = await asyncio.gather(
        *(
            s.call(w2, "POST", f"{base}/claim", {"admission_token": tok2}, idem=str(uuid.uuid4()))
            for _ in range(6)
        )
    )
    seats = {x.body.get("seat_no") for x in many if x.ok}
    s.check(
        "6 concurrent claims (two tabs) -> one seat",
        len(seats) == 1 and all(x.ok for x in many),
        [x.status for x in many],
    )
    if losers:
        lme = mes[id(losers[0])].body.get("entry") or {}
        s.check("loser has no admission token", not lme.get("admission_token"), lme.get("status"))
    integ = await admin(s.http, "GET", f"/api/admin/drops/{drop}/integrity", s.key)
    s.check(
        "integrity: sold 2, oversold 0, no duplicate seats",
        integ["sold"] == 2
        and integ["oversold"] == 0
        and integ["duplicate_entries_with_seats"] == 0
        and integ["invariant_ok"],
        {k: integ[k] for k in ("sold", "oversold", "duplicate_entries_with_seats", "invariant_ok")},
    )


async def _fifo(s: Smoke) -> None:
    """FIFO baseline: 1 seat, 3 users race the claim; exactly one seat is sold."""
    d = await admin(
        s.http,
        "POST",
        "/api/admin/drops",
        s.key,
        {
            "name": "smoke-fifo",
            "capacity": 1,
            "mode": "fifo",
            "window_s": 600,
            "claim_window_s": 120,
        },
    )
    drop = d["drop_id"]
    base = f"/api/drops/{drop}"
    users = [User(i + 10, ip=f"49.36.13.{i + 2}") for i in range(3)]
    for u in users:
        await s.login(u)
    await admin(s.http, "POST", f"/api/admin/drops/{drop}/phase", s.key, {"action": "open"})
    for u in users:
        await s.call(u, "POST", f"{base}/entries", {})
    toks = [
        ((await s.call(u, "GET", f"{base}/me")).body.get("entry") or {}).get("admission_token")
        for u in users
    ]
    res = await asyncio.gather(
        *(
            s.call(u, "POST", f"{base}/claim", {"admission_token": t}, idem=str(uuid.uuid4()))
            for u, t in zip(users, toks, strict=True)
            if t
        )
    )
    s.check(
        "FIFO: 3 racing claims for 1 seat -> 1 x 200, rest SOLD_OUT",
        sum(x.ok for x in res) == 1 and all(x.ok or x.code == "SOLD_OUT" for x in res),
        [(x.status, x.code) for x in res],
    )
    integ = await admin(s.http, "GET", f"/api/admin/drops/{drop}/integrity", s.key)
    s.check("FIFO integrity: sold 1, oversold 0", integ["sold"] == 1 and integ["oversold"] == 0)


async def _farm(s: Smoke) -> None:
    """Identity farming from one device: L6 refuses OTPs past 3 phones per device."""
    dev = f"dev-farm-{uuid.uuid4().hex[:12]}"
    codes = []
    for i in range(5):
        u = User(i + 20, ip="45.10.20.30")
        u.device = dev
        r = await s.call(u, "POST", "/api/auth/otp/request", {"phone": u.phone, "device_id": dev})
        codes.append(r.code or r.status)
    s.check(
        "farm: 5 phones on one device -> OTP_THROTTLED after 3",
        codes[:3] == [200] * 3 and codes[3:] == ["OTP_THROTTLED"] * 2,
        codes,
    )
