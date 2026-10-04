"""Registration (one entry per verified identity) and the `/me` status read model.

Fairness note: `entered_at` is stored as evidence only. Fair-mode decision code (the draw) never
reads it, request counts, IP or device (docs/contract/draw.md).
"""

from __future__ import annotations

import ipaddress
import logging
import random
import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any

import asyncpg

from app import abuse
from app.cache import Cache, RedisUnavailableError
from app.config import Settings
from app.db import transaction
from app.deps import Session
from app.errors import NotFound, WindowClosed, WindowNotOpen
from app.metrics import Metrics
from app.schemas.me import MeAllocation, MeEntry, MeOut
from app.services import idempotency
from app.services.drops import effective_phase

log = logging.getLogger("fairdrop.entries")

INSERT_ENTRY = """
INSERT INTO entries (drop_id, user_id, status, client_ip, device_id, risk_score, risk_flags, run_no)
SELECT d.id, $2, 'REGISTERED', $3, $4, $5, $6, d.run_no
FROM drops d
WHERE d.id = $1 AND d.phase = 'OPEN' AND (d.mode = 'fifo' OR now() < d.reg_closes_at)
ON CONFLICT (drop_id, user_id) DO NOTHING
RETURNING id, status
"""


@dataclass(frozen=True)
class Result:
    status_code: int
    payload: dict[str, Any]


def _ip(value: str) -> ipaddress.IPv4Address | ipaddress.IPv6Address | None:
    try:
        return ipaddress.ip_address(value)
    except ValueError:
        return None


async def register_entry(
    pool: asyncpg.Pool,
    settings: Settings,
    metrics: Metrics,
    session: Session,
    drop_id: uuid.UUID,
    *,
    ip: str,
    ua_hash: str,
    key: uuid.UUID | None,
    req_hash: str,
) -> Result:
    """Create the user's entry, or return the existing one.

    The window is enforced INSIDE the insert (phase OPEN and, for Fair drops, database-clock
    `now() < reg_closes_at`), so there is no race between a pre-check and the close. Uniqueness is
    `UNIQUE(drop_id, user_id)`; ten concurrent requests leave exactly one row.
    """
    if key is not None:
        stored = await idempotency.lookup(pool, session.user_id, key, req_hash)
        if stored is not None:
            metrics.incr("duplicate")
            return Result(stored.status_code, stored.payload)

    try:
        score, flags = await abuse.score_entry(
            drop_id=str(drop_id),
            user_id=str(session.user_id),
            device_id=session.device_id,
            client_ip=ip,
            ua_hash=ua_hash,
        )
    except Exception:
        log.exception("risk scoring failed; entry proceeds unscored")
        score, flags = 0, ["risk_unavailable"]

    async with transaction(pool, acquire_timeout_s=settings.pool_acquire_timeout_s) as conn:
        row = await conn.fetchrow(
            INSERT_ENTRY, drop_id, session.user_id, _ip(ip), session.device_id, score, flags
        )
        if row is not None:
            status_code, payload = 201, {"entry_id": str(row["id"]), "status": row["status"]}
            metrics.incr("accepted")
        else:
            existing = await conn.fetchrow(
                "SELECT id, status FROM entries WHERE drop_id = $1 AND user_id = $2",
                drop_id,
                session.user_id,
            )
            if existing is not None:
                status_code = 200
                # Contract: a repeat returns the same body as the 201. `/me` carries later states.
                payload = {"entry_id": str(existing["id"]), "status": "REGISTERED"}
                metrics.incr("duplicate")
            else:
                drop = await conn.fetchrow(
                    "SELECT phase, mode, reg_closes_at FROM drops WHERE id = $1", drop_id
                )
                if drop is None:
                    raise NotFound("Drop not found")
                if drop["phase"] == "SCHEDULED":
                    raise WindowNotOpen("Registration has not opened yet")
                raise WindowClosed("Registration is closed")
        if key is not None:
            await idempotency.store(
                conn,
                user_id=session.user_id,
                key=key,
                drop_id=drop_id,
                endpoint="entries",
                req_hash=req_hash,
                status_code=status_code,
                payload=payload,
            )
    return Result(status_code, payload)


# --- /me ---

BASE_POLL_MS = {
    "SCHEDULED": 5000,
    "OPEN_NO_ENTRY": 3000,
    "OPEN_REGISTERED": 4000,
    "CLOSED": 1500,
    "OFFERED": 1000,
    "STEP_UP_REQUIRED": 1000,
    "WAITLISTED": 2000,
    "TERMINAL": 15000,
    "FIFO_OPEN": 1000,
}
TERMINAL_STATUSES = ("ALLOCATED", "NOT_SELECTED", "OFFER_EXPIRED", "DISQUALIFIED")


