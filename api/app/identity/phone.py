"""Phone normalisation and the peppered hash that makes one phone number one identity.

The raw number exists only inside `identify_phone` and the SMS call. What the rest of the system
sees is `phone_hash` = HMAC-SHA256(PHONE_PEPPER, E.164). The pepper lives only in the environment,
so a leaked database alone cannot be turned back into phone numbers by trying all ~10^9 Indian
mobile numbers (a plain SHA-256 could be).
"""

import hashlib
import hmac
from dataclasses import dataclass, field

import phonenumbers
from pydantic import SecretStr

from app.errors import InvalidPhone

DEFAULT_REGION = "IN"
_MOBILE = frozenset(
    {phonenumbers.PhoneNumberType.MOBILE, phonenumbers.PhoneNumberType.FIXED_LINE_OR_MOBILE}
)


@dataclass(frozen=True, slots=True)
class PhoneIdentity:
    # repr=False everywhere: a stray `log.info("%s", identity)` or a traceback must not print them.
    e164: str = field(repr=False)
    phone_hash: str = field(repr=False)
    prefix: str = field(repr=False)


def phone_hash(pepper: SecretStr, e164: str) -> str:
    return hmac.new(pepper.get_secret_value().encode(), e164.encode(), hashlib.sha256).hexdigest()


def identify_phone(raw: str, pepper: SecretStr, prefix_digits: int) -> PhoneIdentity:
    """Parse, require a valid mobile number, normalise to E.164 and hash. Else INVALID_PHONE."""
    try:
        parsed = phonenumbers.parse(raw, DEFAULT_REGION)
    except phonenumbers.NumberParseException:
        raise InvalidPhone() from None  # never chain: the message could carry the input
    if not phonenumbers.is_valid_number(parsed):
        raise InvalidPhone()
    if phonenumbers.number_type(parsed) not in _MOBILE:
        raise InvalidPhone()
    e164 = phonenumbers.format_number(parsed, phonenumbers.PhoneNumberFormat.E164)
    return PhoneIdentity(
        e164=e164,
        phone_hash=phone_hash(pepper, e164),
        prefix=str(parsed.national_number)[:prefix_digits],
    )
