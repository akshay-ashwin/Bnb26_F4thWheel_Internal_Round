"""Hook points that the abuse layers (Plan 13) fill in. Plan 04 calls them; they do nothing yet.

The signatures are FINAL: Plan 13 replaces only the bodies. Keyword-only so a call site can never
swap two strings by position.

Rules for implementations (Plan 13):
  * Neither hook may use the raw phone number. `phone_hash` is the keyed hash, `phone_prefix` is
    the first PHONE_PREFIX_DIGITS digits of the national number (per request, never stored).
  * They produce evidence or a throttle. They never decide who wins a draw (invariant 5).
"""

from uuid import UUID


async def otp_request_guard(
    *, phone_hash: str, phone_prefix: str, device_id: str, client_ip: str
) -> None:
    """L6: called by POST /auth/otp/request after the phone is validated and hashed, before any
    OTP is generated or sent. Plan 13 raises `OtpThrottled(retry_after_ms=...)` (429
    OTP_THROTTLED) to refuse. Returns None to allow."""


async def on_identity_verified(
    *,
    user_id: UUID,
    device_id: str,
    client_ip: str,
    ua_hash: str,
    verify_latency_ms: int,
) -> None:
    """L7: called by POST /auth/otp/verify after the OTP is consumed and the user and session
    exist, before the response is built. `verify_latency_ms` is the time from the OTP request to
    this verify (a human needs seconds; a script needs milliseconds). Evidence only: this hook
    must never raise to refuse a login."""
