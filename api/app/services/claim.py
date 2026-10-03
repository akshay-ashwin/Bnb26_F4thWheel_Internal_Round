"""The claim: turn a held entry into a seat, atomically.

Lock order is ALWAYS entry row first, then a seat (never the reverse), so claims cannot deadlock:
different users never contend on an entry, and on seats they skip each other with
`FOR UPDATE SKIP LOCKED`. Everything that matters happens in ONE READ COMMITTED transaction:
seat update, entry update, ledger insert and idempotency record. Database constraints (one seat
per entry, one ledger row per entry and seat, the seat cap) are the last line of defence.
"""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from dataclasses import dataclass
from typing import Any

import asyncpg

from app.config import Settings
from app.db import transaction
from app.deps import Session
from app.errors import AppError
from app.metrics import Metrics
from app.security import TokenInvalidError, decode_token
from app.services import idempotency

log = logging.getLogger("fairdrop.claim")

TAKE_SEAT = """
UPDATE seats SET status = 'sold', entry_id = $1, sold_at = now()
WHERE status = 'free' AND id = (
    SELECT id FROM seats WHERE drop_id = $2 AND status = 'free'
    ORDER BY seat_no FOR UPDATE SKIP LOCKED LIMIT 1)
RETURNING id, seat_no
"""


@dataclass(frozen=True)
class Result:
    status_code: int
    payload: dict[str, Any]


def _payload(allocation_id: uuid.UUID, seat_no: int, confirmed_at: Any) -> dict[str, Any]:
    return {
        "allocation_id": str(allocation_id),
        "seat_no": seat_no,
        "confirmed_at": confirmed_at.isoformat(),
    }


def verify_claim_token(
    settings: Settings, metrics: Metrics, session: Session, drop_id: uuid.UUID, token: str
) -> dict[str, Any]:
    """Checks that need no database: signature, expiry, drop and session binding."""
    try:
        claims = decode_token(settings.token_signing_key, token)
        if claims["drop_id"] != str(drop_id):
            raise TokenInvalidError("wrong_drop")
        if claims["sid_hash"] != session.sid_hash:
            raise TokenInvalidError("session_mismatch")
        uuid.UUID(claims["entry_id"])
    except (TokenInvalidError, ValueError, TypeError) as exc:
        reason = exc.reason if isinstance(exc, TokenInvalidError) else "malformed"
        log.info("token rejected user=%s reason=%s", session.user_public_id, reason)
        metrics.incr("token_rejected")
        raise AppError("TOKEN_INVALID", "This admission token is not valid") from None
    return claims


