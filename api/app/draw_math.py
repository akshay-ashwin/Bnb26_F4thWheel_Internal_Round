"""The Fair Draw, as pure functions (no I/O). Byte-exact definitions: docs/contract/draw.md.

* seed          32 random bytes, transported as 64 lowercase hex characters
* seed_commit   hex(SHA-256(seed bytes))
* entry set     the REGISTERED entries at freeze time; entry_set_hash =
                hex(SHA-256(UTF-8("\\n".join(sorted(user_public_ids))))), no trailing newline
* rank key      hex(HMAC-SHA256(key = seed bytes, msg = UTF-8(drop_id + "|" + user_public_id)))
                with drop_id as the lowercase canonical UUID string
* ranking       ascending by rank key (rank 1 first); ties by user_public_id
Only the seed, the drop id and the frozen set of public ids go in: never arrival time, request
counts, IP or device.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
from collections.abc import Iterable
from dataclasses import dataclass

ALGORITHM = "HMAC_SHA256(seed, drop_id|user_public_id) asc; ties by user_public_id"


def new_seed() -> str:
    return secrets.token_bytes(32).hex()


def seed_commit(seed_hex: str) -> str:
    return hashlib.sha256(bytes.fromhex(seed_hex)).hexdigest()


def entry_set_hash(public_ids: Iterable[str]) -> str:
    return hashlib.sha256("\n".join(sorted(public_ids)).encode()).hexdigest()


def rank_key(seed_hex: str, drop_id: str, public_id: str) -> str:
    message = f"{drop_id}|{public_id}".encode()
    return hmac.new(bytes.fromhex(seed_hex), message, hashlib.sha256).hexdigest()


@dataclass(frozen=True)
class Ranked:
    rank: int
    entry_id: str
    public_id: str
    key: str


def rank_entries(seed_hex: str, drop_id: str, entries: Iterable[tuple[str, str]]) -> list[Ranked]:
    """`entries` are (entry_id, user_public_id). Returns them ranked 1..N."""
    keyed = sorted(
        (rank_key(seed_hex, drop_id, public_id), public_id, entry_id)
        for entry_id, public_id in entries
    )
    return [
        Ranked(i, entry_id, public_id, key) for i, (key, public_id, entry_id) in enumerate(keyed, 1)
    ]
