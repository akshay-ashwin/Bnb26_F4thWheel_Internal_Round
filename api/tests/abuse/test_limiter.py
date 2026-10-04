"""L1–L3 limiter against a real Redis.  Run: docker compose run --rm api pytest tests/abuse"""

from __future__ import annotations

import asyncio
import math
import statistics
import time

import httpx
from redis.asyncio import Redis

from app.abuse.config import AbuseConfig, ConfigStore
from app.abuse.limiter import Limiter, RequestKey, endpoint_group, net24_of, session_id_from_token

from .conftest import DROP, SECRET, Harness, build_app, hdr, token

ME = f"/api/drops/{DROP}/me"
ENTRIES = f"/api/drops/{DROP}/entries"


def test_endpoint_groups_and_token_check() -> None:
    assert endpoint_group("GET", f"/api/drops/{DROP}/me") == "me"
    assert endpoint_group("POST", f"/api/drops/{DROP}/step-up") == "step_up"
    assert endpoint_group("GET", f"/api/drops/{DROP}") == "read"
    assert endpoint_group("POST", "/api/auth/otp/request") == "otp"
    assert endpoint_group("GET", "/api/admin/drops/x/metrics") is None
    assert endpoint_group("POST", "/api/sim/telemetry") is None
    assert net24_of("192.168.7.42") == "192.168.7.0"
    assert session_id_from_token(token("abc"), SECRET) == "abc"
    assert session_id_from_token("abc.forged", SECRET) is None
    assert session_id_from_token("garbage", SECRET) is None


async def test_human_pace_is_never_limited(harness: Harness) -> None:
    """Ten users, two tabs each, polling /me at the fastest server pace (1 s) plus a refresh,
    after entering several times (impatient double clicks)."""
    codes: list[int] = []

    async def user(i: int) -> None:
        sid = f"human-{i}"
        h = hdr(f"10.1.0.{i}", sid)
        for _ in range(5):
            codes.append((await harness.client.post(ENTRIES, headers=h)).status_code)
        for rnd in range(4):
            for _tab in range(2):
                codes.append((await harness.client.get(ME, headers=h)).status_code)
            if rnd == 1:
                codes.append((await harness.client.get(ME, headers=h)).status_code)  # refresh
            await asyncio.sleep(1.0)

    await asyncio.gather(*(user(i) for i in range(10)))
    assert codes.count(429) == 0, codes.count(429)


async def test_flood_from_one_ip_is_rejected_without_backend_work(harness: Harness) -> None:
    n = 5000
    codes = [(await harness.client.get(ME, headers=hdr("6.6.6.6"))).status_code for _ in range(n)]
    rejected = codes.count(429)
    allowed = n - rejected
    assert rejected / n > 0.99, rejected / n
    assert harness.hits["n"] == allowed  # rejected requests never reached the handler


async def test_campus_nat_2000_users_one_slash24(harness: Harness) -> None:
    """2,000 signed-in users on one /24 (100 per IP, as behind NAT), one request each."""

    async def one(i: int) -> int:
        ip = f"172.20.5.{1 + i % 20}"
        r = await harness.client.get(ME, headers=hdr(ip, f"campus-{i}"))
        return r.status_code

    codes = await asyncio.gather(*(one(i) for i in range(2000)))
    assert codes.count(429) == 0, codes.count(429)


async def test_rejected_request_consumes_no_tokens(harness: Harness, redis: Redis) -> None:
    sid = "tight"
    for _ in range(6):  # session burst for entries is 6
        assert (await harness.client.post(ENTRIES, headers=hdr("10.9.9.9", sid))).status_code == 200
    ip_key, net_key, l1_key = "rl:L2:ipa:10.9.9.9", "rl:L2:n:10.9.9.0", "rl:L1:entries:auth"
    before = {k: float(await redis.hget(k, "t") or 0) for k in (ip_key, net_key, l1_key)}
    for _ in range(20):
        r = await harness.client.post(ENTRIES, headers=hdr("10.9.9.9", sid))
        assert r.status_code == 429 and r.headers["x-ratelimit-layer"] == "L3"
    after = {k: float(await redis.hget(k, "t") or 0) for k in (ip_key, net_key, l1_key)}
    assert after == before  # the L3 rejection spent nothing from the L2 or L1 buckets


async def test_unauthenticated_flood_cannot_use_signed_in_pool(redis: Redis) -> None:
    cfg = AbuseConfig().merged(
        {
            "limits": {
                "L1": {"entries": {"anon": [5, 5], "auth": [1000, 1000]}},
                "L2": {"ip_anon": [1000, 1000]},
            }
        }
    )
    app, *_ = build_app(redis, cfg)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
        anon = [
            (await c.post(ENTRIES, headers=hdr(f"9.{i % 250}.0.1"))).status_code for i in range(200)
        ]
        assert anon.count(429) >= 190  # the anonymous pool is tiny and runs dry
        forged = await c.post(
            ENTRIES,
            headers={"X-Sim-Client-IP": "9.9.9.9", "Authorization": "Bearer someone.forged"},
        )
        assert forged.status_code == 429  # a forged token counts as anonymous
        authed = [
            (await c.post(ENTRIES, headers=hdr(f"11.0.{i}.1", f"user-{i}"))).status_code
            for i in range(100)
        ]
        assert authed.count(429) == 0  # signed-in users still have their reserved capacity


