"""Pure, DB-free Fair Draw primitives. Anyone can re-run these on the public data to verify a draw.

Canonical encodings (all text is UTF-8, no trailing newline):

* seed        : 64-char lowercase hex string (32 random bytes). Everywhere it is used as the UTF-8 bytes
                of that hex string, so `printf %s "$SEED" | shasum -a 256` reproduces the commitment.
* commitment  : SHA256(seed) as hex.
* entry set   : the sorted (ascending, byte order) *unique* user public ids of the frozen entries.
                canonical = "fairdrop:entryset:v1\\ndrop_id=<id>\\ncount=<n>\\n" + "\\n".join(public_ids)
                entry_set_hash = SHA256(canonical) as hex. Phone numbers are never part of it.
* score       : HMAC-SHA256(key=seed, msg="fairdrop:draw:v1|<drop_id>|<public_id>") as hex.
* ranking     : ascending by score (lowest digest = rank 1), ties (practically impossible) by public id.
                Ranks 1..total_seats are winners, the rest are waitlist.

Only the seed, drop id and the frozen set of public ids enter the computation: never time, order or counts
of requests.
"""
import hashlib
import hmac
import secrets
from dataclasses import dataclass
from typing import Iterable

ENTRY_SET_PREFIX = "fairdrop:entryset:v1"
DRAW_PREFIX = "fairdrop:draw:v1"


@dataclass(frozen=True)
class Ranked:
    rank: int
    user_public_id: str
    score: str
    is_winner: bool


def new_seed() -> str:
    return secrets.token_hex(32)


def seed_commitment(seed: str) -> str:
    return hashlib.sha256(seed.encode()).hexdigest()


def entry_set_hash(drop_id: int, public_ids: Iterable[str]) -> str:
    ids = sorted(set(public_ids))
    canonical = f"{ENTRY_SET_PREFIX}\ndrop_id={drop_id}\ncount={len(ids)}\n" + "\n".join(ids)
    return hashlib.sha256(canonical.encode()).hexdigest()


def score(seed: str, drop_id: int, public_id: str) -> str:
    return hmac.new(seed.encode(), f"{DRAW_PREFIX}|{drop_id}|{public_id}".encode(), hashlib.sha256).hexdigest()


def rank_entries(seed: str, drop_id: int, public_ids: Iterable[str], seats: int) -> list[Ranked]:
    scored = sorted((score(seed, drop_id, pid), pid) for pid in set(public_ids))
    return [Ranked(i, pid, sc, i <= seats) for i, (sc, pid) in enumerate(scored, start=1)]
