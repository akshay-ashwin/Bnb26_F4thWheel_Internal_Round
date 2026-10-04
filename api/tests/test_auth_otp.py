"""Phone hashing, OTP request and verify: single use, guess limit, races, hooks, Redis down."""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import inspect
from collections.abc import AsyncIterator
from typing import Any
from uuid import UUID

import asyncpg
import httpx
import pytest
import redis.asyncio as aioredis

from app.abuse import hooks
from app.config import Settings
from app.errors import InvalidPhone, OtpThrottled
from app.identity.otp import OtpStore
from app.identity.phone import PhoneIdentity, identify_phone, phone_hash
from app.identity.session import otp_key
from tests.auth_helpers import (
    DEVICE,
    OTHER_DEVICE,
    PHONE,
    PHONE_E164,
    login,
    request_otp,
    verify,
)
from tests.conftest import TEST_REDIS_URL
from tests.helpers import fire_concurrently

pytestmark = pytest.mark.usefixtures("clean_db", "clean_redis")


@pytest.fixture
async def redis_client() -> AsyncIterator[aioredis.Redis]:
    r = aioredis.Redis.from_url(TEST_REDIS_URL, decode_responses=True)
    yield r
    await r.aclose()


def _wrong(otp: str, i: int = 0) -> str:
    return f"{(int(otp) + 1 + i) % 10**6:06d}"


async def _fresh(client: httpx.AsyncClient, phone: str = PHONE) -> tuple[str, str]:
    body = (await request_otp(client, phone)).json()
    return body["request_id"], body["dev_otp"]


# --- phone normalisation and the peppered hash ---------------------------------------------


@pytest.mark.parametrize(
    "raw",
    [
        "+919876543210",
        "09876543210",
        "98765 43210",
        "+91 98765-43210",
        " 9876543210 ",
        "+91(98765)43210",
    ],
)
def test_formats_of_one_number_give_one_hash(raw: str, base_settings: Settings) -> None:
    ident = identify_phone(raw, base_settings.phone_pepper, 6)
    assert ident.e164 == PHONE_E164
    assert ident.phone_hash == identify_phone(PHONE, base_settings.phone_pepper, 6).phone_hash
    assert ident.prefix == "987654"


@pytest.mark.parametrize(
    "raw", ["12345", "abcdef", "+91 22 2345 6789", "+91 98765", "0000000000", "+"]
)
def test_invalid_or_non_mobile_numbers_are_rejected(raw: str, base_settings: Settings) -> None:
    with pytest.raises(InvalidPhone):
        identify_phone(raw, base_settings.phone_pepper, 6)


def test_hash_is_hmac_with_the_pepper_and_pepper_changes_it(base_settings: Settings) -> None:
    pepper = base_settings.phone_pepper
    expected = hmac.new(
        pepper.get_secret_value().encode(), PHONE_E164.encode(), hashlib.sha256
    ).hexdigest()
    assert phone_hash(pepper, PHONE_E164) == expected
    assert phone_hash(type(pepper)("x" * 40), PHONE_E164) != expected
    assert expected != hashlib.sha256(PHONE_E164.encode()).hexdigest()  # not a bare hash


def test_phone_identity_never_prints_its_fields(base_settings: Settings) -> None:
    ident = identify_phone(PHONE, base_settings.phone_pepper, 6)
    shown = repr(ident) + str(ident)
    assert "9876543210" not in shown
    assert ident.phone_hash not in shown
    assert isinstance(ident, PhoneIdentity)


async def test_invalid_phone_is_400_with_the_envelope(client: httpx.AsyncClient) -> None:
    r = await request_otp(client, phone="12345")
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "INVALID_PHONE"
    assert "12345" not in r.text


@pytest.mark.parametrize("device_id", ["short", "has space in it", "x" * 129, "bad/char/here1"])
async def test_bad_device_id_is_a_400_validation_error(
    client: httpx.AsyncClient, device_id: str
) -> None:
    r = await request_otp(client, device_id=device_id)
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "VALIDATION_ERROR"
    assert r.json()["error"]["details"]["fields"][0]["field"] == "body.device_id"


# --- request: dedupe, dev_otp, storage -----------------------------------------------------


