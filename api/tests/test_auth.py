"""Chunk 2: OTP login, sessions, bearer and cookie authentication."""

from __future__ import annotations

import asyncio
import dataclasses
import logging
import uuid

import asyncpg
import httpx
import pytest
from fastapi import Depends, FastAPI

from app.config import Settings
from app.deps import Session, current_session
from app.errors import AppError
from app.main import create_app
from app.security import normalize_phone, phone_hash, sign_session, verify_session_token
from tests.app_helpers import fresh_phone, login


@pytest.fixture(autouse=True)
def _whoami(app: FastAPI) -> None:
    """A throwaway protected route (the real ones arrive in chunk 3)."""
    if not any(getattr(r, "path", "") == "/api/_whoami" for r in app.router.routes):

        async def whoami(session: Session = Depends(current_session)) -> dict[str, str]:
            return {"user_public_id": session.user_public_id, "sid_hash": session.sid_hash}

        app.add_api_route("/api/_whoami", whoami, methods=["GET"])


DEVICE = "device-test-0001"


# --- pure functions ---


@pytest.mark.parametrize(
    "raw",
    [
        "9876543210",
        "+919876543210",
        "919876543210",
        "09876543210",
        "98765 43210",
        "+91-98765-43210",
    ],
)
def test_every_spelling_of_a_number_normalises_and_hashes_the_same(raw: str) -> None:
    assert normalize_phone(raw) == "+919876543210"
    assert phone_hash("pepper", normalize_phone(raw)) == phone_hash("pepper", "+919876543210")


@pytest.mark.parametrize(
    "raw", ["", "abc", "12345", "5876543210", "+14155552671", "98765432101", "+9198765"]
)
def test_invalid_phones_are_rejected(raw: str) -> None:
    with pytest.raises(AppError) as err:
        normalize_phone(raw)
    assert err.value.code == "INVALID_PHONE" and err.value.status == 400


def test_session_token_signature() -> None:
    sid = uuid.uuid4()
    token = sign_session("s" * 32, sid)
    assert verify_session_token("s" * 32, token) == sid
    assert verify_session_token("x" * 32, token) is None
    assert verify_session_token("s" * 32, token[:-2] + "AA") is None
    assert verify_session_token("s" * 32, "garbage") is None
    assert verify_session_token("s" * 32, str(sid)) is None


# --- OTP request ---


async def test_otp_request_returns_dev_otp_only_in_sim_mode(client: httpx.AsyncClient) -> None:
    r = await client.post(
        "/api/auth/otp/request", json={"phone": fresh_phone(), "device_id": DEVICE}
    )
    assert r.status_code == 200
    body = r.json()
    assert body["expires_in_s"] == 300 and len(body["dev_otp"]) == 6 and body["request_id"]


async def test_dev_otp_is_absent_when_sim_mode_is_off(settings: Settings, clean: None) -> None:
    prod_like = create_app(dataclasses.replace(settings, sim_mode=False))
    async with prod_like.router.lifespan_context(prod_like):
        transport = httpx.ASGITransport(app=prod_like)
        async with httpx.AsyncClient(transport=transport, base_url="http://t") as c:
            r = await c.post(
                "/api/auth/otp/request", json={"phone": fresh_phone(), "device_id": DEVICE}
            )
            assert r.status_code == 200 and "dev_otp" not in r.json()
            stored = await prod_like.state.cache.client.hgetall(f"otp:req:{r.json()['request_id']}")
            assert "dev_otp" not in stored  # not even stored


async def test_same_phone_within_dedupe_window_gets_the_same_request(
    client: httpx.AsyncClient,
) -> None:
    phone = fresh_phone()
    a = await client.post("/api/auth/otp/request", json={"phone": phone, "device_id": DEVICE})
    b = await client.post(
        "/api/auth/otp/request", json={"phone": "+91 " + phone, "device_id": DEVICE}
    )
    assert a.json()["request_id"] == b.json()["request_id"]
    assert a.json()["dev_otp"] == b.json()["dev_otp"]


async def test_concurrent_requests_for_one_phone_collapse_to_one_request(
    client: httpx.AsyncClient,
) -> None:
    phone = fresh_phone()
    rs = await asyncio.gather(
        *[
            client.post("/api/auth/otp/request", json={"phone": phone, "device_id": DEVICE})
            for _ in range(20)
        ]
    )
    assert {r.status_code for r in rs} == {200}
    assert len({r.json()["request_id"] for r in rs}) == 1
    assert len({r.json()["dev_otp"] for r in rs}) == 1


