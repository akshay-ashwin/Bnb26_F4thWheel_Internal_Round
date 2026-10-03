"""Fair Draw lifecycle: open -> frozen -> drawn -> claimable -> closed.

Every transition locks the drop row (FOR UPDATE) and checks the current state, so transitions are atomic and
cannot be replayed. Entry creation takes FOR SHARE on the same row, so a freeze waits for in-flight entries and
every later entry sees `frozen`.
"""
import secrets
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.models import AdmissionToken, Drop, DrawResult, Entry, Seat, Session, User
from app.schemas.fair import (
    AdmissionTokenOut, CommitOut, DemoUserOut, DemoUsersOut, DrawOut, FairnessOut, FreezeOut,
    FrozenEntriesOut, ResultRow, ResultsOut, RevealOut, TransitionOut,
)
from app.utils import draw as draw_util
from app.utils.admission import sign_token
from app.utils.errors import ApiError
from app.utils.security import keyed_hash, new_public_id, new_token

ALGORITHM = {
    "version": 1,
    "commitment": "SHA256(seed), seed = 64-char lowercase hex string, hashed as its UTF-8 bytes",
    "entry_set_hash": "SHA256('fairdrop:entryset:v1\\ndrop_id=<id>\\ncount=<n>\\n' + '\\n'.join(sorted unique user_public_ids))",
    "score": "HMAC-SHA256(key=seed UTF-8, msg='fairdrop:draw:v1|<drop_id>|<user_public_id>') as hex",
    "ranking": "ascending by score (rank 1 = lowest); ranks 1..total_seats win",
}
_CHUNK = 4000


async def _lock_fair_drop(db: AsyncSession, drop_id: int) -> Drop:
    drop = (
        await db.execute(select(Drop).where(Drop.id == drop_id).with_for_update().execution_options(populate_existing=True))
    ).scalar_one_or_none()
    if drop is None:
        raise ApiError(404, "DROP_NOT_FOUND", "Drop not found.")
    if drop.mode != "fair":
        raise ApiError(409, "NOT_A_FAIR_DROP", "This operation is only available for fair-mode drops.")
    return drop


def _need(drop: Drop, *states: str) -> None:
    if drop.status not in states:
        raise ApiError(409, "INVALID_STATE", f"Drop is '{drop.status}'; this requires {' or '.join(states)}.")


async def _entry_public_ids(db: AsyncSession, drop_id: int) -> list[str]:
    return list(
        (
            await db.execute(
                select(User.public_id).join(Entry, Entry.user_id == User.id)
                .where(Entry.drop_id == drop_id).order_by(User.public_id)
            )
        ).scalars()
    )


async def create_drop(db: AsyncSession, name: str, total_seats: int, mode: str) -> Drop:
    now = datetime.now(UTC)
    drop = Drop(name=name, total_seats=total_seats, mode=mode, status="open",
                entry_start=now - timedelta(minutes=1), entry_end=now + timedelta(days=7))
    db.add(drop)
    await db.flush()
    await db.execute(
        insert(Seat).from_select(
            ["drop_id", "seat_number"], select(drop.id, func.generate_series(1, total_seats))
        )
    )
    await db.commit()
    return drop


async def commit_seed(db: AsyncSession, drop_id: int) -> CommitOut:
    """Generate the secret seed and publish only SHA256(seed). Write-once. Do this before freezing."""
    drop = await _lock_fair_drop(db, drop_id)
    _need(drop, "open", "frozen")
    if drop.seed_commitment is not None:
        raise ApiError(409, "SEED_ALREADY_COMMITTED", "A seed is already committed and cannot be replaced.")
    seed = draw_util.new_seed()
    drop.draw_seed, drop.seed_commitment = seed, draw_util.seed_commitment(seed)
    drop.seed_committed_at = datetime.now(UTC)
    await db.commit()
    return CommitOut(drop_id=drop.id, status=drop.status, seed_commitment=drop.seed_commitment,
                     seed_committed_at=drop.seed_committed_at)


async def freeze(db: AsyncSession, drop_id: int) -> FreezeOut:
    drop = await _lock_fair_drop(db, drop_id)
    _need(drop, "open")
    ids = await _entry_public_ids(db, drop_id)
    drop.entry_set_hash = draw_util.entry_set_hash(drop.id, ids)
    drop.frozen_entry_count = len(ids)
    drop.frozen_at = datetime.now(UTC)
    drop.status = "frozen"
    await db.commit()
    return FreezeOut(drop_id=drop.id, status=drop.status, entry_set_hash=drop.entry_set_hash,
                     frozen_entry_count=drop.frozen_entry_count, frozen_at=drop.frozen_at)