def poll_after_ms(
    phase: str, mode: str, status: str | None, *, redis_down: bool, rng: random.Random | None = None
) -> int:
    """Server-paced polling (docs/contract/polling.md): base by situation, x2 if Redis is down,
    +/-10% jitter so 50,000 clients do not synchronise, clamped to 500..30000 ms."""
    if status in TERMINAL_STATUSES or phase == "DONE":
        base = BASE_POLL_MS["TERMINAL"]
    elif status in ("OFFERED", "STEP_UP_REQUIRED", "WAITLISTED"):
        base = BASE_POLL_MS[status]
    elif phase == "SCHEDULED":
        base = BASE_POLL_MS["SCHEDULED"]
    elif phase in ("CLOSED", "DRAWN"):
        base = BASE_POLL_MS["CLOSED"]
    elif phase == "OPEN" and mode == "fifo":
        base = BASE_POLL_MS["FIFO_OPEN"]
    elif phase == "OPEN":
        base = BASE_POLL_MS["OPEN_REGISTERED" if status == "REGISTERED" else "OPEN_NO_ENTRY"]
    else:
        base = BASE_POLL_MS["WAITLISTED"]
    if redis_down:
        base *= 2
    jitter = (rng or random).uniform(0.9, 1.1)
    return max(500, min(30000, int(base * jitter)))


ME_QUERY = """
SELECT d.id AS drop_id, d.phase, d.mode, d.reg_closes_at, d.run_no, d.capacity, d.claim_window_s,
       e.id AS entry_id, e.status, e.draw_rank, e.offer_expires_at,
       a.id AS allocation_id, a.created_at AS confirmed_at, s.seat_no
FROM drops d
LEFT JOIN entries e ON e.drop_id = d.id AND e.user_id = $2
LEFT JOIN allocations a ON a.entry_id = e.id
LEFT JOIN seats s ON s.id = a.seat_id
WHERE d.id = $1
"""


async def _offer_head(pool: asyncpg.Pool, cache: Cache, drop_id: uuid.UUID) -> int:
    """Highest draw rank that has been offered so far (display-only; Redis with SQL fallback)."""
    key = f"drop:{drop_id}:offer_head"
    try:
        cached = await cache.run(lambda r: r.get(key))
        if cached is not None:
            return int(cached)
    except RedisUnavailableError:
        pass
    head: int = await pool.fetchval(
        "SELECT COALESCE(max(draw_rank), 0) FROM entries"
        " WHERE drop_id = $1 AND draw_rank IS NOT NULL AND status <> 'WAITLISTED'",
        drop_id,
    )
    try:
        await cache.run(lambda r: r.set(key, head, ex=5))
    except RedisUnavailableError:
        pass
    return head


async def build_me(
    pool: asyncpg.Pool,
    cache: Cache,
    settings: Settings,
    session: Session,
    drop_id: uuid.UUID,
    *,
    mint_token: Any = None,
    dev_otp_for: Any = None,
) -> MeOut:
    row = await pool.fetchrow(ME_QUERY, drop_id, session.user_id)
    if row is None:
        raise NotFound("Drop not found")
    phase = effective_phase(row)
    status: str | None = row["status"]
    entry: MeEntry | None = None
    allocation: MeAllocation | None = None
    if row["entry_id"] is not None and status is not None:
        waitlist_pos: int | None = None
        if status == "WAITLISTED" and row["draw_rank"] is not None:
            head = await _offer_head(pool, cache, drop_id)
            waitlist_pos = max(1, row["draw_rank"] - head)
        offer_expires = (
            row["offer_expires_at"] if status in ("OFFERED", "STEP_UP_REQUIRED") else None
        )
        entry = MeEntry(
            entry_id=row["entry_id"],
            status=status,  # type: ignore[arg-type]
            rank=row["draw_rank"],
            waitlist_pos=waitlist_pos,
            offer_expires_at=offer_expires if isinstance(offer_expires, datetime) else None,
            step_up_required=status == "STEP_UP_REQUIRED",
            admission_token=mint_token(row, session) if mint_token else None,
            dev_otp=await dev_otp_for(row, session) if dev_otp_for else None,
        )
        if row["allocation_id"] is not None:
            allocation = MeAllocation(
                allocation_id=row["allocation_id"],
                seat_no=row["seat_no"],
                confirmed_at=row["confirmed_at"],
            )
    return MeOut(
        phase=phase,  # type: ignore[arg-type]
        entry=entry,
        allocation=allocation,
        poll_after_ms=poll_after_ms(phase, row["mode"], status, redis_down=not cache.available),
    )
