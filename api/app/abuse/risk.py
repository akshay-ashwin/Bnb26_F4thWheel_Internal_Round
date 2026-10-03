"""L6 OTP abuse controls and L7 identity-farm risk scoring.

L6 throttles OTP *requests* (it can return `429 OTP_THROTTLED`); it never touches entries.
L7 only produces evidence: a 0-100 score and short flags on an entry. It never blocks, never
excludes and never changes rank. A score >= `step_up_score` only means "re-verify with a fresh
OTP if you win" (L8).

Every L7 signal counts DIFFERENT users sharing something (a device, a /24, a provider network, a
user agent). One genuine user moving from Wi-Fi to mobile data adds themselves once to each
network set and so can never raise their own score. The largest single rule is 40 points and the
threshold is 60, so no single signal flags anyone.

Re-scoring at close: the first accounts of a farm enter before their device or subnet crosses a
threshold. `rescore()` recomputes every eligible entry from the final cluster sets before the
draw. Windowed rules then look at a window centred on the entry's own time (+/- window).
"""

from __future__ import annotations

import hashlib
import time
from dataclasses import dataclass, field
from typing import Any

from redis.asyncio import Redis
from redis.exceptions import RedisError

from app.abuse.config import AbuseConfig
from app.abuse.limiter import Decision, net24_of

NEW_USER_S = 24 * 3600
CLUSTER_TTL_S = 2 * 24 * 3600


def synthetic_asn(ip: str) -> str:
    """SIM_MODE stand-in for an ASN lookup: the first two IPv4 octets ("provider network").

    Real deployments would use an offline IP-to-ASN table. This is labelled synthetic wherever
    it is shown; it is only meaningful for simulated IPs.
    """
    parts = ip.split(".")
    return f"syn{parts[0]}.{parts[1]}" if len(parts) == 4 else "syn-unknown"


def short_hash(value: str, n: int = 12) -> str:
    return hashlib.sha256(value.encode()).hexdigest()[:n]


# ---------------------------------------------------------------- L6: OTP request guard