async def reveal(db: AsyncSession, drop_id: int) -> RevealOut:
    """Publish the seed. Idempotent. Requires a frozen entry set so the seed can't be used to pick entries."""
    drop = await _lock_fair_drop(db, drop_id)
    _need(drop, "frozen", "drawn", "claimable", "closed")
    if drop.seed_commitment is None or drop.draw_seed is None:
        raise ApiError(409, "NO_COMMITMENT", "No seed has been committed.")
    if drop.seed_revealed_at is None:
        drop.seed_revealed_at = datetime.now(UTC)
        await db.commit()
    return RevealOut(drop_id=drop.id, status=drop.status, seed=drop.draw_seed, seed_commitment=drop.seed_commitment)


async def run_draw(db: AsyncSession, drop_id: int) -> DrawOut:
    drop = await _lock_fair_drop(db, drop_id)
    _need(drop, "frozen")
    if drop.seed_revealed_at is None or drop.draw_seed is None:
        raise ApiError(409, "SEED_NOT_REVEALED", "Reveal the seed before running the draw.")
    ids = await _entry_public_ids(db, drop_id)
    # The draw must use exactly the frozen set.
    if draw_util.entry_set_hash(drop.id, ids) != drop.entry_set_hash:
        raise ApiError(409, "ENTRY_SET_MISMATCH", "Entries differ from the frozen set; refusing to draw.")
    uid = dict((await db.execute(select(User.public_id, User.id).join(Entry, Entry.user_id == User.id)
                                 .where(Entry.drop_id == drop_id))).all())
    ranked = draw_util.rank_entries(drop.draw_seed, drop.id, ids, drop.total_seats)
    for i in range(0, len(ranked), _CHUNK):
        await db.execute(
            insert(DrawResult).values(
                [dict(drop_id=drop.id, user_id=uid[r.user_public_id], user_public_id=r.user_public_id,
                      score=r.score, rank=r.rank, is_winner=r.is_winner) for r in ranked[i:i + _CHUNK]]
            )
        )
    drop.status, drop.drawn_at = "drawn", datetime.now(UTC)
    await db.commit()
    return DrawOut(drop_id=drop.id, status=drop.status, entry_set_hash=drop.entry_set_hash, entry_count=len(ranked),
                   winner_count=sum(r.is_winner for r in ranked), drawn_at=drop.drawn_at)


async def open_claims(db: AsyncSession, drop_id: int) -> TransitionOut:
    drop = await _lock_fair_drop(db, drop_id)
    _need(drop, "drawn")
    drop.status, drop.claimable_at = "claimable", datetime.now(UTC)
    await db.commit()
    return TransitionOut(drop_id=drop.id, status=drop.status)


async def close(db: AsyncSession, drop_id: int) -> TransitionOut:
    drop = await _lock_fair_drop(db, drop_id)
    _need(drop, "drawn", "claimable")
    drop.status, drop.closed_at = "closed", datetime.now(UTC)
    await db.commit()
    return TransitionOut(drop_id=drop.id, status=drop.status)


# ---- public verification -------------------------------------------------------------------------------------

async def _fair_drop_public(db: AsyncSession, drop_id: int) -> Drop:
    drop = await db.get(Drop, drop_id)
    if drop is None:
        raise ApiError(404, "DROP_NOT_FOUND", "Drop not found.")
    if drop.mode != "fair":
        raise ApiError(409, "NOT_A_FAIR_DROP", "This drop is not a fair-mode drop.")
    return drop


async def fairness(db: AsyncSession, drop_id: int) -> FairnessOut:
    drop = await _fair_drop_public(db, drop_id)
    winners = await db.scalar(
        select(func.count()).select_from(DrawResult).where(DrawResult.drop_id == drop_id, DrawResult.is_winner)
    ) or 0
    revealed = drop.seed_revealed_at is not None
    return FairnessOut(
        drop_id=drop.id, mode=drop.mode, status=drop.status, total_seats=drop.total_seats,
        seed_commitment=drop.seed_commitment, seed_revealed=revealed, seed=drop.draw_seed if revealed else None,
        entry_set_hash=drop.entry_set_hash, frozen_entry_count=drop.frozen_entry_count, frozen_at=drop.frozen_at,
        draw_status="complete" if drop.drawn_at else "pending", winner_count=winners, drawn_at=drop.drawn_at,
        algorithm=ALGORITHM,
    )


async def frozen_entries(db: AsyncSession, drop_id: int, offset: int, limit: int) -> FrozenEntriesOut:
    drop = await _fair_drop_public(db, drop_id)
    if drop.entry_set_hash is None:
        raise ApiError(409, "NOT_FROZEN", "Entries are not frozen yet.")
    ids = list(
        (
            await db.execute(
                select(User.public_id).join(Entry, Entry.user_id == User.id).where(Entry.drop_id == drop_id)
                .order_by(User.public_id).offset(offset).limit(limit)
            )
        ).scalars()
    )
    return FrozenEntriesOut(drop_id=drop_id, entry_set_hash=drop.entry_set_hash, total=drop.frozen_entry_count or 0,
                            offset=offset, user_public_ids=ids)


