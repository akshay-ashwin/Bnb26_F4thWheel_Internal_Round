"""Pure security primitives: phone normalisation/hash, OTP hashing, session tokens, admission JWTs.

No I/O here, so everything is unit-testable. Secrets come from `Settings`.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import re
import secrets
import time
import uuid
from typing import Any

from app.errors import InvalidPhone

# --- phone ---

_PHONE_CHARS = re.compile(r"[\s\-().]")


def normalize_phone(raw: str) -> str:
    """Return the E.164 form of an Indian mobile number or raise INVALID_PHONE.

    Accepts `+91XXXXXXXXXX`, `91XXXXXXXXXX`, `0XXXXXXXXXX` and `XXXXXXXXXX` (mobile numbers start
    with 6-9). Spaces, dashes and brackets are ignored, so every spelling of one number yields the
    same value (and therefore the same phone_hash).
    """
    cleaned = _PHONE_CHARS.sub("", raw)
    if cleaned.startswith("+"):
        digits = cleaned[1:]
        if not digits.isdigit() or not digits.startswith("91") or len(digits) != 12:
            raise InvalidPhone("Enter a valid Indian mobile number")
        national = digits[2:]
    else:
        if not cleaned.isdigit():
            raise InvalidPhone("Enter a valid Indian mobile number")
        if len(cleaned) == 12 and cleaned.startswith("91"):
            national = cleaned[2:]
        elif len(cleaned) == 11 and cleaned.startswith("0"):
            national = cleaned[1:]
        elif len(cleaned) == 10:
            national = cleaned
        else:
            raise InvalidPhone("Enter a valid Indian mobile number")
    if not re.fullmatch(r"[6-9]\d{9}", national):
        raise InvalidPhone("Enter a valid Indian mobile number")
    return "+91" + national


def phone_hash(pepper: str, e164: str) -> str:
    """HMAC-SHA256(pepper, e164) hex. The raw number is never stored or logged."""
    return hmac.new(pepper.encode(), e164.encode(), hashlib.sha256).hexdigest()


def phone_prefix(e164: str, digits: int = 6) -> str:
    return e164[3 : 3 + digits]


# --- OTP ---


def new_otp() -> str:
    return f"{secrets.randbelow(1_000_000):06d}"


def otp_hash(secret: str, request_id: str, otp: str) -> str:
    return hmac.new(secret.encode(), f"{request_id}:{otp}".encode(), hashlib.sha256).hexdigest()


def new_request_id() -> str:
    return secrets.token_urlsafe(16)


def constant_time_equal(a: str, b: str) -> bool:
    return hmac.compare_digest(a.encode(), b.encode())


# --- public ids ---


def new_public_id() -> str:
    """16 random bytes, base32, lowercase, no padding (26 chars). This exact format is what the
    draw hashes (docs/contract/draw.md)."""
    return base64.b32encode(secrets.token_bytes(16)).decode().rstrip("=").lower()


# --- base64url ---


def b64e(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def b64d(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


# --- session token: <session_uuid>.<HMAC-SHA256(SESSION_SECRET, session_uuid) base64url> ---


def sign_session(secret: str, session_id: uuid.UUID) -> str:
    sid = str(session_id)
    mac = hmac.new(secret.encode(), sid.encode(), hashlib.sha256).digest()
    return f"{sid}.{b64e(mac)}"


def verify_session_token(secret: str, token: str) -> uuid.UUID | None:
    """The session id if the signature is valid, else None. No database access."""
    sid, _, sig = token.partition(".")
    if not sid or not sig:
        return None
    try:
        session_id = uuid.UUID(sid)
        given = b64d(sig)
    except (ValueError, TypeError):
        return None
    expected = hmac.new(secret.encode(), str(session_id).encode(), hashlib.sha256).digest()
    return session_id if hmac.compare_digest(expected, given) else None


def sid_hash(session_id: uuid.UUID | str) -> str:
    """First 128 bits of SHA-256 of the session id (hex): what admission tokens carry, never the raw
    session id. Truncated to keep the token under ~400 bytes; 128 bits is plenty for a binding."""
    return hashlib.sha256(str(session_id).encode()).hexdigest()[:32]


# --- admission token: compact JWS, HS256 only ---


class TokenInvalidError(Exception):
    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


_HEADER = b64e(json.dumps({"alg": "HS256", "typ": "JWT"}, separators=(",", ":")).encode())


def _sign(key: str, signing_input: str) -> str:
    return b64e(hmac.new(key.encode(), signing_input.encode(), hashlib.sha256).digest())


def encode_token(key: str, claims: dict[str, Any]) -> str:
    payload = b64e(json.dumps(claims, separators=(",", ":"), sort_keys=True).encode())
    signing_input = f"{_HEADER}.{payload}"
    return f"{signing_input}.{_sign(key, signing_input)}"


def decode_token(
    key: str, token: str, *, leeway_s: int = 2, now: float | None = None
) -> dict[str, Any]:
    """Verify signature and expiry and return the claims; otherwise TokenInvalidError(reason).

    The algorithm is pinned: anything but HS256 (including `none`) is rejected before any
    claim is read.
    """
    parts = token.split(".")
    if len(parts) != 3:
        raise TokenInvalidError("malformed")
    header_b64, payload_b64, sig = parts
    try:
        header = json.loads(b64d(header_b64))
        if not isinstance(header, dict) or header.get("alg") != "HS256":
            raise TokenInvalidError("forged")
        expected = _sign(key, f"{header_b64}.{payload_b64}")
        if not hmac.compare_digest(expected.encode(), sig.encode()):
            raise TokenInvalidError("forged")
        claims = json.loads(b64d(payload_b64))
    except TokenInvalidError:
        raise
    except Exception as exc:
        raise TokenInvalidError("malformed") from exc
    if not isinstance(claims, dict) or not all(
        k in claims for k in ("drop_id", "entry_id", "sid_hash", "jti", "iat", "exp", "run")
    ):
        raise TokenInvalidError("malformed")
    if (now if now is not None else time.time()) > float(claims["exp"]) + leeway_s:
        raise TokenInvalidError("expired")
    return claims