async def otp_guard(
    redis: Redis | None,
    cfg: AbuseConfig,
    *,
    phone_e164: str,
    phone_hash: str,
    device_id: str,
    ip: str,
) -> Decision | None:
    """Return a reject Decision (`OTP_THROTTLED`) or None. Redis errors fail open (counted)."""
    if redis is None or not cfg.on("L6"):
        return None
    th = cfg.thresholds
    now = time.time()
    net = net24_of(ip)
    prefix = phone_e164[:-3] if len(phone_e164) > 6 else phone_e164
    try:
        blocked = await redis.pttl(f"otp:pfxblock:{prefix}")
        if blocked > 0:
            return _otp_reject(blocked)
        p = redis.pipeline(transaction=False)
        checks: list[tuple[str, int, int, str]] = []
        for key, member, (limit, window) in (
            (f"otp:ph:{phone_hash}", f"{now}", th["otp_per_phone"]),
            (f"otp:dev:{device_id}", phone_hash, th["otp_per_device_phones"]),
            (f"otp:ip:{ip}", f"{now}:{phone_hash}", th["otp_per_ip"]),
            (f"otp:n24:{net}", f"{now}:{phone_hash}", th["otp_per_net24"]),
            (f"otp:pfx:{prefix}", phone_hash, th["otp_prefix_distinct"]),
        ):
            p.zremrangebyscore(key, 0, now - window)
            p.zadd(key, {member: now})
            p.zcard(key)
            p.expire(key, int(window) + 5)
            checks.append((key, int(limit), int(window), member))
        res = await p.execute()
    except (RedisError, OSError):
        return None
    worst = 0
    for i, (key, limit, window, _member) in enumerate(checks):
        count = int(res[i * 4 + 2])
        if count > limit:
            if key.startswith("otp:pfx:"):
                block_s = int(th["otp_prefix_block_s"])
                try:
                    await redis.set(f"otp:pfxblock:{prefix}", "1", ex=block_s)
                except (RedisError, OSError):
                    pass
                worst = max(worst, block_s * 1000)
            else:
                worst = max(worst, window * 1000 // max(1, limit))
    return _otp_reject(worst) if worst else None


def _otp_reject(ms: int) -> Decision:
    return Decision("reject", "L6", "OTP_THROTTLED", max(1000, int(ms)), scope="otp")


async def record_verify(
    redis: Redis | None, cfg: AbuseConfig, *, user_id: str, latency_ms: float
) -> None:
    """Store the fast-OTP signal (verified quicker than a person can read and type a code)."""
    if redis is None or latency_ms >= cfg.rules["R_FAST_OTP"]["ms"]:
        return
    try:
        await redis.set(f"fast_otp:{user_id}", "1", ex=NEW_USER_S)
    except (RedisError, OSError):
        return


# ---------------------------------------------------------------- L7: cluster risk scoring


@dataclass(frozen=True, slots=True)
class EntryContext:
    """Facts the backend already holds for an entry. No simulator data, ever."""

    entry_id: str
    user_id: str
    device_id: str
    ip: str
    user_agent: str
    user_created_at: float  # epoch seconds
    entered_at: float  # epoch seconds


@dataclass
class RiskResult:
    score: int = 0
    flags: list[str] = field(default_factory=list)
    evidence: dict[str, Any] = field(default_factory=dict)


FLAG_NAMES = {
    "R_DEVICE": "device_shared",
    "R_SUBNET": "subnet_burst",
    "R_FAST_OTP": "fast_otp",
    "R_UA": "ua_burst",
}


def _keys(drop_id: str, c: EntryContext) -> dict[str, str]:
    return {
        "device": f"cl:{drop_id}:device:{short_hash(c.device_id)}",
        "net24": f"cl:{drop_id}:ip24:{net24_of(c.ip)}",
        "asn": f"cl:{drop_id}:asn:{synthetic_asn(c.ip)}",
        "ua": f"cl:{drop_id}:ua:{short_hash(c.user_agent)}",
    }


def _is_new(c: EntryContext) -> bool:
    return c.entered_at - c.user_created_at <= NEW_USER_S


def _score(cfg: AbuseConfig, counts: dict[str, int], fast: bool) -> RiskResult:
    r = cfg.rules
    out = RiskResult(evidence=dict(counts))
    hits: list[tuple[str, int]] = []
    if r["R_DEVICE"]["enabled"] and counts["device"] > r["R_DEVICE"]["users"]:
        hits.append(("R_DEVICE", r["R_DEVICE"]["points"]))
    if r["R_SUBNET"]["enabled"] and (
        counts["net24"] > r["R_SUBNET"]["new_users"]
        or counts["asn"] > r["R_SUBNET"]["asn_new_users"]
    ):
        hits.append(("R_SUBNET", r["R_SUBNET"]["points"]))
    if r["R_FAST_OTP"]["enabled"] and fast:
        hits.append(("R_FAST_OTP", r["R_FAST_OTP"]["points"]))
    if r["R_UA"]["enabled"] and counts["ua"] > r["R_UA"]["users"]:
        hits.append(("R_UA", r["R_UA"]["points"]))
    out.score = min(100, sum(int(p) for _, p in hits))
    out.flags = [FLAG_NAMES[rule] for rule, _ in hits]
    return out


async def score_entry(
    redis: Redis | None, cfg: AbuseConfig, drop_id: str, c: EntryContext
) -> RiskResult:
    """Add the entry to its cluster sets and score it from current counts (one pipeline)."""
    if not cfg.on("L7"):
        return RiskResult()
    if redis is None:
        return RiskResult(flags=["risk_unavailable"])
    k = _keys(drop_id, c)
    t = c.entered_at
    sub_w = float(cfg.rules["R_SUBNET"]["window_s"])
    ua_w = float(cfg.rules["R_UA"]["window_s"])
    try:
        p = redis.pipeline(transaction=False)
        p.zadd(k["device"], {c.user_id: t})
        p.zadd(k["ua"], {c.user_id: t})
        if _is_new(c):
            p.zadd(k["net24"], {c.user_id: t})
            p.zadd(k["asn"], {c.user_id: t})
        for key in k.values():
            p.expire(key, CLUSTER_TTL_S)
        p.zcard(k["device"])
        p.zcount(k["net24"], t - sub_w, t)
        p.zcount(k["asn"], t - sub_w, t)
        p.zcount(k["ua"], t - ua_w, t)
        p.exists(f"fast_otp:{c.user_id}")
        res = await p.execute()
    except (RedisError, OSError):
        return RiskResult(flags=["risk_unavailable"])
    d, n24, asn, ua, fast = (int(x) for x in res[-5:])
    return _score(cfg, {"device": d, "net24": n24, "asn": asn, "ua": ua}, bool(fast))


async def rescore(
    redis: Redis | None, cfg: AbuseConfig, drop_id: str, entries: list[EntryContext]
) -> dict[str, RiskResult]:
    """Re-score all eligible entries from final cluster sets, before the draw.

    Changes only score and flags, never eligibility or rank.
    """
    if not cfg.on("L7"):
        return {e.entry_id: RiskResult() for e in entries}
    if redis is None:
        return {e.entry_id: RiskResult(flags=["risk_unavailable"]) for e in entries}
    sub_w = float(cfg.rules["R_SUBNET"]["window_s"])
    ua_w = float(cfg.rules["R_UA"]["window_s"])
    out: dict[str, RiskResult] = {}
    chunk = 2000
    for i in range(0, len(entries), chunk):
        part = entries[i : i + chunk]
        try:
            p = redis.pipeline(transaction=False)
            for c in part:
                k, t = _keys(drop_id, c), c.entered_at
                p.zcard(k["device"])
                p.zcount(k["net24"], t - sub_w, t + sub_w)
                p.zcount(k["asn"], t - sub_w, t + sub_w)
                p.zcount(k["ua"], t - ua_w, t + ua_w)
                p.exists(f"fast_otp:{c.user_id}")
            res = await p.execute()
        except (RedisError, OSError):
            for c in part:
                out[c.entry_id] = RiskResult(flags=["risk_unavailable"])
            continue
        for j, c in enumerate(part):
            d, n24, asn, ua, fast = (int(x) for x in res[j * 5 : j * 5 + 5])
            out[c.entry_id] = _score(
                cfg, {"device": d, "net24": n24, "asn": asn, "ua": ua}, bool(fast)
            )
    return out
