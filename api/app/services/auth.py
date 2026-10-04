"""OTP login and sessions.

Redis holds OTP requests (`otp:req:{id}` hash, 5 min) and a 30 s per-phone dedupe key. Postgres
holds users and sessions. If Redis is down, OTP login answers 503 (availability, never integrity).
"""

from __future__ import annotations

import ipaddress
import logging
import re
import time
import uuid
from dataclasses import dataclass
from typing import Any, Protocol, cast

import asyncpg
import redis.asyncio as aioredis

from app import abuse
from app.cache import Cache, RedisUnavailableError
from app.config import Settings
from app.errors import AppError, OtpExpired, OtpInvalid, ServiceUnavailable, ValidationFailed
from app.security import (
    constant_time_equal,
    new_otp,
    new_public_id,
    new_request_id,
    normalize_phone,
    otp_hash,
    phone_hash,
    phone_prefix,
    sign_session,
)

log = logging.getLogger("fairdrop.auth")

_DEVICE_ID = re.compile(r"^[A-Za-z0-9._:\-]{8,128}$")


def _check_device(device_id: str) -> None:
    if not _DEVICE_ID.match(device_id):
        raise ValidationFailed("device_id must be 8-128 characters of A-Z a-z 0-9 . _ : -")


class SmsProvider(Protocol):
    async def send_otp(self, phone_hash: str, otp: str) -> None: ...


class SimulatedSmsProvider:
    """Hackathon provider: nothing is sent. Never logs the OTP or the phone."""

    async def send_otp(self, phone_hash: str, otp: str) -> None:
        log.info("OTP sent to phone_hash=%s...", phone_hash[:8])


def _unavailable() -> AppError:
    return ServiceUnavailable("Sign-in is temporarily unavailable", retry_after_ms=1000)


@dataclass(frozen=True)
class OtpRequested:
    request_id: str
    expires_in_s: int
    dev_otp: str | None


@dataclass(frozen=True)
class Verified:
    session_token: str
    user_public_id: str


def _ip(value: str) -> ipaddress.IPv4Address | ipaddress.IPv6Address | None:
    try:
        return ipaddress.ip_address(value)
    except ValueError:
        return None


async def request_otp(
    settings: Settings,
    cache: Cache,
    sms: SmsProvider,
    *,
    phone: str,
    device_id: str,
    ip: str,
) -> OtpRequested:
    _check_device(device_id)
    e164 = normalize_phone(phone)
    p_hash = phone_hash(settings.phone_pepper.get_secret_value(), e164)
    await abuse.otp_request_guard(
        phone_hash=p_hash, phone_prefix=phone_prefix(e164), device_id=device_id, client_ip=ip
    )
    dedupe_key = f"otp:phone:{p_hash}"
    candidate = new_request_id()
    otp = new_otp()
    req_key = f"otp:req:{candidate}"
    fields: dict[Any, Any] = {
        "phone_hash": p_hash,
        "otp_hash": otp_hash(settings.session_secret.get_secret_value(), candidate, otp),
        "device_id": device_id,
        "attempts": "0",
        "created_ms": str(int(time.time() * 1000)),
    }
    if settings.sim_mode:
        fields["dev_otp"] = otp  # development only; never stored when SIM_MODE is off

    async def op(r: aioredis.Redis) -> tuple[str | None, bool]:
        # Write the request first, then claim the dedupe slot: a concurrent loser can always
        # read the winner's request.
        pipe = r.pipeline(transaction=False)
        pipe.hset(req_key, mapping=fields)
        pipe.expire(req_key, settings.otp_ttl_s)
        await pipe.execute()
        won = await r.set(dedupe_key, candidate, nx=True, ex=settings.otp_dedupe_s)
        if won:
            return candidate, True
        existing = str(await r.get(dedupe_key) or "")
        if existing and await r.exists(f"otp:req:{existing}"):
            await r.delete(req_key)
            return existing, False
        # The dedupe key outlived its request (verified or expired): replace it.
        await r.set(dedupe_key, candidate, ex=settings.otp_dedupe_s)
        return candidate, True

    try:
        request_id, created = await cache.run(op)
        if request_id is None:
            raise _unavailable()
        if created:
            await sms.send_otp(p_hash, otp)
            dev = otp if settings.sim_mode else None
        else:
            stored = cast(
                "str | None", await cache.run(lambda r: r.hget(f"otp:req:{request_id}", "dev_otp"))
            )
            dev = stored if settings.sim_mode else None
    except RedisUnavailableError:
        raise _unavailable() from None
    return OtpRequested(request_id, settings.otp_ttl_s, dev)


