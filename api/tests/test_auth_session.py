"""Users, sessions, tokens, expiry, revocation, cookie flags and client address extraction."""

from __future__ import annotations

import hashlib
import hmac
import re
import time
from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from typing import Any
from uuid import UUID

import asyncpg
import httpx
import pytest
import redis.asyncio as aioredis
from starlette.requests import Request

from app import db as app_db_module
from app.config import Settings
from app.identity import session as sess
from app.identity import users
from app.identity.client import client_info, network_of, resolve_ip
from app.identity.phone import identify_phone
from tests.auth_helpers import DEVICE, OTHER_DEVICE, PHONE, add_whoami, login, request_otp, verify
from tests.conftest import TEST_REDIS_URL
from tests.helpers import fire_concurrently, make_user

pytestmark = pytest.mark.usefixtures("clean_db", "clean_redis")

ClientFactory = Callable[..., AbstractAsyncContextManager[httpx.AsyncClient]]


@pytest.fixture
async def redis_client() -> Any:
    r = aioredis.Redis.from_url(TEST_REDIS_URL, decode_responses=True)
    yield r
    await r.aclose()


@pytest.fixture
async def whoami(client: httpx.AsyncClient) -> httpx.AsyncClient:
    add_whoami(client.app)  # type: ignore[attr-defined]
    return client


def _bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _sid(token: str) -> UUID:
    return UUID(token.partition(".")[0])


# --- users -----------------------------------------------------------------------------------


async def test_login_creates_one_user_with_a_hashed_phone_and_a_random_public_id(
    client: httpx.AsyncClient, db: asyncpg.Connection, base_settings: Settings
) -> None:
    done = await login(client)
    rows = await db.fetch("SELECT * FROM users")
    assert len(rows) == 1
    user = rows[0]
    ident = identify_phone(PHONE, base_settings.phone_pepper, 6)
    assert user["phone_hash"] == ident.phone_hash
    assert re.fullmatch(r"[a-z2-7]{26}", user["public_id"])
    assert user["public_id"] == done.user_public_id
    assert user["first_device_id"] == DEVICE
    # No column of any table holds a form of the phone number.
    dump = " ".join(
        [
            await db.fetchval("SELECT string_agg(to_jsonb(u)::text, ' ') FROM users u"),
            await db.fetchval("SELECT string_agg(to_jsonb(s)::text, ' ') FROM sessions s"),
        ]
    )
    for form in ("9876543210", "919876543210", "+919876543210", "98765 43210"):
        assert form not in dump


async def test_50_concurrent_upserts_for_one_phone_create_one_user(
    client: httpx.AsyncClient, db: asyncpg.Connection
) -> None:
    pool = client.app.state.pool  # type: ignore[attr-defined]

    async def one(i: int) -> users.UserRow:
        async with app_db_module.transaction(pool, acquire_timeout_s=5) as conn:
            return await users.upsert_user(
                conn,
                phone_hash="ab" * 32,
                device_id=f"device-{i:04d}-xxxx",
                client_ip="203.0.113.5",
            )

    rows = await fire_concurrently(50, one)
    assert len({r.id for r in rows}) == 1
    assert len({r.public_id for r in rows}) == 1
    assert await db.fetchval("SELECT count(*) FROM users") == 1


async def test_50_concurrent_session_creations_for_one_device_make_one_session(
    client: httpx.AsyncClient, db: asyncpg.Connection, base_settings: Settings
) -> None:
    pool = client.app.state.pool  # type: ignore[attr-defined]
    user_id = await make_user(db)

    async def one(i: int) -> UUID:
        async with app_db_module.transaction(pool, acquire_timeout_s=5) as conn:
            sid, _ = await users.get_or_create_session(
                conn,
                base_settings,
                user_id=user_id,
                device_id=DEVICE,
                client_ip="203.0.113.5",
                ua_hash="u",
            )
            return sid

    ids = await fire_concurrently(50, one)
    assert len(set(ids)) == 1
    assert await db.fetchval("SELECT count(*) FROM sessions") == 1


async def test_different_phones_are_different_users(client: httpx.AsyncClient) -> None:
    a = await login(client, phone="+91 98765 43210")
    b = await login(client, phone="+91 98765 43211")
    assert a.user_public_id != b.user_public_id


