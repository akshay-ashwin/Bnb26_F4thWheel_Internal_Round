"""DEV-ONLY reference backend for the frozen API contract (docs/contract), in memory.

Why it exists: the abuse layers and the simulator must be measured end to end, and the real
endpoints (Akshay's backend) are not built yet. This app implements the contract's shapes with
the real abuse middleware, L6 OTP guard and L7 scoring in front of a deliberately simple
in-memory store. It is NOT the allocation engine: no Postgres, one process only (state lives in
memory, so run it with one worker). `app.main` never imports it. When the real backend lands,
point the simulator at it with `--base-url` and nothing else changes.

Integrity in this stub comes from single-threaded critical sections: every check-then-write
runs without an `await` in between, so two requests can never both take the last seat.

Run:  uvicorn app.abuse.devstub.app:app --host 0.0.0.0 --port 8001   (SIM_MODE=true)
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import re
import secrets
import time
import uuid
from collections import Counter
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from fastapi import FastAPI, Header, Request
from fastapi.responses import JSONResponse, Response, StreamingResponse
from redis.asyncio import Redis
from redis.exceptions import RedisError

from app.abuse import risk
from app.abuse.config import ConfigStore
from app.abuse.limiter import Limiter, session_id_from_token
from app.abuse.middleware import AbuseMiddleware, client_ip, session_token, sim_mode
from app.abuse.router import config_router
from app.clock import server_time


def _env(name: str, default: str) -> str:
    return os.environ.get(name) or default


SESSION_SECRET = _env("SESSION_SECRET", "dev-session-secret").encode()
TOKEN_KEY = _env("TOKEN_SIGNING_KEY", "dev-token-key").encode()
PHONE_PEPPER = _env("PHONE_PEPPER", "dev-pepper").encode()
ADMIN_KEY = _env("ADMIN_KEY", "dev-admin")
SIM_KEY = _env("SIM_TELEMETRY_KEY", "dev-sim-key")
PHONE_RE = re.compile(r"^\+[1-9]\d{7,14}$")
OTP_TTL_S = 300
OTP_DEDUPE_S = 30
FIFO_TOKEN_S = 60

POLL_BASE_MS = {
    "SCHEDULED": 5000,
    "OPEN_none": 3000,
    "OPEN_reg": 4000,
    "CLOSED": 1500,
    "offered": 1000,
    "WAITLISTED": 2000,
    "terminal": 15000,
    "FIFO_OPEN": 1000,
}


def now_iso() -> str:
    return server_time()  # the contract format: milliseconds, Z suffix


def iso(ts: float | None) -> str | None:
    if ts is None:
        return None
    return datetime.fromtimestamp(ts, UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def err(
    status: int, code: str, message: str = "", retry_after_ms: int | None = None
) -> JSONResponse:
    body: dict[str, Any] = {
        "error": {"code": code, "message": message or code},
        "server_time": now_iso(),
    }
    headers: dict[str, str] = {}
    if retry_after_ms is not None:
        body["error"]["retry_after_ms"] = retry_after_ms
        headers["Retry-After"] = str(max(1, -(-retry_after_ms // 1000)))
    return JSONResponse(status_code=status, content=body, headers=headers)


def ok(body: dict[str, Any], status: int = 200) -> JSONResponse:
    return JSONResponse(status_code=status, content={**body, "server_time": now_iso()})


def b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def unb64(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


# ----------------------------------------------------------------------------- state


@dataclass
class OtpRequest:
    request_id: str
    phone_hash: str
    phone: str
    otp: str
    device_id: str
    requested_at: float
    attempts: int = 0


@dataclass
class User:
    id: str
    public_id: str
    phone_hash: str
    created_at: float


@dataclass
class Session:
    id: str
    user_id: str
    device_id: str
    ip: str
    ua: str


@dataclass
class Entry:
    entry_id: str
    user_id: str
    public_id: str
    entered_at: float
    ctx: risk.EntryContext
    status: str = "REGISTERED"
    risk_score: int = 0
    risk_flags: list[str] = field(default_factory=list)
    rank: int | None = None
    waitlist_pos: int | None = None
    offer_expires_at: float | None = None
    step_up_otp: str | None = None
    step_up_passed_at: float | None = None
    seat_no: int | None = None
    allocation_id: str | None = None
    confirmed_at: float | None = None


@dataclass
class Drop:
    id: str
    name: str
    capacity: int
    mode: str
    window_s: int
    claim_window_s: int
    seed: str
    phase: str = "SCHEDULED"
    run_no: int = 1
    reg_opens_at: float | None = None
    reg_closes_at: float | None = None
    sold: int = 0
    entry_set_hash: str | None = None
    entries: dict[str, Entry] = field(default_factory=dict)  # user_id -> entry
    seats: dict[int, str] = field(default_factory=dict)  # seat_no -> entry_id

    @property
    def seed_commit(self) -> str:
        return hashlib.sha256(self.seed.encode()).hexdigest()


class Store:
    def __init__(self) -> None:
        self.drops: dict[str, Drop] = {}
        self.otps: dict[str, OtpRequest] = {}
        self.otp_by_phone: dict[str, str] = {}
        self.users: dict[str, User] = {}
        self.users_by_phone: dict[str, User] = {}
        self.sessions: dict[str, Session] = {}
        self.session_by_user_device: dict[tuple[str, str], str] = {}
        self.idem: dict[tuple[str, str], tuple[str, int, dict[str, Any]]] = {}
        self.used_jti: set[str] = set()
        self.counters: Counter[str] = Counter()
        self.telemetry: dict[str, Any] = {}  # latest simulator telemetry (display only)


S = Store()
REDIS: Redis | None = None
CONFIG = ConfigStore(None)
OUTCOMES: Counter[str] = Counter()


async def _user_of_session(sid: str) -> str | None:
    s = S.sessions.get(sid)
    return s.user_id if s else None


LIMITER = Limiter(None, session_secret=SESSION_SECRET, user_resolver=_user_of_session, workers=1)


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    global REDIS
    url = os.environ.get("REDIS_URL")
    if url:
        REDIS = Redis.from_url(
            url, decode_responses=True, socket_timeout=float(_env("REDIS_TIMEOUT_MS", "200")) / 1000
        )
        LIMITER.redis = REDIS
        CONFIG.redis = REDIS
        await CONFIG.refresh()
    yield
    if REDIS is not None:
        await REDIS.aclose()


app = FastAPI(title="Fair Drop DEV STUB (not the real backend)", lifespan=lifespan)
app.add_middleware(AbuseMiddleware, limiter=LIMITER, config=CONFIG, outcomes=OUTCOMES)


def require_admin(x_admin_key: str | None = Header(default=None)) -> None:
    if not x_admin_key or not hmac.compare_digest(x_admin_key, ADMIN_KEY):
        from fastapi import HTTPException

        raise HTTPException(status_code=401, detail="UNAUTHENTICATED")


app.include_router(config_router(CONFIG, require_admin))


@app.exception_handler(Exception)
async def _unhandled(_request: Request, _exc: Exception) -> JSONResponse:
    return err(500, "INTERNAL", "internal error")


def _admin(key: str | None) -> JSONResponse | None:
    if not key or not hmac.compare_digest(key, ADMIN_KEY):
        return err(401, "UNAUTHENTICATED", "admin key required")
    return None


def _session(request: Request) -> Session | None:
    tok = session_token({k.lower(): v for k, v in request.headers.items()})
    sid = session_id_from_token(tok, SESSION_SECRET) if tok else None
    return S.sessions.get(sid) if sid else None


def _ip(request: Request) -> str:
    return client_ip(request.scope, {k.lower(): v for k, v in request.headers.items()})


def sid_hash(sid: str) -> str:
    return hashlib.sha256(sid.encode()).hexdigest()


def make_session_token(sid: str) -> str:
    return f"{sid}.{b64(hmac.new(SESSION_SECRET, sid.encode(), hashlib.sha256).digest())}"


# ----------------------------------------------------------------------------- tokens (L4)


def mint_token(drop: Drop, e: Entry, sess: Session, exp: float) -> str:
    claims = {
        "drop_id": drop.id,
        "entry_id": e.entry_id,
        "sid_hash": sid_hash(sess.id),
        "jti": uuid.uuid4().hex,
        "iat": int(time.time()),
        "exp": int(exp),
    }
    head = b64(json.dumps({"alg": "HS256", "typ": "JWT"}).encode())
    body = b64(json.dumps(claims, separators=(",", ":")).encode())
    sig = b64(hmac.new(TOKEN_KEY, f"{head}.{body}".encode(), hashlib.sha256).digest())
    return f"{head}.{body}.{sig}"


def verify_token(tok: str, drop: Drop, sess: Session, strict: bool) -> dict[str, Any] | None:
    """Signature and expiry always; session binding only when L4 is on. Single use (jti) is
    checked by the caller after the idempotency replay, so a retried claim still gets its 200."""
    try:
        head, body, sig = tok.split(".")
        want = b64(hmac.new(TOKEN_KEY, f"{head}.{body}".encode(), hashlib.sha256).digest())
        if not hmac.compare_digest(want, sig):
            return None
        claims: dict[str, Any] = json.loads(unb64(body))
    except (ValueError, json.JSONDecodeError):
        return None
    if claims.get("drop_id") != drop.id or time.time() > float(claims.get("exp", 0)):
        return None
    if strict and claims.get("sid_hash") != sid_hash(sess.id):
        return None
    return claims


# ----------------------------------------------------------------------------- public


@app.get("/api/healthz")
async def healthz() -> dict[str, str]:
    return {"status": "ok", "server_time": now_iso()}


def _drop_or_404(drop_id: str) -> Drop | JSONResponse:
    d = S.drops.get(drop_id)
    return d if d is not None else err(404, "NOT_FOUND", "no such drop")


@app.get("/api/drops/{drop_id}")
async def get_drop(drop_id: str) -> JSONResponse:
    d = _drop_or_404(drop_id)
    if isinstance(d, JSONResponse):
        return d
    out: dict[str, Any] = {
        "id": d.id,
        "name": d.name,
        "capacity": d.capacity,
        "mode": d.mode,
        "phase": d.phase,
        "reg_opens_at": iso(d.reg_opens_at),
        "reg_closes_at": iso(d.reg_closes_at),
        "claim_window_s": d.claim_window_s,
        "seats_remaining": d.capacity - d.sold,
        "seed_commit": d.seed_commit,
        # always present (contract): null until the draw reveals them
        "seed": d.seed if d.phase in ("DRAWN", "CLAIMING", "DONE") else None,
        "entry_set_hash": d.entry_set_hash,
    }
    return ok(out)


@app.post("/api/auth/otp/request")
async def otp_request(request: Request) -> JSONResponse:
    body = await _json(request)
    phone, device = str(body.get("phone", "")), str(body.get("device_id", ""))[:128]
    if not PHONE_RE.match(phone) or not device:
        return err(400, "INVALID_PHONE", "phone must be E.164 and device_id is required")
    ph = hmac.new(PHONE_PEPPER, phone.encode(), hashlib.sha256).hexdigest()
    now = time.time()
    prev = S.otps.get(S.otp_by_phone.get(ph, ""))
    if prev is not None and now - prev.requested_at < OTP_DEDUPE_S:
        return ok(
            {
                "request_id": prev.request_id,
                "expires_in_s": OTP_TTL_S,
                "dev_otp": prev.otp if sim_mode() else None,
            }
        )
    blocked = await risk.otp_guard(
        REDIS, CONFIG.current(), phone_e164=phone, phone_hash=ph, device_id=device, ip=_ip(request)
    )
    if blocked is not None:
        S.counters["otp_throttled"] += 1
        return err(429, "OTP_THROTTLED", "too many codes requested", blocked.retry_after_ms)
    rid = uuid.uuid4().hex
    otp = f"{secrets.randbelow(10**6):06d}"
    S.otps[rid] = OtpRequest(rid, ph, phone, otp, device, now)
    S.otp_by_phone[ph] = rid
    return ok(
        {"request_id": rid, "expires_in_s": OTP_TTL_S, "dev_otp": otp if sim_mode() else None}
    )


@app.post("/api/auth/otp/verify")
async def otp_verify(request: Request) -> JSONResponse:
    body = await _json(request)
    o = S.otps.get(str(body.get("request_id", "")))
    now = time.time()
    if o is None or now - o.requested_at > OTP_TTL_S or o.attempts >= 5:
        return err(410, "OTP_EXPIRED", "request a new code")
    if not hmac.compare_digest(str(body.get("otp", "")), o.otp):
        o.attempts += 1
        return err(401, "OTP_INVALID", "wrong code")
    S.otps.pop(o.request_id, None)
    S.otp_by_phone.pop(o.phone_hash, None)
    user = S.users_by_phone.get(o.phone_hash)
    if user is None:
        user = User(uuid.uuid4().hex, "u_" + secrets.token_urlsafe(8), o.phone_hash, now)
        S.users[user.id] = user
        S.users_by_phone[o.phone_hash] = user
    device = str(body.get("device_id", "")) or o.device_id
    sid = S.session_by_user_device.get((user.id, device))
    if sid is None or sid not in S.sessions:
        sid = str(uuid.uuid4())
        S.sessions[sid] = Session(
            sid, user.id, device, _ip(request), request.headers.get("user-agent", "")
        )
        S.session_by_user_device[(user.id, device)] = sid
    await risk.record_verify(
        REDIS, CONFIG.current(), user_id=user.id, latency_ms=(now - o.requested_at) * 1000
    )
    token = make_session_token(sid)
    resp = ok({"session_token": token, "user_public_id": user.public_id})
    resp.set_cookie("fd_session", token, httponly=True, samesite="lax")
    return resp


@app.post("/api/drops/{drop_id}/entries")
async def create_entry(drop_id: str, request: Request) -> JSONResponse:
    sess = _session(request)
    if sess is None:
        return err(401, "UNAUTHENTICATED", "sign in first")
    d = _drop_or_404(drop_id)
    if isinstance(d, JSONResponse):
        return d
    user = S.users[sess.user_id]
    existing = d.entries.get(user.id)
    if existing is not None:
        S.counters["duplicate"] += 1
        return ok({"entry_id": existing.entry_id, "status": existing.status})
    if d.phase == "SCHEDULED":
        return err(403, "WINDOW_NOT_OPEN", "registration has not opened")
    if d.phase != "OPEN":
        return err(403, "WINDOW_CLOSED", "registration is closed")
    now = time.time()
    ctx = risk.EntryContext(
        str(uuid.uuid4()),
        user.id,
        sess.device_id,
        _ip(request),
        request.headers.get("user-agent", ""),
        user.created_at,
        now,
    )
    e = Entry(ctx.entry_id, user.id, user.public_id, now, ctx)
    d.entries[user.id] = e  # inserted before any await: the per-user uniqueness point
    r = await risk.score_entry(REDIS, CONFIG.current(), d.id, ctx)
    e.risk_score, e.risk_flags = r.score, r.flags
    return ok({"entry_id": e.entry_id, "status": "REGISTERED"}, 201)


def _expire(d: Drop, e: Entry, now: float) -> None:
    if (
        e.status in ("OFFERED", "STEP_UP_REQUIRED")
        and e.offer_expires_at is not None
        and now > e.offer_expires_at
    ):
        e.status = "OFFER_EXPIRED"


async def _slowed(sid: str) -> bool:
    if REDIS is None:
        return False
    try:
        return bool(await REDIS.exists(f"slow:{sid}"))
    except (RedisError, OSError):
        return False


def _poll_ms(d: Drop, e: Entry | None) -> int:
    if d.mode == "fifo" and d.phase == "OPEN":
        return POLL_BASE_MS["FIFO_OPEN"]
    if e is None:
        return (
            POLL_BASE_MS["OPEN_none"]
            if d.phase == "OPEN"
            else POLL_BASE_MS.get(d.phase, POLL_BASE_MS["terminal"])
        )
    if e.status in ("OFFERED", "STEP_UP_REQUIRED"):
        return POLL_BASE_MS["offered"]
    if e.status == "WAITLISTED":
        return POLL_BASE_MS["WAITLISTED"]
    if e.status == "REGISTERED":
        return POLL_BASE_MS["OPEN_reg"] if d.phase == "OPEN" else POLL_BASE_MS["CLOSED"]
    return POLL_BASE_MS["terminal"]


@app.get("/api/drops/{drop_id}/me")
async def me(drop_id: str, request: Request) -> JSONResponse:
    sess = _session(request)
    if sess is None:
        return err(401, "UNAUTHENTICATED", "sign in first")
    d = _drop_or_404(drop_id)
    if isinstance(d, JSONResponse):
        return d
    now = time.time()
    e = d.entries.get(sess.user_id)
    entry: dict[str, Any] | None = None
    allocation: dict[str, Any] | None = None
    if e is not None:
        _expire(d, e, now)
        # Plan 03 contract: optional fields are always present, null when they do not apply.
        offered = e.status in ("OFFERED", "STEP_UP_REQUIRED")
        entry = {
            "entry_id": e.entry_id,
            "status": e.status,
            "rank": e.rank,
            "waitlist_pos": e.waitlist_pos,
            "offer_expires_at": iso(e.offer_expires_at) if offered else None,
            "step_up_required": e.status == "STEP_UP_REQUIRED",
            "admission_token": None,
            "dev_otp": None,
        }
        if d.mode == "fair" and e.status == "OFFERED" and e.offer_expires_at:
            entry["admission_token"] = mint_token(d, e, sess, e.offer_expires_at)
        elif d.mode == "fifo" and d.phase == "OPEN" and e.status == "REGISTERED":
            entry["admission_token"] = mint_token(d, e, sess, now + FIFO_TOKEN_S)
        if e.status == "STEP_UP_REQUIRED" and sim_mode():
            entry["dev_otp"] = e.step_up_otp
        if e.seat_no is not None:
            allocation = {
                "allocation_id": e.allocation_id,
                "seat_no": e.seat_no,
                "confirmed_at": iso(e.confirmed_at),
            }
    poll = _poll_ms(d, e) * (3 if await _slowed(sess.id) else 1)
    poll = int(min(30000, max(500, poll * (0.9 + 0.2 * secrets.randbelow(1001) / 1000))))
    return ok({"phase": d.phase, "entry": entry, "allocation": allocation, "poll_after_ms": poll})


@app.post("/api/drops/{drop_id}/claim")
async def claim(
    drop_id: str, request: Request, idempotency_key: str | None = Header(default=None)
) -> JSONResponse:
    sess = _session(request)
    if sess is None:
        return err(401, "UNAUTHENTICATED", "sign in first")
    d = _drop_or_404(drop_id)
    if isinstance(d, JSONResponse):
        return d
    if not idempotency_key:
        return err(400, "IDEMPOTENCY_KEY_MISSING", "Idempotency-Key header required")
    body = await _json(request)
    cfg = CONFIG.current()
    claims = verify_token(str(body.get("admission_token", "")), d, sess, strict=cfg.on("L4"))
    e = d.entries.get(sess.user_id)
    if claims is None or e is None or claims.get("entry_id") != e.entry_id:
        S.counters["token_rejected"] += 1
        return err(401, "TOKEN_INVALID", "admission token rejected")
    # ---- critical section: no await from here to the end of the handler
    ikey = (sess.id, idempotency_key)
    if cfg.on("L5") and ikey in S.idem:
        sem, status, cached = S.idem[ikey]
        if sem != e.entry_id:
            return err(422, "IDEMPOTENCY_KEY_REUSED", "key used for another request")
        S.counters["duplicate"] += 1
        return ok(cached, status)
    if e.seat_no is not None:  # the database-style guarantee: one seat per entry, always on
        S.counters["duplicate"] += 1
        return ok(_alloc_body(e))
    if cfg.on("L4") and claims.get("jti") in S.used_jti:
        S.counters["token_rejected"] += 1
        return err(401, "TOKEN_INVALID", "admission token already used")
    now = time.time()
    _expire(d, e, now)
    if d.mode == "fair":
        if e.status == "STEP_UP_REQUIRED":
            return err(423, "STEP_UP_REQUIRED", "re-verify first")
        if e.status == "OFFER_EXPIRED":
            return err(409, "OFFER_EXPIRED", "offer expired")
        if d.phase != "CLAIMING" or e.status != "OFFERED":
            return err(403, "NOT_OFFERED", "no offer for this entry")
    elif d.phase != "OPEN" or e.status != "REGISTERED":
        return (
            err(409, "SOLD_OUT", "no seats left")
            if d.phase == "DONE"
            else err(403, "NOT_OFFERED", "claims are not open")
        )
    if d.sold >= d.capacity:
        if d.mode == "fifo":
            _sold_out_fifo(d)
        S.counters["sold_out"] += 1
        return err(409, "SOLD_OUT", "no seats left")
    d.sold += 1
    e.seat_no, e.allocation_id, e.confirmed_at = d.sold, str(uuid.uuid4()), now
    e.status = "ALLOCATED"
    d.seats[e.seat_no] = e.entry_id
    S.used_jti.add(str(claims.get("jti")))
    if d.mode == "fifo" and d.sold >= d.capacity:
        _sold_out_fifo(d)
    out = _alloc_body(e)
    if cfg.on("L5"):
        S.idem[ikey] = (e.entry_id, 200, out)
    return ok(out)


def _sold_out_fifo(d: Drop) -> None:
    """Design section 13: when a FIFO drop sells out, REGISTERED entries become NOT_SELECTED."""
    d.phase = "DONE"
    for e in d.entries.values():
        if e.status == "REGISTERED":
            e.status = "NOT_SELECTED"


def _alloc_body(e: Entry) -> dict[str, Any]:
    return {
        "allocation_id": e.allocation_id,
        "seat_no": e.seat_no,
        "confirmed_at": iso(e.confirmed_at),
    }


@app.post("/api/drops/{drop_id}/step-up")
async def step_up(
    drop_id: str, request: Request, idempotency_key: str | None = Header(default=None)
) -> JSONResponse:
    sess = _session(request)
    if sess is None:
        return err(401, "UNAUTHENTICATED", "sign in first")
    d = _drop_or_404(drop_id)
    if isinstance(d, JSONResponse):
        return d
    if not idempotency_key:
        return err(400, "IDEMPOTENCY_KEY_MISSING", "Idempotency-Key header required")
    body = await _json(request)
    e = d.entries.get(sess.user_id)
    if e is None:
        return err(401, "OTP_INVALID", "no step-up pending")
    _expire(d, e, time.time())
    if e.step_up_passed_at is not None and e.status in ("OFFERED", "ALLOCATED"):
        return ok({"status": e.status})
    if e.status == "OFFER_EXPIRED":
        return err(409, "OFFER_EXPIRED", "offer expired")
    if (
        e.status != "STEP_UP_REQUIRED"
        or not e.step_up_otp
        or not hmac.compare_digest(str(body.get("otp", "")), e.step_up_otp)
    ):
        S.counters["step_up_failed"] += 1
        return err(401, "OTP_INVALID", "wrong code")
    e.status, e.step_up_passed_at = "OFFERED", time.time()
    S.counters["step_up_passed"] += 1
    return ok({"status": "OFFERED"})


async def _json(request: Request) -> dict[str, Any]:
    try:
        data = await request.json()
    except (ValueError, UnicodeDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


# ----------------------------------------------------------------------------- admin


@app.post("/api/admin/drops")
async def admin_create(
    request: Request, x_admin_key: str | None = Header(default=None)
) -> JSONResponse:
    if (bad := _admin(x_admin_key)) is not None:
        return bad
    b = await _json(request)
    try:
        cap = int(b.get("capacity", 500))
        mode = str(b.get("mode", "fair"))
        window_s = int(b.get("window_s", 60))
        claim_s = int(b.get("claim_window_s", 120))
    except (TypeError, ValueError):
        return err(400, "VALIDATION_ERROR", "bad field types")
    if cap < 1 or mode not in ("fair", "fifo") or window_s < 1 or claim_s < 1:
        return err(400, "VALIDATION_ERROR", "capacity >= 1, mode fair|fifo")
    d = Drop(
        str(uuid.uuid4()),
        str(b.get("name", "drop")),
        cap,
        mode,
        window_s,
        claim_s,
        secrets.token_hex(32),
    )
    S.drops[d.id] = d
    return ok({"drop_id": d.id, "seed_commit": d.seed_commit}, 201)


def _rank_key(d: Drop, public_id: str) -> str:
    return hmac.new(d.seed.encode(), f"{d.id}|{public_id}".encode(), hashlib.sha256).hexdigest()


@app.post("/api/admin/drops/{drop_id}/phase")
async def admin_phase(
    drop_id: str, request: Request, x_admin_key: str | None = Header(default=None)
) -> JSONResponse:
    if (bad := _admin(x_admin_key)) is not None:
        return bad
    d = _drop_or_404(drop_id)
    if isinstance(d, JSONResponse):
        return d
    action = str((await _json(request)).get("action", ""))
    now = time.time()
    cfg = CONFIG.current()
    if action == "open" and d.phase == "SCHEDULED":
        d.phase, d.reg_opens_at, d.reg_closes_at = "OPEN", now, now + d.window_s
    elif action == "close" and d.phase == "OPEN":
        d.phase, d.reg_closes_at = ("CLOSED" if d.mode == "fair" else "DONE"), now
        if d.mode == "fifo":
            _sold_out_fifo(d)
        if d.mode == "fair":
            ids = sorted(e.public_id for e in d.entries.values())
            d.entry_set_hash = hashlib.sha256("\n".join(ids).encode()).hexdigest()
            scores = await risk.rescore(REDIS, cfg, d.id, [e.ctx for e in d.entries.values()])
            for e in d.entries.values():
                r = scores.get(e.entry_id)
                if r is not None:
                    e.risk_score, e.risk_flags = r.score, r.flags
    elif action == "draw" and d.phase == "CLOSED":
        draw(d, cfg.on("L7") and cfg.on("L8"), int(cfg.thresholds["step_up_score"]), now)
    elif action == "reset":
        d.seed, d.phase, d.run_no = secrets.token_hex(32), "SCHEDULED", d.run_no + 1
        d.sold, d.entries, d.seats, d.entry_set_hash = 0, {}, {}, None
        d.reg_opens_at = d.reg_closes_at = None
    else:
        return err(409, "INVALID_TRANSITION", f"cannot {action} from {d.phase}")
    return ok({"phase": d.phase})


def draw(d: Drop, step_up_on: bool, threshold: int, now: float) -> None:
    """Rank = HMAC(seed, drop_id|user_public_id) ascending. Risk never enters the ranking."""
    ranked = sorted(d.entries.values(), key=lambda e: (_rank_key(d, e.public_id), e.public_id))
    for i, e in enumerate(ranked, 1):
        e.rank = i
        if i <= d.capacity:
            e.offer_expires_at = now + d.claim_window_s
            if step_up_on and e.risk_score >= threshold:
                e.status, e.step_up_otp = "STEP_UP_REQUIRED", f"{secrets.randbelow(10**6):06d}"
            else:
                e.status = "OFFERED"
        else:
            e.status, e.waitlist_pos = "WAITLISTED", i - d.capacity
    d.phase = "CLAIMING"


@app.get("/api/admin/drops/{drop_id}/integrity")
async def admin_integrity(
    drop_id: str, x_admin_key: str | None = Header(default=None)
) -> JSONResponse:
    if (bad := _admin(x_admin_key)) is not None:
        return bad
    d = _drop_or_404(drop_id)
    if isinstance(d, JSONResponse):
        return d
    seated = [e for e in d.entries.values() if e.seat_no is not None]
    seat_nos = [e.seat_no for e in seated]
    oversold = max(0, len(seated) - d.capacity)
    dup = len(seat_nos) - len(set(seat_nos))
    allocated = sum(1 for e in d.entries.values() if e.status == "ALLOCATED")
    return ok(
        {
            "seats_total": d.capacity,
            "sold": len(seated),
            "free": d.capacity - len(seated),
            "oversold": oversold,
            "duplicate_entries_with_seats": dup,
            "invariant_ok": oversold == 0 and dup == 0 and len(seated) == d.sold,
            # docs/contract/additions.md: all 0 except the two counts, which equal `sold`
            "extra": {
                "capacity": d.capacity,
                "allocations_count": len(seated),
                "sold_without_allocation": 0,
                "allocation_without_sold_seat": 0,
                "entries_allocated_count": allocated,
                "entries_allocated_mismatch": abs(allocated - len(seated)),
                "sold_seat_entry_not_allocated": 0,
                "free_seat_with_sold_at": 0,
            },
        }
    )


@app.get("/api/admin/drops/{drop_id}/export")
async def admin_export(drop_id: str, x_admin_key: str | None = Header(default=None)) -> Response:
    if (bad := _admin(x_admin_key)) is not None:
        return bad
    d = _drop_or_404(drop_id)
    if isinstance(d, JSONResponse):
        return d
    rows = sorted(d.entries.values(), key=lambda e: e.entered_at)

    async def gen() -> AsyncIterator[bytes]:
        for e in rows:
            row: dict[str, Any] = {
                "user_public_id": e.public_id,
                "entry_id": e.entry_id,
                "entered_at": iso(e.entered_at),
                "risk_score": e.risk_score,
                "risk_flags": e.risk_flags,
                "rank": e.rank,
                "status": e.status,
            }
            if e.seat_no is not None:
                row["seat_no"] = e.seat_no
            yield (json.dumps(row) + "\n").encode()

    return StreamingResponse(
        gen(), media_type="application/x-ndjson", headers={"X-Server-Time": now_iso()}
    )


@app.get("/api/admin/drops/{drop_id}/metrics")
async def admin_metrics(
    drop_id: str, x_admin_key: str | None = Header(default=None)
) -> JSONResponse:
    if (bad := _admin(x_admin_key)) is not None:
        return bad
    d = _drop_or_404(drop_id)
    if isinstance(d, JSONResponse):
        return d
    st = Counter(e.status for e in d.entries.values())
    threshold = int(CONFIG.current().thresholds["step_up_score"])
    return ok(
        {
            "rps_series": [],
            "outcomes_series": {},
            # the stub does not measure server-side latency: zeros here, and the note says so;
            # use the simulator's client-observed latency instead
            "latency": {"p50": 0.0, "p95": 0.0, "p99": 0.0},
            "latency_note": "dev stub: not measured server-side; see simulator client latency",
            "error_rate": 0.0,
            "active_sessions": len(S.sessions),
            "entries": len(d.entries),
            "offers": st["OFFERED"] + st["STEP_UP_REQUIRED"],
            "allocated": st["ALLOCATED"],
            "remaining": d.capacity - d.sold,
            "flagged_entries": sum(1 for e in d.entries.values() if e.risk_score >= threshold),
            "step_ups": {
                "issued": sum(1 for e in d.entries.values() if e.step_up_otp),
                "passed": S.counters["step_up_passed"],
                "failed": S.counters["step_up_failed"],
            },
            "outcome_totals": {**OUTCOMES, **S.counters},
            # docs/contract/additions.md (all in-process counters for the stub's lifetime)
            "phase": d.phase,
            "mode": d.mode,
            "run_no": d.run_no,
            "capacity": d.capacity,
            "oversold": max(0, d.sold - d.capacity),
            "invariant_ok": d.sold <= d.capacity,
            "claims_ok": st["ALLOCATED"],
            "claims_sold_out": S.counters["sold_out"],
            "blocked_requests": OUTCOMES["rate_limited"],
            "throttled_requests": S.counters["otp_throttled"],
            "duplicate_requests": S.counters["duplicate"],
            "rate_limited_by_layer": {
                k.split(":", 1)[1]: v for k, v in OUTCOMES.items() if k.startswith("rate_limited:")
            },
            "window_s": 0,
            "metrics_dropped": 0,
        }
    )


@app.get("/api/admin/drops/{drop_id}/draw-proof")
async def admin_draw_proof(
    drop_id: str, x_admin_key: str | None = Header(default=None)
) -> JSONResponse:
    if (bad := _admin(x_admin_key)) is not None:
        return bad
    d = _drop_or_404(drop_id)
    if isinstance(d, JSONResponse):
        return d
    if d.phase not in ("CLAIMING", "DONE", "DRAWN"):
        return err(409, "INVALID_TRANSITION", "draw has not happened")
    drawn = [e for e in d.entries.values() if e.rank is not None]
    ranked = sorted(drawn, key=lambda e: e.rank or 0)
    return ok(
        {
            "seed_commit": d.seed_commit,
            "seed": d.seed,
            "entry_set_hash": d.entry_set_hash,
            "algorithm": "HMAC_SHA256(seed, drop_id‖user_public_id) asc",
            "drop_id": d.id,
            "run_no": d.run_no,
            "eligible_public_ids": sorted(e.public_id for e in ranked),
            "ranked_public_ids": [e.public_id for e in ranked],
        }
    )


# ----------------------------------------------------------------------------- simulator


@app.post("/api/sim/telemetry")
async def sim_telemetry(
    request: Request, x_sim_key: str | None = Header(default=None)
) -> JSONResponse:
    """Presentation only: kept in memory, never read by any decision code above. (The real
    backend stores it in Redis from its admin service, the one module allowed to.)"""
    if not sim_mode():
        return err(404, "NOT_FOUND", "simulator telemetry is off")
    if not x_sim_key or not hmac.compare_digest(x_sim_key, SIM_KEY):
        return err(401, "UNAUTHENTICATED", "bad simulator key")
    S.telemetry = await _json(request)
    return ok({})
