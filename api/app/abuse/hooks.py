"""The backend's integration boundary, backed by the real limiter (L1-L3), OTP guard (L6) and
risk scoring (L7). Active only with `ABUSE_ENFORCE=true` (see `enforcing()`).

The backend calls only these five functions (re-exported by `app.abuse`):

* `check(request) -> Decision`      from `AbuseLayersMiddleware`, before routing; no Postgres.
* `otp_request_guard(...)`          in POST /auth/otp/request; raises OtpThrottled.
* `on_identity_verified(...)`       after a successful OTP verify (fast-OTP signal, first-seen).
* `score_entry(...)`                before an entry is stored -> (risk_score, risk_flags).
* `rescore_eligible(conn, drop_id)` inside the draw transaction, before scores are read.

Per-app state (limiter, Redis, config) is built lazily from `app.state.settings` and
`app.state.cache` on the first limited request, so the backend needs no extra wiring. The hooks
that run without a request object (the draw is an admin call, which the limiter skips) use the
most recently bound app. With no app bound yet every hook is a no-op: allow, score 0.

Configuration is the contract document `{layers, thresholds}` that the backend's admin route
writes to Redis (`abuse:config`), re-read at most once per second; known keys apply, the rest is
ignored. With Redis down the last known config stays and the limiter uses per-worker buckets.

Nothing here reads simulator data or ground-truth labels (invariant 6).
"""

from __future__ import annotations

import contextvars
import json
import logging
import os
import time
import uuid
from dataclasses import dataclass, field
from typing import Any

import asyncpg
from redis.asyncio import BlockingConnectionPool, Redis
from redis.asyncio.retry import Retry
from redis.backoff import NoBackoff
from redis.exceptions import RedisError
from starlette.requests import Request

from app import netutil
from app.abuse import risk
from app.abuse.config import BACKEND_KEY, REFRESH_S, AbuseConfig
from app.abuse.limiter import (
    ALLOW,
    Decision,
    Limiter,
    RequestKey,
    endpoint_group,
    net24_of,
    session_id_from_token,
)
from app.abuse.middleware import session_token
from app.cache import Cache
from app.config import Settings
from app.errors import OtpThrottled

log = logging.getLogger("fairdrop.abuse")

SEEN_TTL_S = 30 * 24 * 3600  # first-seen time of a verified user ("new account" rules)
LIMITER_POOL = 100  # Redis connections per worker, for the limiter only
_KEEP = object()

RESCORE_SQL = """
SELECT e.id, e.user_id, e.device_id, host(e.client_ip) AS ip,
       extract(epoch FROM e.entered_at)::float8 AS t, e.risk_score, e.risk_flags::text AS flags
FROM entries e
WHERE e.drop_id = $1 AND e.status = 'REGISTERED'
"""

UPDATE_SQL = """
UPDATE entries e SET risk_score = v.score, risk_flags = v.flags::jsonb
FROM unnest($1::uuid[], $2::int[], $3::text[]) AS v(id, score, flags)
WHERE e.id = v.id AND (e.risk_score <> v.score OR e.risk_flags <> v.flags::jsonb)
"""


@dataclass
class Runtime:
    settings: Settings
    cache: Cache
    limiter: Limiter
    cfg: AbuseConfig = field(default_factory=AbuseConfig)
    checked: float = -REFRESH_S

    def redis(self) -> Redis | None:
        """The client, or None while the backend's circuit breaker is open."""
        return self.cache.client if self.cache.redis_available() else None

    async def config(self) -> AbuseConfig:
        if time.monotonic() - self.checked < REFRESH_S:
            return self.cfg
        self.checked = time.monotonic()

        async def read(r: Redis) -> Any:
            return await r.get(BACKEND_KEY)

        try:
            raw = await self.cache.run(read, fallback=lambda: _KEEP)
            if raw is not _KEEP:
                self.cfg = AbuseConfig().applied_contract(json.loads(raw)) if raw else AbuseConfig()
        except (RedisError, ValueError):
            pass  # keep the last known config
        return self.cfg