async def results(db: AsyncSession, drop_id: int, offset: int, limit: int) -> ResultsOut:
    drop = await _fair_drop_public(db, drop_id)
    if drop.drawn_at is None:
        raise ApiError(409, "DRAW_NOT_COMPLETE", "The draw has not been run yet.")
    rows = (
        await db.execute(
            select(DrawResult).where(DrawResult.drop_id == drop_id).order_by(DrawResult.rank).offset(offset).limit(limit)
        )
    ).scalars().all()
    winners = await db.scalar(
        select(func.count()).select_from(DrawResult).where(DrawResult.drop_id == drop_id, DrawResult.is_winner)
    ) or 0
    return ResultsOut(
        drop_id=drop_id, total=drop.frozen_entry_count or 0, winner_count=winners, offset=offset,
        results=[ResultRow(rank=r.rank, user_public_id=r.user_public_id, score=r.score, is_winner=r.is_winner) for r in rows],
    )


# ---- admission token -----------------------------------------------------------------------------------------

async def issue_token(db: AsyncSession, drop_id: int, user: User, session_id: int) -> AdmissionTokenOut:
    """Winners only, while claimable. Re-issuing replaces the previous (unconsumed) token, so an expired or lost
    offer can be refreshed without ever creating a second live token."""
    drop = await _fair_drop_public(db, drop_id)
    if drop.status != "claimable":
        raise ApiError(409, "DROP_NOT_CLAIMABLE", f"Drop is '{drop.status}', not claimable.")
    res = (
        await db.execute(select(DrawResult).where(DrawResult.drop_id == drop_id, DrawResult.user_id == user.id))
    ).scalar_one_or_none()
    if res is None or not res.is_winner:
        raise ApiError(403, "NOT_A_WINNER", "You are not a winner of this drop.")
    now = datetime.now(UTC)
    exp = now + timedelta(seconds=get_settings().admission_token_ttl_seconds)
    jti = secrets.token_hex(16)
    stmt = insert(AdmissionToken).values(drop_id=drop_id, user_id=user.id, session_id=session_id, jti=jti, expires_at=exp)
    stmt = stmt.on_conflict_do_update(
        constraint="uq_admission_tokens_drop_user",
        set_={"jti": jti, "session_id": session_id, "expires_at": exp, "issued_at": now},
        where=AdmissionToken.consumed_at.is_(None),
    ).returning(AdmissionToken.id)
    if (await db.execute(stmt)).scalar_one_or_none() is None:
        await db.rollback()
        raise ApiError(409, "ALREADY_CLAIMED", "You already claimed your seat.")
    await db.commit()
    token = sign_token(jti=jti, drop_id=drop_id, user_public_id=user.public_id, session_id=session_id,
                       exp=int(exp.timestamp()))
    return AdmissionTokenOut(drop_id=drop_id, admission_token=token, expires_at=exp)


# ---- demo helpers (SIM_MODE + admin key only) ---------------------------------------------------------------

async def demo_users(db: AsyncSession, count: int, drop_id: int | None, enter: bool) -> DemoUsersOut:
    settings = get_settings()
    if not settings.sim_mode:
        raise ApiError(403, "SIM_MODE_REQUIRED", "Demo user generation requires SIM_MODE.")
    entered = False
    if drop_id is not None and enter:
        # same locking protocol as create_entry
        drop = (await db.execute(select(Drop).where(Drop.id == drop_id).with_for_update(read=True)
                                 .execution_options(populate_existing=True))).scalar_one_or_none()
        if drop is None:
            raise ApiError(404, "DROP_NOT_FOUND", "Drop not found.")
        if drop.status != "open":
            raise ApiError(409, "ENTRY_CLOSED", "Entries are not open for this drop.")
        entered = True
    users = [DemoUserOut(user_public_id=new_public_id(), session_token=new_token()) for _ in range(count)]
    expires = datetime.now(UTC) + timedelta(minutes=settings.session_ttl_minutes)
    for i in range(0, count, _CHUNK):
        part = users[i:i + _CHUNK]
        rows = (
            await db.execute(
                insert(User).values([dict(public_id=u.user_public_id, phone_hash=keyed_hash("phone:demo:" + secrets.token_hex(8)))
                                     for u in part]).returning(User.id, User.public_id)
            )
        ).all()
        ids = {pub: uid for uid, pub in rows}
        await db.execute(
            insert(Session).values([dict(user_id=ids[u.user_public_id], token_hash=keyed_hash("sess:" + u.session_token),
                                         expires_at=expires) for u in part])
        )
        if entered:
            await db.execute(insert(Entry).values([dict(drop_id=drop_id, user_id=ids[u.user_public_id]) for u in part]))
    await db.commit()
    return DemoUsersOut(users=users, entered=entered)