async def test_same_phone_within_30s_gets_the_same_request_id(
    client: httpx.AsyncClient, redis_client: aioredis.Redis, base_settings: Settings
) -> None:
    first = (await request_otp(client)).json()
    second = (await request_otp(client, phone="09876543210")).json()  # another spelling
    assert second["request_id"] == first["request_id"]
    assert second["dev_otp"] == first["dev_otp"]  # SIM_MODE only: the simulator is never stranded
    ident = identify_phone(PHONE, base_settings.phone_pepper, 6)
    ttl = await redis_client.ttl("otp:phone:" + ident.phone_hash)
    assert 0 < ttl <= 30
    assert 0 < await redis_client.ttl("otp:req:" + first["request_id"]) <= 300

    # The 30 s key ends by its own expiry: a new request id and a new code follow.
    await redis_client.pexpire("otp:phone:" + ident.phone_hash, 50)
    await asyncio.sleep(0.12)
    third = (await request_otp(client)).json()
    assert third["request_id"] != first["request_id"]


async def test_parallel_requests_for_one_phone_agree_on_one_request(
    client: httpx.AsyncClient, redis_client: aioredis.Redis
) -> None:
    results = await fire_concurrently(25, lambda i: request_otp(client))
    ids = {r.json()["request_id"] for r in results}
    assert len(ids) == 1
    assert len([k async for k in redis_client.scan_iter("otp:req:*")]) == 1  # losers cleaned up


async def test_dev_otp_only_in_sim_mode_and_plaintext_never_stored_otherwise(
    client: httpx.AsyncClient,
    client_no_sim: httpx.AsyncClient,
    redis_client: aioredis.Redis,
) -> None:
    sim = (await request_otp(client)).json()
    assert len(sim["dev_otp"]) == 6
    assert await redis_client.hget("otp:req:" + sim["request_id"], "otp") == sim["dev_otp"]

    real = (await request_otp(client_no_sim, phone="+91 98765 43211")).json()
    assert real["dev_otp"] is None
    stored = await redis_client.hgetall("otp:req:" + real["request_id"])
    assert "otp" not in stored and set(stored) >= {"otp_hash", "phone_hash", "device_id"}
    assert len(stored["otp_hash"]) == 64


async def test_code_hash_is_bound_to_the_request_and_not_a_plain_hash(
    client: httpx.AsyncClient, redis_client: aioredis.Redis, base_settings: Settings
) -> None:
    a = (await request_otp(client, phone="+91 98765 00001")).json()
    stored = {
        str(k): str(v)
        for k, v in (await redis_client.hgetall("otp:req:" + a["request_id"])).items()
    }
    assert stored["otp_hash"] != hashlib.sha256(a["dev_otp"].encode()).hexdigest()
    store = OtpStore(client.app.state.cache, otp_key(base_settings), keep_plaintext=False)  # type: ignore[attr-defined]
    assert store.hash_otp(a["request_id"], stored["phone_hash"], a["dev_otp"]) == stored["otp_hash"]
    # The same code under another request id or phone hashes differently.
    other = store.hash_otp("other-request-id", stored["phone_hash"], a["dev_otp"])
    assert other != stored["otp_hash"]
    assert store.hash_otp(a["request_id"], "other-phone", a["dev_otp"]) != stored["otp_hash"]


# --- verify: single use, constant-time compare, guess limit --------------------------------


async def test_verify_succeeds_once_and_the_code_is_single_use(
    client: httpx.AsyncClient, redis_client: aioredis.Redis
) -> None:
    rid, otp = await _fresh(client)
    ok = await verify(client, rid, otp)
    assert ok.status_code == 200
    assert await redis_client.exists("otp:req:" + rid) == 0
    again = await verify(client, rid, otp)
    assert again.status_code == 410 and again.json()["error"]["code"] == "OTP_EXPIRED"
    # Consuming also freed the dedupe key: the next request is a new one.
    assert (await _fresh(client))[0] != rid


async def test_unknown_malformed_and_expired_requests_look_the_same(
    client: httpx.AsyncClient, redis_client: aioredis.Redis
) -> None:
    for rid in ["does-not-exist-1", "x", "has spaces and ../ stuff", "a" * 64]:
        r = await verify(client, rid, "123456")
        assert (r.status_code, r.json()["error"]["code"]) == (410, "OTP_EXPIRED")
    rid, otp = await _fresh(client)
    await redis_client.delete("otp:req:" + rid)  # what the 300 s TTL does
    assert (await verify(client, rid, otp)).status_code == 410


