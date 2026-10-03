"""Idempotency keys backed by Postgres (`idempotency_records`, PK (user_id, key)).

Protocol (docs/implementation/plans/05): look up before any lock; replay a stored success when the
request hash matches, 422 when it does not; the record is written INSIDE the business transaction
so a committed result always has its record and a rolled-back attempt never does. Only 2xx
responses are stored. The stored payload has no `server_time`; replays add a fresh one.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass
from typing import Any

import asyncpg

from app.errors import AppError


@dataclass(frozen=True)
class Stored:
    status_code: int
    payload: dict[str, Any]


def request_hash(method: str, route: str, body: Any) -> str:
    """SHA-256 over method, route template and canonical JSON (sorted keys, no whitespace)."""
    canonical = json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(f"{method}\n{route}\n{canonical}".encode()).hexdigest()


async def lookup(
    conn: asyncpg.Pool | asyncpg.Connection, user_id: uuid.UUID, key: uuid.UUID, req_hash: str
) -> Stored | None:
    """The stored success for this key, None on a miss; IDEMPOTENCY_KEY_REUSED if the key was
    used for a different request."""
    row = await conn.fetchrow(
        "SELECT request_hash, status_code, response FROM idempotency_records"
        " WHERE user_id = $1 AND key = $2",
        user_id,
        key,
    )
    if row is None:
        return None
    if row["request_hash"] != req_hash:
        raise AppError(
            "IDEMPOTENCY_KEY_REUSED", "This Idempotency-Key was used for another request"
        )
    return Stored(row["status_code"], row["response"])


async def store(
    conn: asyncpg.Connection,
    *,
    user_id: uuid.UUID,
    key: uuid.UUID,
    drop_id: uuid.UUID,
    endpoint: str,
    req_hash: str,
    status_code: int,
    payload: dict[str, Any],
) -> None:
    """Write the record inside the caller's transaction; a concurrent duplicate is a no-op."""
    await conn.execute(
        "INSERT INTO idempotency_records"
        " (user_id, key, drop_id, endpoint, request_hash, response, status_code)"
        " VALUES ($1, $2, $3, $4, $5, $6, $7) ON CONFLICT (user_id, key) DO NOTHING",
        user_id,
        key,
        drop_id,
        endpoint,
        req_hash,
        payload,
        status_code,
    )
