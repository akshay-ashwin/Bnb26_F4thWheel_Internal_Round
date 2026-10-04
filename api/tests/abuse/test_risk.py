"""L6 OTP guard and L7 scoring against a real Redis."""

from __future__ import annotations

import random
import time

from redis.asyncio import Redis

from app.abuse.config import AbuseConfig
from app.abuse.risk import EntryContext, otp_guard, record_verify, rescore, score_entry

DROP = "drop-risk"
CHROME = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/128.0 Safari/537.36"
SAFARI = "Mozilla/5.0 (iPhone; CPU iPhone OS 17_5 like Mac OS X) Mobile/15E148 Safari/604.1"
THRESHOLD = 60


def ctx(
    i: int, *, device: str, ip: str, ua: str = CHROME, t: float | None = None, new: bool = True
) -> EntryContext:
    now = t if t is not None else time.time()
    created = now - (60 if new else 7 * 86400)
    return EntryContext(f"e{i}", f"u{i}", device, ip, ua, created, now)


def test_no_single_rule_reaches_the_threshold() -> None:
    rules = AbuseConfig().rules
    assert max(r["points"] for r in rules.values()) < THRESHOLD


async def test_device_rule_boundary_and_rescore_catches_early_accounts(redis: Redis) -> None:
    cfg = AbuseConfig()
    t = time.time()
    entries = [ctx(i, device="dev-farm", ip=f"50.{i}.1.1", t=t + i) for i in range(5)]
    at_entry = [await score_entry(redis, cfg, DROP, c) for c in entries]
    assert ["device_shared" in r.flags for r in at_entry] == [False, False, False, True, True]
    final = await rescore(redis, cfg, DROP, entries)
    assert all("device_shared" in final[c.entry_id].flags for c in entries)
    assert all(final[c.entry_id].score < THRESHOLD for c in entries)  # one signal never flags


async def test_shared_campus_wifi_30_humans_not_flagged(redis: Redis) -> None:
    """30 genuine people on one campus network: same public IP, own phones, typing their OTP."""
    cfg = AbuseConfig()
    rng = random.Random(7)  # noqa: S311 - seeded test data, not crypto
    t = time.time()
    people = []
    for i in range(30):
        await record_verify(redis, cfg, user_id=f"u{i}", latency_ms=rng.uniform(2500, 9000))
        people.append(
            ctx(
                i,
                device=f"phone-{i}",
                ip="128.2.40.17",
                ua=rng.choice([CHROME, SAFARI]),
                t=t + rng.uniform(0, 30),
            )
        )
    for c in people:
        await score_entry(redis, cfg, DROP, c)
    final = await rescore(redis, cfg, DROP, people)
    flagged = sum(1 for c in people if final[c.entry_id].score >= THRESHOLD)
    assert flagged / len(people) < 0.02, flagged


async def test_identity_farm_5000_mostly_flagged(redis: Redis) -> None:
    """5,000 farmed identities: 20 devices, 10 /24s, scripted OTP. Shared IP alone is not enough:
    each flag needs at least two independent signals (device + subnet, or + fast OTP)."""
    cfg = AbuseConfig()
    t = time.time()
    farm = []
    for i in range(5000):
        await record_verify(redis, cfg, user_id=f"u{i}", latency_ms=300)
        farm.append(
            ctx(
                i,
                device=f"farm-dev-{i % 20}",
                ip=f"45.77.{i % 10}.{1 + i % 250}",
                ua="python-httpx/0.27",
                t=t + i * 0.01,
            )
        )
    for c in farm:
        await score_entry(redis, cfg, DROP, c)
    final = await rescore(redis, cfg, DROP, farm)
    flagged = sum(1 for c in farm if final[c.entry_id].score >= THRESHOLD)
    assert flagged / len(farm) > 0.9, flagged
    assert all({"device_shared", "subnet_burst"} <= set(final[c.entry_id].flags) for c in farm)


async def test_one_user_switching_networks_adds_nothing(redis: Redis) -> None:
    """Verify on Wi-Fi, enter on mobile data: the person counts once per network, never twice."""
    cfg = AbuseConfig()
    await record_verify(redis, cfg, user_id="u1", latency_ms=4200)
    r = await score_entry(redis, cfg, DROP, ctx(1, device="phone-1", ip="100.64.12.9"))
    final = await rescore(redis, cfg, DROP, [ctx(1, device="phone-1", ip="100.64.12.9")])
    assert r.score == 0 and final["e1"].score == 0


async def test_old_accounts_do_not_count_as_a_new_subnet_burst(redis: Redis) -> None:
    cfg = AbuseConfig()
    t = time.time()
    users = [ctx(i, device=f"d{i}", ip="9.9.9.9", t=t, new=False) for i in range(40)]
    for c in users:
        await score_entry(redis, cfg, DROP, c)
    final = await rescore(redis, cfg, DROP, users)
    assert all("subnet_burst" not in final[c.entry_id].flags for c in users)


async def test_l7_off_scores_nothing_and_redis_down_marks_unavailable(redis: Redis) -> None:
    off = AbuseConfig().merged({"layers": {"L7": False}})
    assert (await score_entry(redis, off, DROP, ctx(1, device="d", ip="1.1.1.1"))).score == 0
    r = await score_entry(None, AbuseConfig(), DROP, ctx(2, device="d", ip="1.1.1.1"))
    assert r.score == 0 and r.flags == ["risk_unavailable"]


async def test_otp_per_phone_limit(redis: Redis) -> None:
    cfg = AbuseConfig()
    res = [
        await otp_guard(
            redis, cfg, phone_e164="+919876543210", phone_hash="h1", device_id="d1", ip="1.2.3.4"
        )
        for _ in range(4)
    ]
    assert res[:3] == [None, None, None]
    assert res[3] is not None and res[3].code == "OTP_THROTTLED" and res[3].retry_after_ms


async def test_otp_sequential_prefix_is_throttled(redis: Redis) -> None:
    cfg = AbuseConfig()
    out = []
    for i in range(25):
        out.append(
            await otp_guard(
                redis,
                cfg,
                phone_e164=f"+9198765430{i:02d}",
                phone_hash=f"seq{i}",
                device_id=f"dev{i}",
                ip=f"60.0.{i}.1",
            )
        )
    assert out[19] is None and out[20] is not None  # the 21st number with the same prefix


async def test_otp_campus_30_people_one_ip_not_throttled(redis: Redis) -> None:
    cfg = AbuseConfig()
    rng = random.Random(3)  # noqa: S311 - seeded test data, not crypto
    res = [
        await otp_guard(
            redis,
            cfg,
            phone_e164=f"+91{rng.randrange(7_000_000_000, 9_999_999_999)}",
            phone_hash=f"c{i}",
            device_id=f"phone{i}",
            ip="128.2.40.17",
        )
        for i in range(30)
    ]
    assert all(r is None for r in res)