async def verify_otp(
    settings: Settings,
    cache: Cache,
    pool: asyncpg.Pool,
    *,
    request_id: str,
    otp: str,
    device_id: str,
    ip: str,
    ua_hash: str,
) -> Verified:
    _check_device(device_id)
    req_key = f"otp:req:{request_id}"
    try:
        data = cast("dict[str, str]", await cache.run(lambda r: r.hgetall(req_key)))
        if not data:
            raise OtpExpired("This code has expired. Request a new one.")
        attempts = int(data.get("attempts", "0"))
        if attempts >= settings.otp_max_attempts:
            await cache.run(lambda r: r.delete(req_key))
            raise OtpInvalid("Too many wrong attempts")
        ok = constant_time_equal(
            data["otp_hash"], otp_hash(settings.session_secret.get_secret_value(), request_id, otp)
        ) and constant_time_equal(data["device_id"], device_id)
        if not ok:
            await cache.run(lambda r: r.hincrby(req_key, "attempts", 1))
            raise OtpInvalid("That code is not correct")
        # Single use, race-free: only the request that actually deletes the key proceeds.
        if await cache.run(lambda r: r.delete(req_key)) != 1:
            raise OtpExpired("This code has expired. Request a new one.")
    except RedisUnavailableError:
        raise _unavailable() from None

    created_ms = int(data.get("created_ms", "0"))
    verify_latency_ms = int(time.time() * 1000) - created_ms if created_ms else None
    ip_obj = _ip(ip)
    async with pool.acquire(timeout=settings.pool_acquire_timeout_s) as conn:
        row = await conn.fetchrow(
            "INSERT INTO users (public_id, phone_hash, first_device_id, first_ip)"
            " VALUES ($1, $2, $3, $4) ON CONFLICT (phone_hash) DO NOTHING"
            " RETURNING id, public_id",
            new_public_id(),
            data["phone_hash"],
            device_id,
            ip_obj,
        )
        if row is None:
            row = await conn.fetchrow(
                "SELECT id, public_id FROM users WHERE phone_hash = $1", data["phone_hash"]
            )
        if row is None:
            raise RuntimeError("user upsert returned no row")
        user_id: uuid.UUID = row["id"]
        existing = await conn.fetchval(
            "SELECT id FROM sessions WHERE user_id = $1 AND device_id = $2"
            " AND revoked_at IS NULL AND created_at > now() - make_interval(secs => $3)"
            " ORDER BY created_at DESC LIMIT 1",
            user_id,
            device_id,
            float(settings.session_ttl_s),
        )
        session_id: uuid.UUID = existing or await conn.fetchval(
            "INSERT INTO sessions (user_id, device_id, ip, ua_hash) VALUES ($1, $2, $3, $4)"
            " RETURNING id",
            user_id,
            device_id,
            ip_obj,
            ua_hash,
        )
    await abuse.on_identity_verified(
        user_id=str(user_id),
        device_id=device_id,
        client_ip=ip,
        ua_hash=ua_hash,
        verify_latency_ms=verify_latency_ms,
    )
    return Verified(
        sign_session(settings.session_secret.get_secret_value(), session_id), row["public_id"]
    )