async def test_new_request_after_the_window(client: httpx.AsyncClient, app: FastAPI) -> None:
    phone = fresh_phone()
    a = await client.post("/api/auth/otp/request", json={"phone": phone, "device_id": DEVICE})
    h = phone_hash(app.state.settings.phone_pepper, normalize_phone(phone))
    await app.state.cache.client.delete(f"otp:phone:{h}")  # the 30 s window passed
    b = await client.post("/api/auth/otp/request", json={"phone": phone, "device_id": DEVICE})
    assert a.json()["request_id"] != b.json()["request_id"]


async def test_invalid_phone_and_bad_device_are_400(client: httpx.AsyncClient) -> None:
    r = await client.post("/api/auth/otp/request", json={"phone": "123", "device_id": DEVICE})
    assert r.status_code == 400 and r.json()["error"]["code"] == "INVALID_PHONE"
    r = await client.post("/api/auth/otp/request", json={"phone": fresh_phone(), "device_id": "x"})
    assert r.status_code == 400 and r.json()["error"]["code"] == "VALIDATION_ERROR"


# --- OTP verify ---


async def _request(
    client: httpx.AsyncClient, phone: str | None = None, device: str = DEVICE
) -> dict[str, str]:
    r = await client.post(
        "/api/auth/otp/request", json={"phone": phone or fresh_phone(), "device_id": device}
    )
    assert r.status_code == 200
    out: dict[str, str] = r.json()
    return out


async def test_full_login_flow_bearer_and_cookie(client: httpx.AsyncClient) -> None:
    req = await _request(client)
    v = await client.post(
        "/api/auth/otp/verify",
        json={"request_id": req["request_id"], "otp": req["dev_otp"], "device_id": DEVICE},
    )
    assert v.status_code == 200
    token, public_id = v.json()["session_token"], v.json()["user_public_id"]
    assert len(public_id) == 26 and public_id == public_id.lower()
    cookie = v.headers["set-cookie"].lower()
    assert "fd_session=" in cookie and "httponly" in cookie and "samesite=lax" in cookie
    assert "path=/api" in cookie and "max-age=86400" in cookie and "secure" not in cookie

    bearer = await client.get("/api/_whoami", headers={"Authorization": f"Bearer {token}"})
    assert bearer.status_code == 200 and bearer.json()["user_public_id"] == public_id
    async with httpx.AsyncClient(
        transport=client._transport,
        base_url="http://test",
        cookies={"fd_session": token},  # noqa: SLF001
    ) as cookie_client:
        by_cookie = await cookie_client.get("/api/_whoami")
        assert by_cookie.status_code == 200 and by_cookie.json() == bearer.json()


async def test_unauthenticated_requests_are_401(client: httpx.AsyncClient) -> None:
    for headers in ({}, {"Authorization": "Bearer nope"}, {"Authorization": "Basic abc"}):
        r = await client.get("/api/_whoami", headers=headers)
        assert r.status_code == 401 and r.json()["error"]["code"] == "UNAUTHENTICATED"


async def test_wrong_otp_counts_attempts_and_five_wrong_kill_the_request(
    client: httpx.AsyncClient,
) -> None:
    req = await _request(client)
    wrong = "000000" if req["dev_otp"] != "000000" else "111111"
    for _ in range(5):
        r = await client.post(
            "/api/auth/otp/verify",
            json={"request_id": req["request_id"], "otp": wrong, "device_id": DEVICE},
        )
        assert r.status_code == 401 and r.json()["error"]["code"] == "OTP_INVALID"
    dead = await client.post(
        "/api/auth/otp/verify",
        json={"request_id": req["request_id"], "otp": req["dev_otp"], "device_id": DEVICE},
    )
    assert dead.status_code == 401  # the 6th attempt hits the cap even with the right code
    gone = await client.post(
        "/api/auth/otp/verify",
        json={"request_id": req["request_id"], "otp": req["dev_otp"], "device_id": DEVICE},
    )
    assert gone.status_code == 410 and gone.json()["error"]["code"] == "OTP_EXPIRED"


async def test_unknown_and_expired_requests_are_410(client: httpx.AsyncClient) -> None:
    r = await client.post(
        "/api/auth/otp/verify",
        json={"request_id": "does-not-exist", "otp": "123456", "device_id": DEVICE},
    )
    assert r.status_code == 410 and r.json()["error"]["code"] == "OTP_EXPIRED"


