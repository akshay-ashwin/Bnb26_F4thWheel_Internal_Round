"""Step-up (L8): a flagged winner proves they hold the phone with a fresh OTP before the seat is
confirmed. Nobody is blocked: the seat is held for the rest of the offer window; a pass moves the
entry STEP_UP_REQUIRED -> OFFERED, three wrong codes (or the window ending) release the seat to the
waitlist.

The OTP lives in Redis (`stepup:{entry_id}`, TTL = remaining offer time). The server keeps only
`phone_hash`, so the SMS provider is keyed by it (SIM_MODE exposes `dev_otp` in `/me`). OTP
verification happens BEFORE any database lock is taken (no network calls while holding locks).
"""

from __future__ import annotations

import logging
import time
import uuid
from datetime import datetime
from typing import Any, cast

import asyncpg

from app.cache import Cache, RedisUnavailableError
from app.config import Settings
from app.db import transaction
from app.deps import Session
from app.errors import NotOffered, OfferExpired, OtpInvalid, ServiceUnavailable
from app.metrics import Metrics
from app.security import constant_time_equal, new_otp, otp_hash
from app.services import idempotency
from app.services.auth import SmsProvider
from app.services.entries import Result

log = logging.getLogger("fairdrop.stepup")


def _key(entry_id: uuid.UUID | str) -> str:
    return f"stepup:{entry_id}"


async def ensure_challenge(
    pool: asyncpg.Pool,
    cache: Cache,
    settings: Settings,
    sms: SmsProvider,
    metrics: Metrics,
    *,
    entry_id: uuid.UUID,
    user_id: uuid.UUID,
    offer_expires_at: datetime,
) -> str | None:
    """Make sure a step-up OTP exists for this entry; returns it only in SIM_MODE. Best effort:
    a Redis problem must not break `/me`."""
    key = _key(entry_id)
    ttl = max(1, int(offer_expires_at.timestamp() - time.time()))
    otp = new_otp()
    try:
        created = await cache.run(
            lambda r: r.hsetnx(
                key,
                "otp_hash",
                otp_hash(settings.session_secret.get_secret_value(), str(entry_id), otp),
            )
        )
        if created:
            fields: dict[Any, Any] = {"attempts": "0"}
            if settings.sim_mode:
                fields["dev_otp"] = otp
            await cache.run(lambda r: r.hset(key, mapping=fields))
            await cache.run(lambda r: r.expire(key, ttl))
            p_hash = await pool.fetchval("SELECT phone_hash FROM users WHERE id = $1", user_id)
            await sms.send_otp(p_hash, otp)
            metrics.incr("step_up_issued")
            return otp if settings.sim_mode else None
        stored = cast("str | None", await cache.run(lambda r: r.hget(key, "dev_otp")))
        return stored if settings.sim_mode else None
    except RedisUnavailableError:
        return None