async def test_cooldown_triggers_and_expires(redis: Redis) -> None:
    cfg = AbuseConfig().merged(
        {
            "thresholds": {"cooldown_violations": 3, "cooldown_ms": 1200},
            "limits": {"L2": {"ip_anon": [1, 1]}},
        }
    )
    app, *_ = build_app(redis, cfg)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
        for _ in range(5):
            await c.get(ME, headers=hdr("7.7.7.7"))
        assert await redis.pttl("cd:ip:7.7.7.7") > 0
        await asyncio.sleep(1.1)  # bucket has refilled, but the cooldown still holds
        assert (await c.get(ME, headers=hdr("7.7.7.7"))).status_code == 429
        await asyncio.sleep(0.5)
        assert (await c.get(ME, headers=hdr("7.7.7.7"))).status_code == 200
        # signed-in traffic from the same IP is never put in the IP cooldown
        await redis.set("cd:ip:7.7.7.7", "1", px=5000)
        assert (await c.get(ME, headers=hdr("7.7.7.7", "real-user"))).status_code == 200


async def test_live_switch_reaches_other_workers_within_a_second(redis: Redis) -> None:
    slow = {"limits": {"L2": {"ip_anon": [0.01, 1]}}}  # refill too slow to pass on its own
    app, store_a, *_ = build_app(redis, AbuseConfig().merged(slow))
    app_b, store_b, *_ = build_app(redis, AbuseConfig().merged(slow))  # a second "worker"
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app_b), base_url="http://t"
    ) as b:
        flood = [(await b.get(ME, headers=hdr("5.5.5.5"))).status_code for _ in range(10)]
        assert flood.count(429) == 9
        await store_a.update({"layers": {"L2": False, "L1": False}})
        t0 = time.monotonic()
        while time.monotonic() - t0 < 2.5:
            if (await b.get(ME, headers=hdr("5.5.5.5"))).status_code == 200:
                break
            await asyncio.sleep(0.05)
        assert time.monotonic() - t0 < 1.5
        assert store_b.current().on("L2") is False


async def test_redis_down_falls_back_to_local_buckets() -> None:
    dead = Redis.from_url("redis://127.0.0.1:1/0", socket_connect_timeout=0.05)
    app, _store, limiter, _ = build_app(dead)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
        codes = [(await c.get(ME, headers=hdr("8.8.8.8"))).status_code for _ in range(300)]
        human = (await c.get(ME, headers=hdr("8.8.4.4", "human"))).status_code
    assert 500 not in codes and codes.count(429) > 200  # neither fails open nor errors
    assert human == 200  # nor fails closed
    assert limiter.redis_failures > 0
    await dead.aclose()


async def test_retry_after_matches_and_retry_succeeds(harness: Harness) -> None:
    sid = "retrier"
    r = None
    for _ in range(10):
        r = await harness.client.post(ENTRIES, headers=hdr("10.3.3.3", sid))
        if r.status_code == 429:
            break
    assert r is not None and r.status_code == 429
    ms = r.json()["error"]["retry_after_ms"]
    assert int(r.headers["retry-after"]) == math.ceil(ms / 1000)
    assert "server_time" in r.json()
    await asyncio.sleep(ms / 1000 + 0.02)
    assert (await harness.client.post(ENTRIES, headers=hdr("10.3.3.3", sid))).status_code == 200


async def test_exempt_paths_skip_the_limiter(harness: Harness) -> None:
    for _ in range(300):
        assert (await harness.client.get("/api/healthz", headers=hdr("6.6.6.7"))).status_code == 200
        assert (
            await harness.client.get(f"/api/admin/drops/{DROP}/metrics", headers=hdr("6.6.6.7"))
        ).status_code == 200


async def test_reject_path_cpu_time(redis: Redis) -> None:
    """CPU (not wall) time of one limiter decision on the reject path, real Redis call included."""
    limiter = Limiter(redis, session_secret=SECRET, workers=1)
    store = ConfigStore(redis)
    cfg = store.current()
    rk = RequestKey("me", "4.4.4.4", "4.4.4.0", None, None)
    for _ in range(200):
        await limiter.check(rk, cfg)
    samples = []
    for _ in range(1000):
        t0 = time.process_time_ns()
        d = await limiter.check(rk, cfg)
        samples.append((time.process_time_ns() - t0) / 1e6)
        assert d.outcome == "reject"
    samples.sort()
    p50, p99 = statistics.median(samples), samples[int(0.99 * len(samples))]
    print(f"\nreject path CPU ms: p50={p50:.3f} p99={p99:.3f}")
    assert p50 < 1.0
