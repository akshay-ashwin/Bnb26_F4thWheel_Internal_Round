"""One-time codes, kept in Redis only (invariant 3: Redis may lose them; the user asks again).

Keys:
  otp:req:{request_id}   hash {phone_hash, otp_hash, device_id, created_ms, attempts[, otp]}, 300 s
  otp:phone:{phone_hash} -> request_id, TTL 30 s (same phone within 30 s gets the same request_id)

Security properties and how they are enforced:
  * Only an HMAC of the code is stored. The HMAC key is derived from SESSION_SECRET with its own
    label, and the code is bound to request_id and phone_hash, so a stored hash cannot be replayed
    against another request. (`otp` plaintext is also kept when SIM_MODE is on, so a deduplicated
    request can still hand `dev_otp` to the simulator; never in any other mode.)
  * Guess limit: the attempt counter is incremented BEFORE the comparison, inside one Lua script,
    so parallel guesses cannot all slip under the limit. Guess number 6 is refused even when it is
    correct.
  * Single use: success calls a second Lua script whose result says whether THIS call consumed
    the request. Two parallel correct verifies: one gets 1, the other gets 0 and fails.
  * Comparison is hmac.compare_digest on the HMAC hex strings (done by the caller).
"""

import hashlib
import hmac
import re
import secrets
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any, cast

from redis.asyncio import Redis

from app.cache import Cache
from app.errors import ServiceUnavailable

OTP_TTL_S = 300
DEDUPE_TTL_S = 30
MAX_ATTEMPTS = 5
_REQ = "otp:req:"
_PHONE = "otp:phone:"
_REQUEST_ID = re.compile(r"^[A-Za-z0-9_-]{8,64}$")

# Shared by both scripts: delete a request and, only if it still points at this request, its
# phone dedupe key (so a dead request_id is never handed out again).
_RELEASE = """
local function release(req_key, phone_hash, request_id, phone_prefix)
  redis.call('DEL', req_key)
  if phone_hash then
    local dk = phone_prefix .. phone_hash
    if redis.call('GET', dk) == request_id then redis.call('DEL', dk) end
  end
end
"""
# KEYS[1] request key. ARGV: 1 max attempts, 2 request_id, 3 phone key prefix.
# Returns nil (no such request), {-1} (attempt limit exceeded; request deleted), or
# {attempts, phone_hash, otp_hash, device_id, created_ms}.
_ATTEMPT = (
    _RELEASE
    + """
if redis.call('EXISTS', KEYS[1]) == 0 then return nil end
local attempts = redis.call('HINCRBY', KEYS[1], 'attempts', 1)
local f = redis.call('HMGET', KEYS[1], 'phone_hash', 'otp_hash', 'device_id', 'created_ms')
if attempts > tonumber(ARGV[1]) then
  release(KEYS[1], f[1], ARGV[2], ARGV[3])
  return {-1}
end
return {attempts, f[1], f[2], f[3], f[4]}
"""
)
# KEYS[1] request key. ARGV: 1 request_id, 2 phone key prefix. Returns 1 for exactly one caller.
_CONSUME = (
    _RELEASE
    + """
local existed = redis.call('EXISTS', KEYS[1])
if existed == 1 then
  release(KEYS[1], redis.call('HGET', KEYS[1], 'phone_hash'), ARGV[1], ARGV[2])
end
return existed
"""
)


def valid_request_id(value: str) -> bool:
    return _REQUEST_ID.match(value) is not None


def _text(value: bytes | str | None) -> str | None:
    return value.decode() if isinstance(value, bytes) else value


@dataclass(frozen=True, slots=True)
class OtpCreated:
    request_id: str
    expires_in_s: int
    is_new: bool
    otp: str | None = field(repr=False)  # plaintext: a new request, or a SIM_MODE reuse


@dataclass(frozen=True, slots=True)
class OtpAttempt:
    phone_hash: str = field(repr=False)
    otp_hash: str = field(repr=False)
    device_id: str = field(repr=False)
    created_ms: int


class Exhausted:
    """The guess limit was hit; the request has been deleted."""


class OtpStore:
    def __init__(self, cache: Cache, otp_key: bytes, *, keep_plaintext: bool) -> None:
        self._cache = cache
        self._key = otp_key
        self._keep_plaintext = keep_plaintext

    def hash_otp(self, request_id: str, phone_hash: str, otp: str) -> str:
        message = f"{request_id}:{phone_hash}:{otp}".encode()
        return hmac.new(self._key, message, hashlib.sha256).hexdigest()

    @staticmethod
    def new_otp() -> str:
        return f"{secrets.randbelow(10**6):06d}"

    async def create_or_reuse(self, *, phone_hash: str, device_id: str) -> OtpCreated:
        """Create a request, or return the live one for this phone (SET NX decides the winner).

        The request hash is written BEFORE the dedupe key, so nobody can be handed a request_id
        whose hash does not exist yet."""

        async def op(r: Redis) -> OtpCreated:
            for _ in range(3):
                otp = self.new_otp()
                request_id = secrets.token_urlsafe(16)
                rkey = _REQ + request_id
                fields = {
                    "phone_hash": phone_hash,
                    "otp_hash": self.hash_otp(request_id, phone_hash, otp),
                    "device_id": device_id,
                    "created_ms": str(int(time.time() * 1000)),
                    "attempts": "0",
                }
                if self._keep_plaintext:
                    fields["otp"] = otp
                pipe = r.pipeline(transaction=True)
                pipe.hset(rkey, mapping=fields)  # type: ignore[arg-type]
                pipe.expire(rkey, OTP_TTL_S)
                await pipe.execute()
                if await r.set(_PHONE + phone_hash, request_id, nx=True, ex=DEDUPE_TTL_S):
                    return OtpCreated(request_id, OTP_TTL_S, True, otp)
                await r.delete(rkey)  # lost the race: our hash was never handed out
                existing = _text(await r.get(_PHONE + phone_hash))
                if existing is None:
                    continue  # the 30 s key expired between the two calls: try again
                ttl = int(await r.ttl(_REQ + existing))
                if ttl < 0:
                    continue  # that request just died
                plain = (
                    _text(await r.hget(_REQ + existing, "otp")) if self._keep_plaintext else None
                )
                return OtpCreated(existing, ttl, False, plain)
            raise ServiceUnavailable(retry_after_ms=1000)

        return await self._cache.run_required(op)

    async def attempt(self, request_id: str) -> OtpAttempt | Exhausted | None:
        """Count one guess and load the request. None: unknown or expired. Exhausted: the attempt
        limit was hit (the request is deleted). Otherwise the stored values to compare against."""

        async def op(r: Redis) -> Any:
            return await r.eval(_ATTEMPT, 1, _REQ + request_id, MAX_ATTEMPTS, request_id, _PHONE)

        raw = cast("list[Any] | None", await self._cache.run_required(op))
        if raw is None:
            return None
        if raw[0] == -1:
            return Exhausted()
        return OtpAttempt(
            phone_hash=str(raw[1]),
            otp_hash=str(raw[2]),
            device_id=str(raw[3]),
            created_ms=int(raw[4]),
        )

    async def consume(self, request_id: str) -> bool:
        """True for exactly one caller per request. Also used to drop an unsendable request."""
        return bool(await self._cache.run_required(self._consume_op(request_id)))

    def _consume_op(self, request_id: str) -> Callable[[Redis], Awaitable[int]]:
        async def op(r: Redis) -> int:
            result = await r.eval(_CONSUME, 1, _REQ + request_id, request_id, _PHONE)
            return int(result)

        return op