async def test_wrong_code_counts_attempts_and_five_wrong_kill_the_request(
    client: httpx.AsyncClient, redis_client: aioredis.Redis
) -> None:
    rid, otp = await _fresh(client)
    for n in range(1, 6):
        r = await verify(client, rid, _wrong(otp, n))
        assert (r.status_code, r.json()["error"]["code"]) == (401, "OTP_INVALID")
        assert await redis_client.hget("otp:req:" + rid, "attempts") == str(n)
    sixth = await verify(client, rid, otp)  # the CORRECT code, one guess too late
    assert (sixth.status_code, sixth.json()["error"]["code"]) == (401, "OTP_INVALID")
    assert await redis_client.exists("otp:req:" + rid) == 0
    assert (await verify(client, rid, otp)).status_code == 410
    assert (await _fresh(client))[0] != rid  # a dead request_id is not handed out again


async def test_correct_code_within_five_attempts_still_works(client: httpx.AsyncClient) -> None:
    rid, otp = await _fresh(client)
    for n in range(4):
        assert (await verify(client, rid, _wrong(otp, n))).status_code == 401
    assert (await verify(client, rid, otp)).status_code == 200


async def test_correct_code_is_rejected_after_five_parallel_wrong_guesses(
    client: httpx.AsyncClient, redis_client: aioredis.Redis
) -> None:
    rid, otp = await _fresh(client)
    wrong = await fire_concurrently(5, lambda i: verify(client, rid, _wrong(otp, i)))
    assert [r.status_code for r in wrong] == [401] * 5
    assert await redis_client.hget("otp:req:" + rid, "attempts") == "5"
    late = await verify(client, rid, otp)
    assert (late.status_code, late.json()["error"]["code"]) == (401, "OTP_INVALID")
    assert await redis_client.exists("otp:req:" + rid) == 0


async def test_a_flood_of_parallel_guesses_cannot_win_with_the_right_code_last(
    client: httpx.AsyncClient, redis_client: aioredis.Redis, db: asyncpg.Connection
) -> None:
    rid, otp = await _fresh(client)
    codes = [_wrong(otp, i) for i in range(49)] + [otp]  # the right one is among 50 in flight
    results = await fire_concurrently(50, lambda i: verify(client, rid, codes[i]))
    # Atomic counting: at most 5 requests were allowed to compare, so the right code only wins
    # if it was among the first five to arrive. Whatever happened, never more than one session.
    assert sum(r.status_code == 200 for r in results) <= 1
    odd = [r.text for r in results if r.status_code not in (200, 401, 410)]
    assert not odd, odd[:2]
    assert await db.fetchval("SELECT count(*) FROM users") <= 1
    assert await redis_client.exists("otp:req:" + rid) == 0


async def test_parallel_correct_verifies_yield_exactly_one_session(
    client: httpx.AsyncClient, db: asyncpg.Connection
) -> None:
    rid, otp = await _fresh(client)
    # 5 is the attempt limit, so all five reach the comparison; only one can consume the code.
    results = await fire_concurrently(5, lambda i: verify(client, rid, otp))
    assert sorted(r.status_code for r in results) == [200, 410, 410, 410, 410]
    assert await db.fetchval("SELECT count(*) FROM users") == 1
    assert await db.fetchval("SELECT count(*) FROM sessions") == 1


async def test_a_larger_burst_of_correct_verifies_never_makes_two_sessions(
    client: httpx.AsyncClient, db: asyncpg.Connection
) -> None:
    rid, otp = await _fresh(client)
    # Beyond 5 in flight, the extra guesses use up the attempt limit and the request is deleted,
    # which can take the code away from the would-be winner too. That costs this one request
    # (the person asks for a new code); it must never produce two sessions.
    results = await fire_concurrently(20, lambda i: verify(client, rid, otp))
    assert sum(r.status_code == 200 for r in results) <= 1
    assert {r.status_code for r in results} <= {200, 401, 410}
    assert await db.fetchval("SELECT count(*) FROM users") <= 1
    assert await db.fetchval("SELECT count(*) FROM sessions") <= 1