# --- sessions: reuse, cookie, bearer -----------------------------------------------------------


async def test_reverify_same_device_returns_the_same_session_other_device_a_new_one(
    client: httpx.AsyncClient, db: asyncpg.Connection
) -> None:
    first = await login(client)
    again = await login(client)
    assert again.token == first.token and again.user_public_id == first.user_public_id
    other = await login(client, device_id=OTHER_DEVICE)
    assert other.token != first.token and other.user_public_id == first.user_public_id
    assert await db.fetchval("SELECT count(*) FROM users") == 1
    assert await db.fetchval("SELECT count(*) FROM sessions") == 2


async def test_cookie_flags(client_factory: ClientFactory) -> None:
    async with client_factory() as c:
        body = (await request_otp(c)).json()
        r = await verify(c, body["request_id"], body["dev_otp"])
    header = r.headers["set-cookie"]
    assert header.startswith("fd_session=")
    lowered = header.lower()
    assert "httponly" in lowered and "samesite=lax" in lowered and "path=/api" in lowered
    assert "max-age=86400" in lowered and "secure" not in lowered and "domain" not in lowered
    assert r.json()["session_token"] in header

    async with client_factory(cookie_secure=True) as c:
        body = (await request_otp(c)).json()
        r = await verify(c, body["request_id"], body["dev_otp"])
    assert "secure" in r.headers["set-cookie"].lower()


async def test_cookie_and_bearer_identify_the_same_user_and_session(
    whoami: httpx.AsyncClient,
) -> None:
    done = await login(whoami)
    by_cookie = await whoami.get("/api/_whoami")  # the client jar holds fd_session
    whoami.cookies.clear()
    by_bearer = await whoami.get("/api/_whoami", headers=_bearer(done.token))
    assert by_cookie.status_code == by_bearer.status_code == 200
    assert by_cookie.json() == by_bearer.json()
    assert by_cookie.json()["user_public_id"] == done.user_public_id
    expected_sid_hash = hashlib.sha256(str(_sid(done.token)).encode()).hexdigest()
    assert by_cookie.json()["sid_hash"] == expected_sid_hash
    assert str(_sid(done.token)) not in by_cookie.text  # the raw session id never leaves


async def test_missing_and_tampered_credentials_are_401(whoami: httpx.AsyncClient) -> None:
    done = await login(whoami)
    whoami.cookies.clear()
    uuid_part, _, sig = done.token.partition(".")
    flipped = sig[:-1] + ("A" if sig[-1] != "A" else "B")
    other_sid = "00000000-0000-4000-8000-000000000000"
    bad = [
        "",
        "garbage",
        f"{uuid_part}.",
        f"{uuid_part}.{flipped}",
        f"{other_sid}.{sig}",  # a valid signature for a different id
        f"{uuid_part.upper()}.{sig}",
        f"{uuid_part}.{sig}.extra",
        f"{uuid_part}{sig}",
        "." + sig,
        done.token + "x" * 200,
    ]
    assert (await whoami.get("/api/_whoami")).status_code == 401
    for token in bad:
        r = await whoami.get("/api/_whoami", headers=_bearer(token))
        assert (r.status_code, r.json()["error"]["code"]) == (401, "UNAUTHENTICATED"), token
    assert (await whoami.get("/api/_whoami", headers=_bearer(done.token))).status_code == 200


async def test_token_signed_with_another_secret_is_rejected(
    whoami: httpx.AsyncClient, base_settings: Settings
) -> None:
    done = await login(whoami)
    whoami.cookies.clear()
    forged_sig = hmac.new(b"not-the-secret" * 4, str(_sid(done.token)).encode(), hashlib.sha256)
    import base64

    sig = base64.urlsafe_b64encode(forged_sig.digest()).rstrip(b"=").decode()
    r = await whoami.get("/api/_whoami", headers=_bearer(f"{_sid(done.token)}.{sig}"))
    assert r.status_code == 401


async def test_a_bad_cookie_does_not_fall_through_to_a_good_bearer(
    whoami: httpx.AsyncClient,
) -> None:
    done = await login(whoami)
    whoami.cookies.clear()
    r = await whoami.get(
        "/api/_whoami", headers={**_bearer(done.token), "Cookie": "fd_session=not-a-token"}
    )
    assert r.status_code == 401