async def verify_step_up(
    pool: asyncpg.Pool,
    cache: Cache,
    settings: Settings,
    sms: SmsProvider,
    metrics: Metrics,
    session: Session,
    drop_id: uuid.UUID,
    *,
    otp: str,
    key: uuid.UUID,
) -> Result:
    req_hash = idempotency.request_hash(
        "POST", "/api/drops/{id}/step-up", {"drop_id": str(drop_id), "otp": otp}
    )
    stored = await idempotency.lookup(pool, session.user_id, key, req_hash)
    if stored is not None:
        metrics.incr("duplicate")
        return Result(stored.status_code, stored.payload)

    entry = await pool.fetchrow(
        "SELECT id, status, offer_expires_at, step_up_passed_at, (offer_expires_at < now()) AS over"
        " FROM entries WHERE drop_id = $1 AND user_id = $2",
        drop_id,
        session.user_id,
    )
    if entry is None:
        raise NotOffered("There is no offer for this entry")
    status: str = entry["status"]
    if status in ("ALLOCATED", "OFFERED") and (status == "ALLOCATED" or entry["step_up_passed_at"]):
        return Result(
            200, {"status": "OFFERED"}
        )  # already done: idempotent success (contract: OFFERED)
    if status == "OFFER_EXPIRED":
        raise OfferExpired("Your offer has expired")
    if status != "STEP_UP_REQUIRED":
        raise NotOffered("No step-up is needed for this entry")
    if entry["over"]:
        await _expire(pool, entry["id"])
        raise OfferExpired("Your offer has expired")

    entry_id: uuid.UUID = entry["id"]
    ckey = _key(entry_id)
    try:
        data = cast("dict[str, str]", await cache.run(lambda r: r.hgetall(ckey)))
        if not data or "otp_hash" not in data:
            # never issued (or lost): issue now; the caller must read the new code and retry
            await ensure_challenge(
                pool,
                cache,
                settings,
                sms,
                metrics,
                entry_id=entry_id,
                user_id=session.user_id,
                offer_expires_at=entry["offer_expires_at"],
            )
            raise OtpInvalid("A new code was sent")
        attempts = int(data.get("attempts", "0"))
        good = constant_time_equal(
            data["otp_hash"],
            otp_hash(settings.session_secret.get_secret_value(), str(entry_id), otp),
        )
        if not good:
            attempts = await cache.run(lambda r: r.hincrby(ckey, "attempts", 1))
            metrics.incr("step_up_failed")
            if attempts >= settings.step_up_max_attempts:
                await _expire(pool, entry_id)  # three strikes: the seat goes to the waitlist
                await cache.run(lambda r: r.delete(ckey))
                raise OtpInvalid("Too many wrong codes; your offer was released")
            raise OtpInvalid("That code is not correct")
        if attempts >= settings.step_up_max_attempts:
            raise OtpInvalid("Too many wrong codes")
    except RedisUnavailableError:
        raise ServiceUnavailable(
            "Verification is temporarily unavailable", retry_after_ms=1000
        ) from None

    payload: dict[str, Any]
    async with transaction(pool, lock_timeout_ms=1000, statement_timeout_ms=2000) as conn:
        moved = await conn.execute(
            "UPDATE entries SET status = 'OFFERED', step_up_passed_at = now(),"
            " status_changed_at = now()"
            " WHERE id = $1 AND status = 'STEP_UP_REQUIRED' AND offer_expires_at >= now()",
            entry_id,
        )
        if moved == "UPDATE 1":
            payload = {"status": "OFFERED"}
        else:
            now_status = await conn.fetchval("SELECT status FROM entries WHERE id = $1", entry_id)
            if now_status in ("OFFERED", "ALLOCATED"):
                payload = {"status": "OFFERED"}  # a concurrent retry won
            elif now_status == "OFFER_EXPIRED" or now_status == "STEP_UP_REQUIRED":
                if now_status == "STEP_UP_REQUIRED":
                    await conn.execute(
                        "UPDATE entries SET status = 'OFFER_EXPIRED', status_changed_at = now()"
                        " WHERE id = $1 AND status = 'STEP_UP_REQUIRED'",
                        entry_id,
                    )
                raise OfferExpired("Your offer has expired")
            else:
                raise NotOffered("No step-up is needed for this entry")
        await idempotency.store(
            conn,
            user_id=session.user_id,
            key=key,
            drop_id=drop_id,
            endpoint="step-up",
            req_hash=req_hash,
            status_code=200,
            payload=payload,
        )
    try:
        await cache.run(lambda r: r.delete(ckey))
    except RedisUnavailableError:
        pass
    metrics.incr("step_up_passed")
    return Result(200, payload)


async def _expire(pool: asyncpg.Pool, entry_id: uuid.UUID) -> None:
    await pool.execute(
        "UPDATE entries SET status = 'OFFER_EXPIRED', status_changed_at = now()"
        " WHERE id = $1 AND status = 'STEP_UP_REQUIRED'",
        entry_id,
    )
