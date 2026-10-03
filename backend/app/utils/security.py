import hashlib
import hmac
import re
import secrets

from app.config import get_settings

_PHONE_RE = re.compile(r"^\+?[0-9]{7,15}$")


def normalize_phone(phone: str) -> str | None:
    cleaned = re.sub(r"[\s\-()]", "", phone)
    return cleaned if _PHONE_RE.match(cleaned) else None


def keyed_hash(value: str) -> str:
    """HMAC-SHA256 with the server secret (phones, OTPs, session tokens)."""
    return hmac.new(get_settings().secret_key.encode(), value.encode(), hashlib.sha256).hexdigest()


def new_token() -> str:
    return secrets.token_urlsafe(32)


def new_otp() -> str:
    return f"{secrets.randbelow(1_000_000):06d}"


def new_public_id() -> str:
    return "u_" + secrets.token_hex(6)
