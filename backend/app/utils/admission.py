"""Signed admission tokens: base64url(payload) "." base64url(HMAC-SHA256(key, base64url(payload))).

The payload binds drop, user public id, session id, jti and expiry. Single-use and "latest token only" are
enforced server-side via the admission_tokens table (jti + consumed_at); the signature only proves origin.
"""
import base64
import hashlib
import hmac
import json
import time

from app.config import get_settings


class TokenError(Exception):
    def __init__(self, code: str) -> None:
        self.code = code


def _key() -> bytes:
    return hmac.new(get_settings().secret_key.encode(), b"admission-token:v1", hashlib.sha256).digest()


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _unb64(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def sign_token(*, jti: str, drop_id: int, user_public_id: str, session_id: int, exp: int) -> str:
    payload = _b64(json.dumps({"v": 1, "jti": jti, "d": drop_id, "u": user_public_id, "s": session_id, "exp": exp},
                              separators=(",", ":"), sort_keys=True).encode())
    return payload + "." + _b64(hmac.new(_key(), payload.encode(), hashlib.sha256).digest())


def verify_token(token: str) -> dict:
    """Check signature, structure and expiry. Raises TokenError('ADMISSION_TOKEN_INVALID'|'..._EXPIRED')."""
    try:
        payload, _, sig = token.partition(".")
        if not payload or not sig:
            raise ValueError
        expected = hmac.new(_key(), payload.encode(), hashlib.sha256).digest()
        if not hmac.compare_digest(expected, _unb64(sig)):
            raise ValueError
        claims = json.loads(_unb64(payload))
        if claims.get("v") != 1 or not all(k in claims for k in ("jti", "d", "u", "s", "exp")):
            raise ValueError
    except Exception:
        raise TokenError("ADMISSION_TOKEN_INVALID") from None
    if time.time() >= claims["exp"]:
        raise TokenError("ADMISSION_TOKEN_EXPIRED")
    return claims
