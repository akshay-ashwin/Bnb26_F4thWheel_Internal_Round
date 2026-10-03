"""Claim entry point. fifo-mode drops use the FIFO baseline below; fair-mode drops dispatch to
services/fair_claim.py (winner + admission token). The FIFO path is the BEFORE comparison, not the fair mechanism.

Safety layers: (1) per-user entry row lock serialises same-user claims, (2) seat picked with
FOR UPDATE SKIP LOCKED, (3) DB unique constraints on allocations (user/drop, seat) are the final guard.
"""
from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.services.fair_claim import fair_claim_in_txn
from app.models import Allocation, Drop, Entry, IdempotencyRecord, Seat, User

Result = tuple[int, dict]  # (http status, json body)


def _err(status: int, code: str, message: str) -> Result:
    return status, {"error": {"code": code, "message": message}}


async def _claim_in_txn(db: AsyncSession, drop_id: int, user: User) -> Result:  # FIFO baseline
    drop = await db.get(Drop, drop_id)
    if drop is None:
        return _err(404, "DROP_NOT_FOUND", "Drop not found.")
    if drop.mode != "fifo":
        return _err(409, "MODE_NOT_SUPPORTED", "FIFO claim is only available for fifo-mode drops.")  # unreachable
    if drop.status != "open":
        return _err(409, "DROP_NOT_OPEN", "Drop is not open.")

    entry = (
        await db.execute(select(Entry).where(Entry.drop_id == drop_id, Entry.user_id == user.id).with_for_update())
    ).scalar_one_or_none()
    if entry is None:
        return _err(403, "NO_ENTRY", "You must enter the drop before claiming.")

    existing = await db.scalar(
        select(Allocation.id).where(Allocation.drop_id == drop_id, Allocation.user_id == user.id)
    )
    if existing is not None:
        return _err(409, "ALREADY_CLAIMED", "You already hold a seat in this drop.")

    seat = (
        await db.execute(
            select(Seat).where(Seat.drop_id == drop_id, Seat.status == "available")
            .order_by(Seat.seat_number).limit(1).with_for_update(skip_locked=True)
        )
    ).scalar_one_or_none()
    if seat is None:
        return _err(409, "SOLD_OUT", "No seats remaining.")

    alloc = (
        await db.execute(
            insert(Allocation).values(drop_id=drop_id, seat_id=seat.id, user_id=user.id)
            .returning(Allocation.created_at)
        )
    ).scalar_one()
    await db.execute(update(Seat).where(Seat.id == seat.id).values(status="allocated"))
    await db.execute(update(Entry).where(Entry.id == entry.id).values(status="claimed"))
    return 200, {"drop_id": drop_id, "seat_number": seat.seat_number, "allocated_at": alloc.isoformat()}


async def claim(
    db: AsyncSession, drop_id: int, user: User, idempotency_key: str | None,
    session_id: int | None = None, admission_token: str | None = None,
) -> Result:
    endpoint = f"POST /api/drops/{drop_id}/claim"
    is_fair = (await db.scalar(select(Drop.mode).where(Drop.id == drop_id))) == "fair"
    try:
        if idempotency_key:
            # Concurrent same-key requests block on this unique index until the first commits,
            # then see a conflict and replay the stored response.
            created = (
                await db.execute(
                    insert(IdempotencyRecord).values(user_id=user.id, endpoint=endpoint, key=idempotency_key)
                    .on_conflict_do_nothing(constraint="uq_idem_user_endpoint_key").returning(IdempotencyRecord.id)
                )
            ).scalar_one_or_none()
            if created is None:
                rec = (
                    await db.execute(
                        select(IdempotencyRecord).where(
                            IdempotencyRecord.user_id == user.id,
                            IdempotencyRecord.endpoint == endpoint,
                            IdempotencyRecord.key == idempotency_key,
                        )
                    )
                ).scalar_one()
                status, body = rec.response_status or 409, rec.response_body or {}
                await db.rollback()
                return status, body

        if is_fair:
            status, body = await fair_claim_in_txn(db, drop_id, user, session_id or 0, admission_token)
            if status != 200:
                # Nothing consumed. Roll back (incl. the idempotency row) so the same key can be retried
                # with a corrected token instead of replaying a stale failure.
                await db.rollback()
                return status, body
        else:
            status, body = await _claim_in_txn(db, drop_id, user)
        if idempotency_key:
            await db.execute(
                update(IdempotencyRecord)
                .where(IdempotencyRecord.user_id == user.id, IdempotencyRecord.endpoint == endpoint,
                       IdempotencyRecord.key == idempotency_key)
                .values(response_status=status, response_body=body)
            )
        await db.commit()
        return status, body
    except IntegrityError:
        # A DB constraint rejected a duplicate/oversell attempt; nothing was written.
        await db.rollback()
        return _err(409, "CLAIM_CONFLICT", "Claim conflicted with a concurrent request; retry.")
