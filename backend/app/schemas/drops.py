from datetime import datetime

from pydantic import BaseModel


class DropOut(BaseModel):
    id: int
    name: str
    total_seats: int
    mode: str
    status: str
    entry_start: datetime
    entry_end: datetime
    entry_count: int
    allocated_seats: int
    remaining_seats: int


class EntryOut(BaseModel):
    drop_id: int
    status: str
    created_at: datetime
    queue_position: int


class AllocationOut(BaseModel):
    seat_number: int
    allocated_at: datetime


class DrawStatusOut(BaseModel):
    rank: int
    is_winner: bool


class MeOut(BaseModel):
    user_public_id: str
    drop_id: int
    entry: EntryOut | None
    allocation: AllocationOut | None
    draw: DrawStatusOut | None = None  # fair drops, after the draw


class ClaimOut(BaseModel):
    drop_id: int
    seat_number: int
    allocated_at: datetime


class IntegrityOut(BaseModel):
    drop_id: int
    total_seats: int
    physical_seats: int
    allocated_seats: int
    remaining_seats: int
    duplicate_allocation_count: int
    unique_allocated_users: int
    overselling_occurred: bool
    invariant_allocated_lte_total: bool
