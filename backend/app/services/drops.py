from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Allocation, Drop, DrawResult, Entry, Seat, User
from app.schemas.drops import AllocationOut, DrawStatusOut, DropOut, EntryOut, IntegrityOut, MeOut
from app.utils.errors import ApiError


async def get_drop_or_404(db: AsyncSession, drop_id: int) -> Drop:
    drop = await db.get(Drop, drop_id)
    if drop is None:
        raise ApiError(404, "DROP_NOT_FOUND", "Drop not found.")
    return drop


async def drop_view(db: AsyncSession, drop_id: int) -> DropOut:
    drop = await get_drop_or_404(db, drop_id)
    entries = await db.scalar(select(func.count()).select_from(Entry).where(Entry.drop_id == drop_id))
    allocated = await db.scalar(select(func.count()).select_from(Allocation).where(Allocation.drop_id == drop_id))
    return DropOut(
        id=drop.id, name=drop.name, total_seats=drop.total_seats, mode=drop.mode, status=drop.status,
        entry_start=drop.entry_start, entry_end=drop.entry_end, entry_count=entries or 0,
        allocated_seats=allocated or 0, remaining_seats=drop.total_seats - (allocated or 0),
    )


async def _entry_out(db: AsyncSession, entry: Entry) -> EntryOut:
    # FIFO position by server-side creation time (id breaks ties).
    ahead = await db.scalar(
        select(func.count()).select_from(Entry).where(
            Entry.drop_id == entry.drop_id,
            (Entry.created_at < entry.created_at) | ((Entry.created_at == entry.created_at) & (Entry.id < entry.id)),
        )
    )
    return EntryOut(drop_id=entry.drop_id, status=entry.status, created_at=entry.created_at, queue_position=(ahead or 0) + 1)


async def create_entry(db: AsyncSession, drop_id: int, user: User) -> tuple[EntryOut, bool]:
    """Returns (entry, created). Repeat calls return the existing entry."""
    # FOR SHARE: a concurrent freeze (FOR UPDATE) waits for in-flight entries; later entries see the new status.
    drop = (
        await db.execute(select(Drop).where(Drop.id == drop_id).with_for_update(read=True)
                         .execution_options(populate_existing=True))
    ).scalar_one_or_none()
    if drop is None:
        raise ApiError(404, "DROP_NOT_FOUND", "Drop not found.")
    now = datetime.now(UTC)
    if drop.status != "open" or not (drop.entry_start <= now <= drop.entry_end):
        raise ApiError(409, "ENTRY_CLOSED", "Entries are not open for this drop.")
    inserted = (
        await db.execute(
            insert(Entry).values(drop_id=drop_id, user_id=user.id)
            .on_conflict_do_nothing(constraint="uq_entries_drop_user").returning(Entry.id)
        )
    ).scalar_one_or_none()
    await db.commit()
    entry = (await db.execute(select(Entry).where(Entry.drop_id == drop_id, Entry.user_id == user.id))).scalar_one()
    return await _entry_out(db, entry), inserted is not None


async def me(db: AsyncSession, drop_id: int, user: User) -> MeOut:
    drop = await get_drop_or_404(db, drop_id)
    entry = (await db.execute(select(Entry).where(Entry.drop_id == drop_id, Entry.user_id == user.id))).scalar_one_or_none()
    alloc = (
        await db.execute(
            select(Seat.seat_number, Allocation.created_at)
            .join(Seat, Seat.id == Allocation.seat_id)
            .where(Allocation.drop_id == drop_id, Allocation.user_id == user.id)
        )
    ).first()
    draw = None
    if drop.drawn_at is not None:
        res = (await db.execute(select(DrawResult).where(DrawResult.drop_id == drop_id, DrawResult.user_id == user.id))).scalar_one_or_none()
        if res is not None:
            draw = DrawStatusOut(rank=res.rank, is_winner=res.is_winner)
    return MeOut(
        draw=draw,
        user_public_id=user.public_id, drop_id=drop_id,
        entry=await _entry_out(db, entry) if entry else None,
        allocation=AllocationOut(seat_number=alloc[0], allocated_at=alloc[1]) if alloc else None,
    )


async def integrity(db: AsyncSession, drop_id: int) -> IntegrityOut:
    drop = await get_drop_or_404(db, drop_id)
    physical = await db.scalar(select(func.count()).select_from(Seat).where(Seat.drop_id == drop_id)) or 0
    allocated = await db.scalar(select(func.count()).select_from(Allocation).where(Allocation.drop_id == drop_id)) or 0
    users = await db.scalar(select(func.count(func.distinct(Allocation.user_id))).where(Allocation.drop_id == drop_id)) or 0

    def dup(col):  # rows beyond the first per value
        sub = select(func.count().label("c")).where(Allocation.drop_id == drop_id).group_by(col).subquery()
        return select(func.coalesce(func.sum(sub.c.c - 1), 0)).where(sub.c.c > 1)

    dups = int(await db.scalar(dup(Allocation.user_id)) or 0) + int(await db.scalar(dup(Allocation.seat_id)) or 0)
    ok = allocated <= drop.total_seats and allocated <= physical
    return IntegrityOut(
        drop_id=drop_id, total_seats=drop.total_seats, physical_seats=physical, allocated_seats=allocated,
        remaining_seats=drop.total_seats - allocated, duplicate_allocation_count=dups,
        unique_allocated_users=users, overselling_occurred=not ok, invariant_allocated_lte_total=ok,
    )
