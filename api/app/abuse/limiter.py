"""L1–L3 rate limiting: one atomic Redis Lua call per request, no Postgres.

Request order inside the call (most specific first, so the reported layer is the client's own):
  L3 per session (and per user when the backend can resolve it without slow I/O)
  L2 per IP (anonymous and signed-in budgets differ) and per /24
  L1 per endpoint group, with separate pools for signed-in and anonymous traffic
Every bucket is checked before any is spent, so a rejected request costs no tokens anywhere.

If Redis fails, each worker falls back to its own in-memory buckets (same algorithm, limits
divided by the worker count, bounded least-recently-used map). It never blocks everyone and
never lets everything through.
"""

from __future__ import annotations

import hashlib
import hmac
import ipaddress
import os
import time
from base64 import urlsafe_b64encode
from collections import OrderedDict
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from redis.asyncio import Redis
from redis.exceptions import NoScriptError, RedisError

from app.abuse.config import AbuseConfig

LUA = (Path(__file__).with_name("buckets.lua")).read_text(encoding="utf-8")

EXEMPT_PREFIXES = ("/api/healthz", "/api/readyz", "/api/admin/", "/api/sim/")
SESSION_GROUPS = frozenset({"entries", "me", "claim", "step_up"})
LAYER_NAMES = {1: "L1", 2: "L2", 3: "L3"}


@dataclass(frozen=True, slots=True)
class Decision:
    """The contract's `check(request) -> Decision`. `scope` is internal (metrics, logs)."""

    outcome: Literal["allow", "reject"]
    layer: str | None = None
    code: str | None = None
    retry_after_ms: int | None = None
    scope: str | None = None


ALLOW = Decision("allow")


@dataclass(frozen=True, slots=True)
class RequestKey:
    """Everything the limiter needs, extracted without I/O."""

    group: str
    ip: str
    net24: str
    session_id: str | None
    user_id: str | None


@dataclass(frozen=True, slots=True)
class Bucket:
    key: str
    rate: float
    burst: float
    layer: int
    counts_violation: bool = False
    marks_slow: bool = False
    cost: float = 1.0


def endpoint_group(method: str, path: str) -> str | None:
    """Cheap prefix match; None = exempt. Paths follow docs/contract (base /api)."""
    if path.startswith(EXEMPT_PREFIXES):
        return None
    parts = path.split("/")
    if len(parts) >= 3 and parts[2] == "auth":
        return "otp"
    if len(parts) >= 4 and parts[2] == "drops":
        tail = parts[4] if len(parts) > 4 else ""
        if tail == "":
            return "read" if method == "GET" else "other"
        return {"entries": "entries", "me": "me", "claim": "claim", "step-up": "step_up"}.get(
            tail, "other"
        )
    return "other"


def net24_of(ip: str) -> str:
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return "invalid"
    prefix = 24 if addr.version == 4 else 64
    return str(ipaddress.ip_network(f"{ip}/{prefix}", strict=False).network_address)


def session_id_from_token(token: str, secret: bytes) -> str | None:
    """Verify `<session_uuid>.<base64url HMAC-SHA256(secret, session_uuid)>` (Plan 04 format).

    A pure HMAC check, no I/O. A forged or garbage token counts as anonymous, so it can never
    draw on the signed-in pool.
    """
    sid, sep, sig = token.partition(".")
    if not sep or not sid or len(token) > 512:
        return None
    want = urlsafe_b64encode(hmac.new(secret, sid.encode(), hashlib.sha256).digest())
    if hmac.compare_digest(want.rstrip(b"=").decode(), sig.rstrip("=")):
        return sid
    return None


def build_buckets(rk: RequestKey, cfg: AbuseConfig) -> list[Bucket]:
    lim = cfg.limits
    auth = rk.session_id is not None
    out: list[Bucket] = []
    if cfg.on("L3") and auth and rk.group in SESSION_GROUPS:
        if rk.group == "me":
            r, b = lim["L3"]["session_me"]
            out.append(Bucket(f"rl:L3:s:me:{rk.session_id}", r, b, 3, marks_slow=True))
        else:
            r, b = lim["L3"]["session"]
            out.append(Bucket(f"rl:L3:s:{rk.session_id}", r, b, 3))
        if rk.user_id is not None:
            r, b = lim["L3"]["user"]
            out.append(Bucket(f"rl:L3:u:{rk.user_id}", r, b, 3))
    if cfg.on("L2"):
        if auth:
            r, b = lim["L2"]["ip_auth"]
            out.append(Bucket(f"rl:L2:ipa:{rk.ip}", r, b, 2))
        else:
            r, b = lim["L2"]["ip_anon"]
            out.append(Bucket(f"rl:L2:ip:{rk.ip}", r, b, 2, counts_violation=True))
        r, b = lim["L2"]["net24"]
        out.append(Bucket(f"rl:L2:n:{rk.net24}", r, b, 2))
    if cfg.on("L1"):
        pools = lim["L1"].get(rk.group) or lim["L1"]["other"]
        pool = "auth" if auth and "auth" in pools else "anon"
        r, b = pools.get(pool) or pools["anon"]
        out.append(Bucket(f"rl:L1:{rk.group}:{pool}", r, b, 1))
    return out


