"""The two auth flows. Handlers in routers/public.py only translate HTTP to and from these.

Nothing here logs a phone number, a code or a token. Security events carry a short `phone_ref`
(first 8 hex chars of the phone hash) and the request id, never more.
"""

import hmac
import logging
import re
import time
from dataclasses import dataclass, field

import asyncpg

from app import db
from app.abuse import hooks
from app.cache import Cache
from app.config import Settings
from app.errors import OtpExpired, OtpInvalid, ServiceUnavailable, ValidationFailed
from app.identity import session as sess
from app.identity import users
from app.identity.client import ClientInfo
from app.identity.otp import Exhausted, OtpStore, valid_request_id
from app.identity.phone import identify_phone
from app.identity.sms import SmsProvider

log = logging.getLogger("fairdrop.auth")
_DEVICE_ID = re.compile(r"^[A-Za-z0-9._:-]{8,128}$")


def check_device_id(device_id: str) -> None:
    """8-128 characters from a safe set. Validated here (not in the schema) so the frozen
    OpenAPI contract does not change; the failure uses the standard validation envelope."""
    if not _DEVICE_ID.match(device_id):
        raise ValidationFailed(
            details={
                "fields": [
                    {
                        "field": "body.device_id",
                        "message": "must be 8-128 characters of letters, digits, . _ : -",
                    }
                ]
            }
        )


def make_store(cache: Cache, settings: Settings) -> OtpStore:
    return OtpStore(cache, sess.otp_key(settings), keep_plaintext=settings.sim_mode)


@dataclass(frozen=True, slots=True)
class OtpRequested:
    request_id: str
    expires_in_s: int
    dev_otp: str | None = field(repr=False)


async def request_otp(
    *,
    phone: str,
    device_id: str,
    client: ClientInfo,
    settings: Settings,
    store: OtpStore,
    sms: SmsProvider,
    request_id_for_log: str,
) -> OtpRequested:
    check_device_id(device_id)
    identity = identify_phone(phone, settings.phone_pepper, settings.phone_prefix_digits)
    # L6 hook (Plan 13): may raise OtpThrottled. Nothing is generated or sent before it.
    await hooks.otp_request_guard(
        phone_hash=identity.phone_hash,
        phone_prefix=identity.prefix,
        device_id=device_id,
        client_ip=client.ip,
    )
    created = await store.create_or_reuse(phone_hash=identity.phone_hash, device_id=device_id)
    if created.is_new and created.otp is not None:
        try:
            await sms.send_otp(
                phone_e164=identity.e164, phone_hash=identity.phone_hash, otp=created.otp
            )
        except Exception:
            await store.consume(created.request_id)  # nobody can use a code that was never sent
            log.warning(
                "otp send failed",
                extra={"phone_ref": identity.phone_hash[:8], "request_id": request_id_for_log},
            )
            raise ServiceUnavailable("Could not send the code", retry_after_ms=1000) from None
    return OtpRequested(
        request_id=created.request_id,
        expires_in_s=created.expires_in_s,
        dev_otp=created.otp if settings.sim_mode else None,
    )


@dataclass(frozen=True, slots=True)
class Verified:
    token: str = field(repr=False)
    user_public_id: str


async def verify_otp(
    *,
    request_id: str,
    otp: str,
    device_id: str,
    client: ClientInfo,
    settings: Settings,
    store: OtpStore,
    pool: asyncpg.Pool,
    cache: Cache,
    request_id_for_log: str,
) -> Verified:
    check_device_id(device_id)
    if not valid_request_id(request_id):
        raise OtpExpired()
    state = await store.attempt(request_id)
    if state is None:
        raise OtpExpired()  # expired and never-existed look the same
    if isinstance(state, Exhausted):
        log.warning("otp attempts exhausted", extra={"request_id": request_id_for_log})
        raise OtpInvalid()
    expected = store.hash_otp(request_id, state.phone_hash, otp)
    # `&` not `and`: both comparisons always run, so timing does not say which one failed.
    ok = hmac.compare_digest(expected.encode(), state.otp_hash.encode()) & hmac.compare_digest(
        device_id.encode(), state.device_id.encode()
    )
    if not ok:
        log.info(
            "otp rejected",
            extra={"phone_ref": state.phone_hash[:8], "request_id": request_id_for_log},
        )
        raise OtpInvalid()
    if not await store.consume(request_id):
        raise OtpExpired()  # a parallel verify consumed it first

    async with db.transaction(pool) as conn:
        user = await users.upsert_user(
            conn, phone_hash=state.phone_hash, device_id=device_id, client_ip=client.ip
        )
        expired = await users.revoke_expired_sessions(
            conn, settings, user_id=user.id, device_id=device_id
        )
        session_id, created_at = await users.get_or_create_session(
            conn,
            settings,
            user_id=user.id,
            device_id=device_id,
            client_ip=client.ip,
            ua_hash=client.ua_hash,
        )
    await sess.cache_drop(cache, expired)
    session = sess.Session(
        session_id=session_id,
        user_id=user.id,
        user_public_id=user.public_id,
        device_id=device_id,
        expires_at=sess.expiry_of(settings, created_at),
    )
    await sess.cache_put(cache, session)
    # L7 hook (Plan 13). Evidence only; it must not refuse a login.
    await hooks.on_identity_verified(
        user_id=user.id,
        device_id=device_id,
        client_ip=client.ip,
        ua_hash=client.ua_hash,
        verify_latency_ms=max(0, int(time.time() * 1000) - state.created_ms),
    )
    log.info(
        "identity verified",
        extra={"user_public_id": user.public_id, "request_id": request_id_for_log},
    )
    return Verified(token=sess.issue_token(settings, session_id), user_public_id=user.public_id)