async def test_code_used_from_another_device_is_refused_and_counts(
    client: httpx.AsyncClient, redis_client: aioredis.Redis
) -> None:
    rid, otp = await _fresh(client)
    relayed = await verify(client, rid, otp, device_id=OTHER_DEVICE)
    assert (relayed.status_code, relayed.json()["error"]["code"]) == (401, "OTP_INVALID")
    assert await redis_client.hget("otp:req:" + rid, "attempts") == "1"
    assert (await verify(client, rid, otp)).status_code == 200  # the real device still can


# --- hooks and failure paths ---------------------------------------------------------------


def test_hook_signatures_are_the_final_ones() -> None:
    guard = inspect.signature(hooks.otp_request_guard)
    verified = inspect.signature(hooks.on_identity_verified)
    assert list(guard.parameters) == ["phone_hash", "phone_prefix", "device_id", "client_ip"]
    assert list(verified.parameters) == [
        "user_id",
        "device_id",
        "client_ip",
        "ua_hash",
        "verify_latency_ms",
    ]
    for sig in (guard, verified):
        assert all(p.kind is inspect.Parameter.KEYWORD_ONLY for p in sig.parameters.values())
        assert sig.return_annotation in (None, "None")


async def test_hooks_are_called_with_the_expected_values(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch, base_settings: Settings
) -> None:
    seen: dict[str, dict[str, Any]] = {}

    async def guard(**kw: Any) -> None:
        seen["guard"] = kw

    async def verified(**kw: Any) -> None:
        seen["verified"] = kw

    monkeypatch.setattr(hooks, "otp_request_guard", guard)
    monkeypatch.setattr(hooks, "on_identity_verified", verified)
    done = await login(client, device_id=DEVICE, **{"X-Sim-Client-IP": "203.0.113.9"})

    ident = identify_phone(PHONE, base_settings.phone_pepper, base_settings.phone_prefix_digits)
    assert seen["guard"] == {
        "phone_hash": ident.phone_hash,
        "phone_prefix": "987654",
        "device_id": DEVICE,
        "client_ip": "203.0.113.9",
    }
    v = seen["verified"]
    assert set(v) == {"user_id", "device_id", "client_ip", "ua_hash", "verify_latency_ms"}
    assert isinstance(v["user_id"], UUID) and v["client_ip"] == "203.0.113.9"
    assert 0 <= v["verify_latency_ms"] < 5000 and len(v["ua_hash"]) == 64
    assert done.user_public_id


async def test_a_throttling_guard_stops_the_request_before_any_code_exists(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch, redis_client: aioredis.Redis
) -> None:
    async def guard(**kw: Any) -> None:
        raise OtpThrottled(retry_after_ms=5000)

    monkeypatch.setattr(hooks, "otp_request_guard", guard)
    r = await request_otp(client)
    assert (r.status_code, r.json()["error"]["code"]) == (429, "OTP_THROTTLED")
    assert r.headers["Retry-After"] == "5"
    assert [k async for k in redis_client.scan_iter("otp:*")] == []


async def test_failed_sms_leaves_no_usable_request(
    client: httpx.AsyncClient, redis_client: aioredis.Redis
) -> None:
    class Boom:
        async def send_otp(self, *, phone_e164: str, phone_hash: str, otp: str) -> None:
            raise RuntimeError("provider down")

    client.app.state.sms = Boom()  # type: ignore[attr-defined]
    r = await request_otp(client)
    assert (r.status_code, r.json()["error"]["code"]) == (503, "SERVICE_UNAVAILABLE")
    assert [k async for k in redis_client.scan_iter("otp:*")] == []


async def test_an_open_breaker_does_not_block_login(client: httpx.AsyncClient) -> None:
    """Login has no fallback, so the fast path's breaker must not take it down: during a burst the
    50 ms fast timeout can trip while Redis itself is fine (measured: 159 of 200 logins got 503)."""
    breaker = client.app.state.cache.breaker  # type: ignore[attr-defined]
    for _ in range(3):
        breaker.record_failure()
    assert breaker.is_open
    done = await login(client)
    assert done.user_public_id


async def test_redis_down_means_503_with_retry_after_for_both_endpoints(
    client_redis_down: httpx.AsyncClient,
) -> None:
    for r in (
        await request_otp(client_redis_down),
        await verify(client_redis_down, "some-request-id", "123456"),
    ):
        assert r.status_code == 503
        assert r.json()["error"]["code"] == "SERVICE_UNAVAILABLE"
        assert r.headers["Retry-After"] == "1"