class LocalBuckets:
    """Per-worker fallback when Redis is unreachable. Bounded: least recently used keys go."""

    def __init__(self, max_keys: int, workers: int) -> None:
        self.max_keys, self.workers = max_keys, max(1, workers)
        self.state: OrderedDict[str, tuple[float, float]] = OrderedDict()

    def check(self, buckets: list[Bucket]) -> tuple[bool, int, int]:
        now = time.monotonic() * 1000
        new: list[tuple[str, float]] = []
        deny_layer, deny_wait = 0, 0
        for bk in buckets:
            rate, burst = bk.rate / self.workers, max(1.0, bk.burst / self.workers)
            tk, ts = self.state.get(bk.key, (burst, now))
            tk = min(burst, tk + max(0.0, now - ts) * rate / 1000)
            if tk < bk.cost:
                deny_wait = max(deny_wait, int((bk.cost - tk) * 1000 / rate) + 1)
                deny_layer = deny_layer or bk.layer
            new.append((bk.key, tk))
        if deny_layer:
            return False, deny_layer, deny_wait
        for (key, tk), bk in zip(new, buckets, strict=True):
            self.state[key] = (tk - bk.cost, now)
            self.state.move_to_end(key)
        while len(self.state) > self.max_keys:
            self.state.popitem(last=False)
        return True, 0, 0


UserResolver = Callable[[str], Awaitable[str | None]]


class Limiter:
    def __init__(
        self,
        redis: Redis | None,
        *,
        session_secret: bytes,
        user_resolver: UserResolver | None = None,
        workers: int | None = None,
        fallback_max_keys: int = 100_000,
    ) -> None:
        self.redis = redis
        self.session_secret = session_secret
        self.user_resolver = user_resolver
        self.fallback = LocalBuckets(
            fallback_max_keys, workers or int(os.environ.get("UVICORN_WORKERS", "1"))
        )
        self._sha: str | None = None
        self.redis_failures = 0

    async def check(self, rk: RequestKey, cfg: AbuseConfig) -> Decision:
        buckets = build_buckets(rk, cfg)
        if not buckets:
            return ALLOW
        th = cfg.thresholds
        anon = rk.session_id is None
        if self.redis is not None:
            try:
                res = await self._eval(rk, buckets, th, check_cooldown=anon and cfg.on("L2"))
                ok, layer, wait = int(res[0]), int(res[1]), int(res[2])
            except (RedisError, OSError):
                self.redis_failures += 1
                ok, layer, wait = self.fallback.check(buckets)
        else:
            ok, layer, wait = self.fallback.check(buckets)
        if ok:
            return ALLOW
        return Decision(
            "reject", LAYER_NAMES.get(layer, "L1"), "RATE_LIMITED", max(1, wait), scope=rk.group
        )

    async def _eval(
        self, rk: RequestKey, buckets: list[Bucket], th: dict[str, Any], *, check_cooldown: bool
    ) -> list[Any]:
        assert self.redis is not None  # noqa: S101 - narrowed by caller
        keys = [f"cd:ip:{rk.ip}", f"viol:ip:{rk.ip}", f"slow:{rk.session_id or '-'}"]
        args: list[Any] = [
            len(buckets),
            1 if check_cooldown else 0,
            th["cooldown_violations"],
            th["cooldown_window_ms"],
            th["cooldown_ms"],
            th["slow_ttl_ms"],
        ]
        for bk in buckets:
            keys.append(bk.key)
            args += [
                bk.rate,
                bk.burst,
                bk.cost,
                int(bk.counts_violation),
                int(bk.marks_slow),
                bk.layer,
            ]
        if self._sha is None:
            self._sha = str(await self.redis.script_load(LUA))
        try:
            return list(await self.redis.evalsha(self._sha, len(keys), *keys, *args))
        except NoScriptError:
            self._sha = str(await self.redis.script_load(LUA))
            return list(await self.redis.evalsha(self._sha, len(keys), *keys, *args))