_current: contextvars.ContextVar[Runtime | None] = contextvars.ContextVar(
    "abuse_runtime", default=None
)
_last: Runtime | None = None


def enforcing() -> bool:
    """`ABUSE_ENFORCE=true` switches the layers on. Off by default until the backend's own
    load tests (one client IP for hundreds of users, instant sign-ins) are adapted; while off,
    every hook behaves like the original placeholders: allow everything, score 0."""
    return os.environ.get("ABUSE_ENFORCE", "false").strip().lower() in ("1", "true", "yes")


def _limiter_redis(settings: Settings) -> Redis:
    """The limiter's own Redis client, separate from the backend's `Cache` pool.

    The limiter runs on every request, including the flood it rejects. Sharing the backend's
    pool (non-blocking, 100 connections per worker) let a 10,000-client flood exhaust it; the
    backend's breaker then counted `MaxConnectionsError` as "Redis down" and sign-in answered
    503 to genuine users (real-backend run, seed 101: 163 of 200 never signed in). A blocking
    pool queues a burst for up to the Redis timeout instead of failing; a longer wait raises,
    and the limiter falls back to its per-worker buckets (stricter, never allow-all)."""
    t = settings.redis_timeout_ms / 1000
    pool = BlockingConnectionPool.from_url(
        settings.redis_url,
        max_connections=LIMITER_POOL,
        timeout=t,
        socket_timeout=t,
        socket_connect_timeout=t,
        retry=Retry(NoBackoff(), 0),
        decode_responses=True,
    )
    return Redis(connection_pool=pool)


def _runtime_for(app: Any) -> Runtime | None:
    global _last
    if not enforcing():
        return None
    state = app.state
    rt: Runtime | None = getattr(state, "abuse_runtime", None)
    if rt is None:
        settings, cache = getattr(state, "settings", None), getattr(state, "cache", None)
        if settings is None or cache is None:
            return None
        rt = Runtime(
            settings,
            cache,
            Limiter(
                _limiter_redis(settings),
                session_secret=settings.session_secret.get_secret_value().encode(),
                workers=settings.effective_workers,
            ),
        )
        state.abuse_runtime = rt
    _current.set(rt)
    _last = rt
    return rt


def _active() -> Runtime | None:
    if not enforcing():
        return None
    return _current.get() or _last


# ---------------------------------------------------------------- L1-L3


async def check(request: Request) -> Decision:
    try:
        app = request.app
    except (KeyError, AttributeError):
        return ALLOW
    rt = _runtime_for(app)
    if rt is None:
        return ALLOW
    group = endpoint_group(request.method, request.url.path)
    if group is None:
        return ALLOW
    cfg = await rt.config()
    ip = netutil.client_ip(request, rt.settings)
    tok = session_token(dict(request.headers.items()))
    sid = session_id_from_token(tok, rt.limiter.session_secret) if tok else None
    # Reads the backend's breaker but never feeds it: a limiter error already falls back to the
    # per-worker buckets, and must not take sign-in or entries down with it.
    return await rt.limiter.check(
        RequestKey(group, ip, net24_of(ip), sid, None), cfg, use_redis=rt.cache.redis_available()
    )


# ---------------------------------------------------------------- L6


async def otp_request_guard(
    *, phone_hash: str, phone_prefix: str, device_id: str, client_ip: str
) -> None:
    rt = _active()
    if rt is None:
        return
    blocked = await risk.otp_guard(
        rt.redis(),
        await rt.config(),
        phone_hash=phone_hash,
        device_id=device_id,
        ip=client_ip,
        prefix=phone_prefix,
    )
    if blocked is not None:
        raise OtpThrottled(retry_after_ms=blocked.retry_after_ms)


# ---------------------------------------------------------------- L7