# --- revocation and expiry ---------------------------------------------------------------------


async def test_revocation_is_immediate_even_when_the_session_is_cached(
    whoami: httpx.AsyncClient, redis_client: aioredis.Redis
) -> None:
    done = await login(whoami)
    whoami.cookies.clear()
    assert (await whoami.get("/api/_whoami", headers=_bearer(done.token))).status_code == 200
    key = f"sess:{_sid(done.token)}"
    assert 0 < await redis_client.ttl(key) <= 60  # it IS cached

    app = whoami.app  # type: ignore[attr-defined]
    assert await sess.revoke_session(app.state.pool, app.state.cache, _sid(done.token)) is True
    assert await redis_client.exists(key) == 0
    assert (await whoami.get("/api/_whoami", headers=_bearer(done.token))).status_code == 401
    assert await redis_client.exists(key) == 0  # revoked sessions are never cached
    assert await sess.revoke_session(app.state.pool, app.state.cache, _sid(done.token)) is False

    fresh = await login(whoami, device_id=DEVICE)  # a revoked device slot can be reused
    assert fresh.token != done.token
    whoami.cookies.clear()
    assert (await whoami.get("/api/_whoami", headers=_bearer(fresh.token))).status_code == 200


async def test_session_expiry_is_enforced_for_cookie_and_bearer_cached_or_not(
    whoami: httpx.AsyncClient,
    redis_client: aioredis.Redis,
    base_settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    done = await login(whoami)
    cookie = whoami.cookies.get("fd_session")
    assert cookie == done.token
    assert (await whoami.get("/api/_whoami")).status_code == 200  # cookie, cached
    key = f"sess:{_sid(done.token)}"
    assert await redis_client.exists(key) == 1

    ttl = base_settings.session_ttl_s
    monkeypatch.setattr(sess, "_now", lambda: time.time() + ttl + 5)
    # 1. Cache entry still present: its own expiry stamp rejects it.
    assert (await whoami.get("/api/_whoami")).status_code == 401  # cookie
    assert (await whoami.get("/api/_whoami", headers=_bearer(done.token))).status_code == 401
    # 2. Cache gone (the 60 s TTL passed): the Postgres path rejects it too.
    await redis_client.delete(key)
    whoami.cookies.clear()
    r = await whoami.get("/api/_whoami", headers=_bearer(done.token))
    assert (r.status_code, r.json()["error"]["code"]) == (401, "UNAUTHENTICATED")
    assert await redis_client.exists(key) == 0  # an expired session is not cached again

    # Just before the end it still works: the boundary is the TTL, not a bug.
    monkeypatch.setattr(sess, "_now", lambda: time.time() + ttl - 30)
    assert (await whoami.get("/api/_whoami", headers=_bearer(done.token))).status_code == 200
    assert 0 < await redis_client.ttl(key) <= 30  # cache lifetime never outlives the session


async def test_reverify_after_expiry_issues_a_new_session_and_revokes_the_old(
    whoami: httpx.AsyncClient,
    db: asyncpg.Connection,
    base_settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    old = await login(whoami)
    whoami.cookies.clear()
    monkeypatch.setattr(sess, "_now", lambda: time.time() + base_settings.session_ttl_s + 5)
    new = await login(whoami)  # same phone, same device, after the old session ran out
    assert new.token != old.token
    monkeypatch.undo()
    whoami.cookies.clear()
    assert (await whoami.get("/api/_whoami", headers=_bearer(old.token))).status_code == 401
    assert (await whoami.get("/api/_whoami", headers=_bearer(new.token))).status_code == 200
    assert await db.fetchval("SELECT count(*) FROM sessions WHERE revoked_at IS NULL") == 1


async def test_redis_down_sessions_fall_back_to_postgres(
    client_redis_down: httpx.AsyncClient, db: asyncpg.Connection, base_settings: Settings
) -> None:
    add_whoami(client_redis_down.app)  # type: ignore[attr-defined]
    user_id = await make_user(db)
    sid = await db.fetchval(
        "INSERT INTO sessions (user_id, device_id) VALUES ($1, $2) RETURNING id", user_id, DEVICE
    )
    token = sess.issue_token(base_settings, sid)
    assert (await client_redis_down.get("/api/_whoami", headers=_bearer(token))).status_code == 200
    await db.execute("UPDATE sessions SET revoked_at = now() WHERE id = $1", sid)
    assert (await client_redis_down.get("/api/_whoami", headers=_bearer(token))).status_code == 401


# --- client address ----------------------------------------------------------------------------


def _request(headers: dict[str, str], peer: str | None = "203.0.113.7") -> Request:
    scope: dict[str, Any] = {
        "type": "http",
        "headers": [(k.lower().encode(), v.encode()) for k, v in headers.items()],
        "client": (peer, 5000) if peer else None,
    }
    return Request(scope)


def test_sim_client_ip_header_is_honoured_only_in_sim_mode(base_settings: Settings) -> None:
    sim = base_settings.model_copy(update={"sim_mode": True})
    real = base_settings.model_copy(update={"sim_mode": False})
    req = _request({"X-Sim-Client-IP": "198.51.100.77"})
    assert str(resolve_ip(req, sim)) == "198.51.100.77"
    assert str(resolve_ip(req, real)) == "203.0.113.7"
    assert str(resolve_ip(_request({"X-Sim-Client-IP": "not-an-ip"}), sim)) == "203.0.113.7"


def test_forwarded_for_is_believed_only_from_a_trusted_peer(base_settings: Settings) -> None:
    real = base_settings.model_copy(update={"sim_mode": False})
    spoof = {"X-Forwarded-For": "1.2.3.4"}
    # Direct caller (not a trusted proxy): the header is ignored.
    assert str(resolve_ip(_request(spoof, peer="203.0.113.7"), real)) == "203.0.113.7"
    # Trusted proxy: the client is the first untrusted address from the right.
    assert str(resolve_ip(_request(spoof, peer="10.0.0.5"), real)) == "1.2.3.4"
    chain = {"X-Forwarded-For": "9.9.9.9, 1.2.3.4, 10.0.0.9"}  # 9.9.9.9 is client-supplied
    assert str(resolve_ip(_request(chain, peer="10.0.0.5"), real)) == "1.2.3.4"
    assert (
        str(resolve_ip(_request({"X-Forwarded-For": "junk"}, peer="10.0.0.5"), real)) == "10.0.0.5"
    )
    assert str(resolve_ip(_request({}, peer="10.0.0.5"), real)) == "10.0.0.5"
    assert str(resolve_ip(_request({}, peer=None), real)) == "0.0.0.0"  # noqa: S104


def test_networks_and_ua_hash(base_settings: Settings) -> None:
    real = base_settings.model_copy(update={"sim_mode": False})
    assert network_of(resolve_ip(_request({}, peer="203.0.113.7"), real)) == "203.0.113.0/24"
    v6 = _request({}, peer="2001:db8:abcd:12:1:2:3:4")
    assert network_of(resolve_ip(v6, real)) == "2001:db8:abcd::/48"
    mapped = _request({}, peer="::ffff:203.0.113.7")
    assert str(resolve_ip(mapped, real)) == "203.0.113.7"
    long_a = client_info(_request({"User-Agent": "a" * 512 + "X"}), real).ua_hash
    long_b = client_info(_request({"User-Agent": "a" * 512 + "Y"}), real).ua_hash
    assert long_a == long_b == hashlib.sha256(b"a" * 512).hexdigest()
    assert client_info(_request({}), real).ua_hash == hashlib.sha256(b"").hexdigest()


async def test_x_sim_client_ip_is_ignored_over_http_when_sim_mode_is_off(
    client_factory: ClientFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.abuse import hooks

    seen: list[str] = []

    async def guard(**kw: Any) -> None:
        seen.append(kw["client_ip"])

    monkeypatch.setattr(hooks, "otp_request_guard", guard)
    for sim_mode, expected in ((False, "127.0.0.1"), (True, "198.51.100.77")):
        async with client_factory(sim_mode=sim_mode) as c:
            r = await request_otp(c, **{"X-Sim-Client-IP": "198.51.100.77"})
            assert r.status_code == 200
        assert seen[-1] == expected