async def test_otp_is_single_use_and_device_bound(client: httpx.AsyncClient) -> None:
    req = await _request(client)
    other = await client.post(
        "/api/auth/otp/verify",
        json={
            "request_id": req["request_id"],
            "otp": req["dev_otp"],
            "device_id": "device-other-99",
        },
    )
    assert other.status_code == 401  # OTP relayed to another device
    body = {"request_id": req["request_id"], "otp": req["dev_otp"], "device_id": DEVICE}
    assert (await client.post("/api/auth/otp/verify", json=body)).status_code == 200
    assert (await client.post("/api/auth/otp/verify", json=body)).status_code == 410


async def test_concurrent_verifies_of_one_otp_make_one_session_and_one_user(
    client: httpx.AsyncClient, db: asyncpg.Connection
) -> None:
    req = await _request(client)
    body = {"request_id": req["request_id"], "otp": req["dev_otp"], "device_id": DEVICE}
    rs = await asyncio.gather(*[client.post("/api/auth/otp/verify", json=body) for _ in range(50)])
    codes = sorted(r.status_code for r in rs)
    assert codes.count(200) == 1 and codes.count(410) == 49
    assert await db.fetchval("SELECT count(*) FROM users") == 1
    assert await db.fetchval("SELECT count(*) FROM sessions") == 1


async def test_reverify_same_device_returns_same_session_other_device_new_session(
    client: httpx.AsyncClient, db: asyncpg.Connection
) -> None:
    phone = fresh_phone()
    t1, p1 = await login(client, phone, "device-aaaa-0001")
    t2, p2 = await login(client, phone, "device-aaaa-0001")
    assert (t1, p1) == (t2, p2)
    app_client_phone_hash_requests = await client.post(
        "/api/auth/otp/request", json={"phone": phone, "device_id": "device-bbbb-0002"}
    )
    assert app_client_phone_hash_requests.status_code == 200
    t3, p3 = await login(client, phone, "device-bbbb-0002")
    assert p3 == p1 and t3 != t1
    assert await db.fetchval("SELECT count(*) FROM users") == 1
    assert await db.fetchval("SELECT count(*) FROM sessions") == 2


async def test_raw_phone_is_never_stored(client: httpx.AsyncClient, db: asyncpg.Connection) -> None:
    phone = fresh_phone()
    await login(client, phone)
    row = await db.fetchrow("SELECT phone_hash FROM users")
    assert phone not in row["phone_hash"] and len(row["phone_hash"]) == 64
    dump = await db.fetchval("SELECT string_agg(u::text, ' ') FROM users u")
    assert phone not in dump


# --- session validity ---


async def test_tampered_revoked_and_expired_sessions_are_rejected(
    client: httpx.AsyncClient, db: asyncpg.Connection
) -> None:
    token, _ = await login(client)
    ok = await client.get("/api/_whoami", headers={"Authorization": f"Bearer {token}"})
    assert ok.status_code == 200
    tampered = token[:-3] + ("AAA" if not token.endswith("AAA") else "BBB")
    assert (
        await client.get("/api/_whoami", headers={"Authorization": f"Bearer {tampered}"})
    ).status_code == 401

    await db.execute("UPDATE sessions SET revoked_at = now()")
    revoked = await client.get("/api/_whoami", headers={"Authorization": f"Bearer {token}"})
    assert revoked.status_code == 401  # effective immediately: no auth cache

    await db.execute(
        "UPDATE sessions SET revoked_at = NULL, created_at = now() - interval '25 hours'"
    )
    expired = await client.get("/api/_whoami", headers={"Authorization": f"Bearer {token}"})
    assert expired.status_code == 401


async def test_ip_device_and_user_agent_changes_do_not_invalidate_a_session(
    client: httpx.AsyncClient,
) -> None:
    token, public_id = await login(client)  # logged in from the default test address
    for ip, ua in [
        ("203.0.113.9", "Mozilla/5.0 iPhone"),
        ("198.51.100.77", "Mozilla/5.0 Android"),
        ("2001:db8::1", "curl/8"),
    ]:
        r = await client.get(
            "/api/_whoami",
            headers={"Authorization": f"Bearer {token}", "X-Sim-Client-IP": ip, "User-Agent": ua},
        )
        assert r.status_code == 200 and r.json()["user_public_id"] == public_id  # Wi-Fi -> mobile


async def test_no_secret_reaches_the_logs(
    client: httpx.AsyncClient, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)
    phone = fresh_phone()
    req = await _request(client, phone)
    v = await client.post(
        "/api/auth/otp/verify",
        json={"request_id": req["request_id"], "otp": req["dev_otp"], "device_id": DEVICE},
    )
    text = caplog.text
    assert phone not in text
    assert req["dev_otp"] not in text.replace(req["request_id"], "")
    assert v.json()["session_token"] not in text