async def on_identity_verified(
    *, user_id: str, device_id: str, client_ip: str, ua_hash: str, verify_latency_ms: int | None
) -> None:
    rt = _active()
    redis = rt.redis() if rt is not None else None
    if rt is None or redis is None:
        return
    try:
        await redis.set(f"abuse:seen:{user_id}", str(time.time()), ex=SEEN_TTL_S, nx=True)
    except (RedisError, OSError):
        return
    if verify_latency_ms is not None:
        await risk.record_verify(
            redis, await rt.config(), user_id=user_id, latency_ms=verify_latency_ms
        )


def _ua_key(drop_id: str) -> str:
    return f"cl:{drop_id}:uaof"


def _device(device_id: str | None, user_id: str) -> str:
    """A missing device id must not cluster everyone without one."""
    return device_id or f"unknown:{user_id}"


async def score_entry(
    *, drop_id: str, user_id: str, device_id: str | None, client_ip: str, ua_hash: str
) -> tuple[int, list[str]]:
    rt = _active()
    if rt is None:
        return 0, []
    cfg = await rt.config()
    redis = rt.redis()
    now = time.time()
    created = now - risk.NEW_USER_S - 1  # unknown first-seen: not a new account
    if redis is not None and cfg.on("L7"):
        try:
            p = redis.pipeline(transaction=False)
            p.get(f"abuse:seen:{user_id}")
            p.hset(_ua_key(drop_id), user_id, ua_hash)  # for the re-score before the draw
            p.expire(_ua_key(drop_id), risk.CLUSTER_TTL_S)
            seen = (await p.execute())[0]
            if seen:
                created = float(seen)
        except (RedisError, OSError, ValueError):
            redis = None
    ctx = risk.EntryContext(
        entry_id="",
        user_id=user_id,
        device_id=_device(device_id, user_id),
        ip=client_ip,
        user_agent=ua_hash,
        user_created_at=created,
        entered_at=now,
    )
    result = await risk.score_entry(redis, cfg, drop_id, ctx)
    return result.score, result.flags


async def rescore_eligible(conn: asyncpg.Connection, drop_id: str) -> None:
    """Re-score REGISTERED entries from the final cluster sets. Changes score and flags only
    (never eligibility or rank). Re-scoring only adds evidence: the stored score becomes the
    higher of the two and the flags their union (the final sets are supersets of what the entry
    saw, so in practice the re-score is never lower). If Redis is unavailable the entry-time
    scores stay."""
    rt = _active()
    redis = rt.redis() if rt is not None else None
    if rt is None or redis is None:
        return
    cfg = await rt.config()
    if not cfg.on("L7"):
        return
    rows = await conn.fetch(RESCORE_SQL, uuid.UUID(drop_id))
    if not rows:
        return
    try:
        uas = await redis.hmget(_ua_key(drop_id), [str(r["user_id"]) for r in rows])
    except (RedisError, OSError):
        return
    ctxs = [
        risk.EntryContext(
            entry_id=str(r["id"]),
            user_id=str(r["user_id"]),
            device_id=_device(r["device_id"], str(r["user_id"])),
            ip=r["ip"] or "",
            user_agent=str(ua or ""),
            user_created_at=0.0,  # not used by the re-score (cluster sets already exist)
            entered_at=r["t"],
        )
        for r, ua in zip(rows, uas, strict=True)
    ]
    results = await risk.rescore(redis, cfg, drop_id, ctxs)
    ids, scores, flags = [], [], []
    for r, c in zip(rows, ctxs, strict=True):
        res = results[c.entry_id]
        if "risk_unavailable" in res.flags:
            continue
        old = [f for f in json.loads(r["flags"] or "[]") if f != "risk_unavailable"]
        ids.append(uuid.UUID(c.entry_id))
        scores.append(max(int(r["risk_score"]), res.score))
        flags.append(json.dumps(old + [f for f in res.flags if f not in old]))
    if ids:
        await conn.execute(UPDATE_SQL, ids, scores, flags)
    log.info("rescored %d entries for drop %s", len(ids), drop_id)