async def claim(
    pool: asyncpg.Pool,
    settings: Settings,
    metrics: Metrics,
    session: Session,
    drop_id: uuid.UUID,
    *,
    token: str,
    key: uuid.UUID,
    tasks: set[asyncio.Task[Any]],
) -> Result:
    started = time.perf_counter()
    claims = verify_claim_token(settings, metrics, session, drop_id, token)
    entry_id = uuid.UUID(claims["entry_id"])
    # The hash covers the meaning of the request, not the token string: /me mints a new token on
    # every call, so a legitimate retry may carry a different one.
    req_hash = idempotency.request_hash(
        "POST", "/api/drops/{id}/claim", {"drop_id": str(drop_id), "entry_id": str(entry_id)}
    )
    stored = await idempotency.lookup(pool, session.user_id, key, req_hash)
    if stored is not None:
        metrics.incr("duplicate")
        return Result(stored.status_code, stored.payload)

    expired = False
    async with transaction(
        pool,
        lock_timeout_ms=settings.claim_lock_timeout_ms,
        statement_timeout_ms=settings.claim_statement_timeout_ms,
        acquire_timeout_s=settings.pool_acquire_timeout_s,
    ) as conn:
        drop = await conn.fetchrow("SELECT phase, mode, run_no FROM drops WHERE id = $1", drop_id)
        if drop is None:
            raise AppError("NOT_FOUND", "Drop not found")
        if claims["run"] != drop["run_no"]:  # a token from before a reset
            metrics.incr("token_rejected")
            raise AppError("TOKEN_INVALID", "This admission token is not valid")
        entry = await conn.fetchrow(
            "SELECT status, offer_expires_at, (offer_expires_at < now()) AS offer_over"
            " FROM entries WHERE id = $1 AND user_id = $2 AND drop_id = $3 FOR UPDATE",
            entry_id,
            session.user_id,
            drop_id,
        )
        if entry is None:
            raise AppError("NOT_OFFERED", "There is no offer for this entry")
        status = entry["status"]

        if (
            status == "ALLOCATED"
        ):  # a retry, a second tab, or a replayed token: same seat, no new one
            row = await conn.fetchrow(
                "SELECT a.id, a.created_at, s.seat_no FROM allocations a"
                " JOIN seats s ON s.id = a.seat_id WHERE a.entry_id = $1",
                entry_id,
            )
            if row is None:
                raise RuntimeError("ALLOCATED entry without a ledger row")
            payload = _payload(row["id"], row["seat_no"], row["created_at"])
            await idempotency.store(
                conn,
                user_id=session.user_id,
                key=key,
                drop_id=drop_id,
                endpoint="claim",
                req_hash=req_hash,
                status_code=200,
                payload=payload,
            )
            metrics.incr("duplicate")
            return Result(200, payload)
        if status == "STEP_UP_REQUIRED":
            raise AppError("STEP_UP_REQUIRED", "Verify with the code we sent first")

        if drop["mode"] == "fifo":
            if drop["phase"] == "DONE":
                raise AppError("SOLD_OUT", "All seats are taken")
            if drop["phase"] != "OPEN" or status != "REGISTERED":
                raise AppError("NOT_OFFERED", "There is no offer for this entry")
        elif status == "OFFER_EXPIRED" or (drop["phase"] == "DONE" and status == "OFFERED"):
            raise AppError("OFFER_EXPIRED", "Your offer has expired")
        elif status != "OFFERED" or drop["phase"] != "CLAIMING":
            raise AppError("NOT_OFFERED", "There is no offer for this entry")
        elif entry["offer_over"]:
            # Mark it expired in the SAME transaction (commit), then answer 409 after the commit,
            # so the sweeper and the claim path agree.
            await conn.execute(
                "UPDATE entries SET status = 'OFFER_EXPIRED', status_changed_at = now()"
                " WHERE id = $1 AND status = 'OFFERED'",
                entry_id,
            )
            expired = True

        if not expired:
            seat = await conn.fetchrow(TAKE_SEAT, entry_id, drop_id)
            if seat is None:
                if drop["mode"] == "fair":
                    log.error(
                        "fair claim found no free seat: offers exceeded seats (drop=%s)", drop_id
                    )
                metrics.incr("claims_sold_out")
                raise AppError("SOLD_OUT", "All seats are taken")
            updated = await conn.execute(
                "UPDATE entries SET status = 'ALLOCATED', allocated_at = now(),"
                " status_changed_at = now() WHERE id = $1 AND status = $2",
                entry_id,
                status,
            )
            if updated != "UPDATE 1":  # impossible while we hold the row lock; roll back loudly
                raise RuntimeError(f"guarded entry update lost the race: {updated}")
            alloc = await conn.fetchrow(
                "INSERT INTO allocations (drop_id, entry_id, seat_id, idempotency_key, run_no)"
                " VALUES ($1, $2, $3, $4, $5) RETURNING id, created_at",
                drop_id,
                entry_id,
                seat["id"],
                key,
                drop["run_no"],
            )
            if alloc is None:
                raise RuntimeError("ledger insert returned no row")
            payload = _payload(alloc["id"], seat["seat_no"], alloc["created_at"])
            await idempotency.store(
                conn,
                user_id=session.user_id,
                key=key,
                drop_id=drop_id,
                endpoint="claim",
                req_hash=req_hash,
                status_code=200,
                payload=payload,
            )

    if expired:
        raise AppError("OFFER_EXPIRED", "Your offer has expired")

    # After commit (best effort; never changes the response).
    metrics.incr("claims_ok")
    metrics.incr("accepted")
    metrics.observe_latency((time.perf_counter() - started) * 1000)
    if drop["mode"] == "fifo":
        await _maybe_finish_fifo(pool, drop_id, tasks)
    return Result(200, payload)


async def _maybe_finish_fifo(
    pool: asyncpg.Pool, drop_id: uuid.UUID, tasks: set[asyncio.Task[Any]]
) -> None:
    """FIFO: after the last seat, OPEN -> DONE (guarded, idempotent) and leftovers NOT_SELECTED."""
    try:
        has_free = await pool.fetchval(
            "SELECT EXISTS (SELECT 1 FROM seats WHERE drop_id = $1 AND status = 'free')", drop_id
        )
        if has_free:
            return
        moved = await pool.execute(
            "UPDATE drops SET phase = 'DONE', done_at = now()"
            " WHERE id = $1 AND mode = 'fifo' AND phase = 'OPEN'",
            drop_id,
        )
        if moved == "UPDATE 1":
            task = asyncio.create_task(finalize_unselected(pool, drop_id))
            tasks.add(task)
            task.add_done_callback(tasks.discard)
    except Exception:
        log.exception("could not finish the FIFO drop (the sweeper will retry)")


async def finalize_unselected(
    pool: asyncpg.Pool, drop_id: uuid.UUID, batch: int = 5000, retries: int = 20
) -> int:
    """Mark leftover REGISTERED/WAITLISTED entries NOT_SELECTED in short batches (short locks).

    Rows locked by a claim that is still in flight are skipped (SKIP LOCKED), so after each pass
    we look again, a few times, until nothing is left. The jobs runner repeats this for finished
    drops, so a skipped row can never stay behind for long.
    """
    total = 0
    for _ in range(retries):
        while True:
            done: int = await pool.fetchval(
                "WITH pick AS (SELECT id FROM entries WHERE drop_id = $1"
                "   AND status IN ('REGISTERED', 'WAITLISTED') LIMIT $2 FOR UPDATE SKIP LOCKED),"
                " upd AS (UPDATE entries e SET status = 'NOT_SELECTED', status_changed_at = now()"
                "   FROM pick WHERE e.id = pick.id RETURNING 1)"
                " SELECT count(*) FROM upd",
                drop_id,
                batch,
            )
            total += done
            if done < batch:
                break
        left = await pool.fetchval(
            "SELECT EXISTS (SELECT 1 FROM entries WHERE drop_id = $1"
            " AND status IN ('REGISTERED', 'WAITLISTED'))",
            drop_id,
        )
        if not left:
            return total
        await asyncio.sleep(0.25)
    return total
