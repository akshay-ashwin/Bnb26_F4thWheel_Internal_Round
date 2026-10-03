"""Fair-mode claim: winner + valid single-use admission token -> exactly one seat, in one transaction.

Locks, always in this order: drop row (FOR SHARE, so `close` waits for in-flight claims), the user's token row
(FOR UPDATE, serialises same-user claims), then one free seat (FOR UPDATE SKIP LOCKED). Winners never exceed
seats, so a winner always finds a free seat; the DB unique constraints remain the final guard against any bug.
"""
from datetime import UTC, datetime

from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import AdmissionToken, Allocation, Drop, DrawResult, Entry, Seat, User
from app.utils.admission import TokenError, verify_token

Result = tuple[int, dict]


def _err(status: int, code: str, message: str) -> Result:
    return status, {"error": {"code": code, "message": message}}


async def fair_claim_in_txn(db: AsyncSession, drop_id: int, user: User, session_id: int, token: str | None) -> Result:
    drop = (
        await db.execute(select(Drop).where(Drop.id == drop_id).with_for_update(read=True)
                         .execution_options(populate_existing=True))
    ).scalar_one()
    if drop.status != "claimable":
        return _err(409, "DROP_NOT_CLAIMABLE", f"Drop is '{drop.status}', not claimable.")

    res = (
        await db.execute(select(DrawResult.is_winner).where(DrawResult.drop_id == drop_id, DrawResult.user_id == user.id))
    ).scalar_one_or_none()
    if not res:
        return _err(403, "NOT_A_WINNER", "You are not a winner of this drop.")

    if not token:
        return _err(403, "ADMISSION_TOKEN_REQUIRED", "Send your admission token in the X-Admission-Token header.")
    try:
        claims = verify_token(token)
    except TokenError as e:
        return _err(401, e.code, "Admission token is invalid." if e.code.endswith("INVALID") else "Admission token expired.")
    if claims["d"] != drop_id:
        return _err(403, "ADMISSION_TOKEN_WRONG_DROP", "Token was issued for a different drop.")
    if claims["u"] != user.public_id:
        return _err(403, "ADMISSION_TOKEN_WRONG_USER", "Token was issued to a different user.")
    if claims["s"] != session_id:
        return _err(403, "ADMISSION_TOKEN_WRONG_SESSION", "Token is bound to a different session.")

    row = (
        await db.execute(
            select(AdmissionToken).where(AdmissionToken.drop_id == drop_id, AdmissionToken.user_id == user.id)
            .with_for_update().execution_options(populate_existing=True)
        )
    ).scalar_one_or_none()
    if row is None or row.jti != claims["jti"]:
        return _err(409, "ADMISSION_TOKEN_SUPERSEDED", "Token is no longer the current token; request a new one.")
    if row.consumed_at is not None:
        return _err(409, "ADMISSION_TOKEN_USED", "Admission token has already been used.")

    seat = (
        await db.execute(
            select(Seat).where(Seat.drop_id == drop_id, Seat.status == "available")
            .order_by(Seat.seat_number).limit(1).with_for_update(skip_locked=True)
        )
    ).scalar_one_or_none()
    if seat is None:
        return _err(409, "SOLD_OUT", "No seats remaining.")

    now = datetime.now(UTC)
    allocated_at = (
        await db.execute(
            insert(Allocation).values(drop_id=drop_id, seat_id=seat.id, user_id=user.id).returning(Allocation.created_at)
        )
    ).scalar_one()
    await db.execute(update(Seat).where(Seat.id == seat.id).values(status="allocated"))
    await db.execute(update(AdmissionToken).where(AdmissionToken.id == row.id).values(consumed_at=now))
    await db.execute(update(Entry).where(Entry.drop_id == drop_id, Entry.user_id == user.id).values(status="claimed"))
    return 200, {"drop_id": drop_id, "seat_number": seat.seat_number, "allocated_at": allocated_at.isoformat()}
