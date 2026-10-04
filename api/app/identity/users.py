"""Users and sessions in Postgres. Both writes are race-safe by constraint, not by locking.

* users.phone_hash is UNIQUE: `INSERT ... ON CONFLICT DO NOTHING`, then SELECT the winner's row.
  A concurrent loser waits for the winner's commit (unique-index wait), then reads it.
* sessions has a partial UNIQUE index on (user_id, device_id) WHERE revoked_at IS NULL
  (migration 20261004100400): the same trick, so one device has at most one live session.
"""

import base64
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime
from ipaddress import ip_address
from uuid import UUID

import asyncpg

from app.config import Settings
from app.errors import ServiceUnavailable
from app.identity import session as sess


def new_public_id() -> str:
    """16 random bytes, base32, lowercase, no padding (26 chars). The draw (Plan 09) hashes this
    exact string, so the format is part of the contract with the evaluator."""
    return base64.b32encode(secrets.token_bytes(16)).decode().lower().rstrip("=")


@dataclass(frozen=True, slots=True)
class UserRow:
    id: UUID
    public_id: str


async def upsert_user(
    conn: asyncpg.Connection, *, phone_hash: str, device_id: str, client_ip: str
) -> UserRow:
    row = await conn.fetchrow(
        "INSERT INTO users (public_id, phone_hash, first_device_id, first_ip)"
        " VALUES ($1, $2, $3, $4) ON CONFLICT (phone_hash) DO NOTHING RETURNING id, public_id",
        new_public_id(),
        phone_hash,
        device_id,
        ip_address(client_ip),
    )
    if row is None:
        row = await conn.fetchrow(
            "SELECT id, public_id FROM users WHERE phone_hash = $1", phone_hash
        )
    if row is None:  # users are never deleted, so this cannot happen; fail closed, not silently
        raise ServiceUnavailable(retry_after_ms=1000)
    return UserRow(id=row["id"], public_id=row["public_id"])


async def revoke_expired_sessions(
    conn: asyncpg.Connection, settings: Settings, *, user_id: UUID, device_id: str
) -> list[UUID]:
    """Revoke this device's live session if it is older than the TTL, so a re-verify after expiry
    gets a fresh session instead of the same token that would be rejected. Returns the ids so the
    caller can drop their cache entries."""
    cutoff = datetime.fromtimestamp(sess._now() - settings.session_ttl_s, UTC)
    rows = await conn.fetch(
        "UPDATE sessions SET revoked_at = now()"
        " WHERE user_id = $1 AND device_id = $2 AND revoked_at IS NULL AND created_at <= $3"
        " RETURNING id",
        user_id,
        device_id,
        cutoff,
    )
    return [r["id"] for r in rows]


async def get_or_create_session(
    conn: asyncpg.Connection,
    settings: Settings,
    *,
    user_id: UUID,
    device_id: str,
    client_ip: str,
    ua_hash: str,
) -> tuple[UUID, float]:
    """The live session for (user, device), or a new one. Returns (session_id, created_at epoch)."""
    for _ in range(3):
        row = await conn.fetchrow(
            "INSERT INTO sessions (user_id, device_id, ip, ua_hash) VALUES ($1, $2, $3, $4)"
            " ON CONFLICT (user_id, device_id) WHERE revoked_at IS NULL DO NOTHING"
            " RETURNING id, created_at",
            user_id,
            device_id,
            ip_address(client_ip),
            ua_hash,
        )
        if row is None:
            row = await conn.fetchrow(
                "SELECT id, created_at FROM sessions"
                " WHERE user_id = $1 AND device_id = $2 AND revoked_at IS NULL",
                user_id,
                device_id,
            )
        if row is not None:
            return row["id"], row["created_at"].timestamp()
    raise ServiceUnavailable(retry_after_ms=1000)  # revoked again and again between statements
