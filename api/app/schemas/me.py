from datetime import datetime
from uuid import UUID

from pydantic import Field

from app.schemas.base import ApiObject, ApiResponse, EntryStatus, Phase


class MeEntry(ApiObject):
    entry_id: UUID
    status: EntryStatus
    rank: int | None = None
    waitlist_pos: int | None = None
    offer_expires_at: datetime | None = None
    step_up_required: bool
    admission_token: str | None = None
    dev_otp: str | None = Field(default=None, description="SIM_MODE only; null otherwise.")


class MeAllocation(ApiObject):
    allocation_id: UUID
    seat_no: int
    confirmed_at: datetime


class MeOut(ApiResponse):
    phase: Phase
    entry: MeEntry | None
    allocation: MeAllocation | None
    poll_after_ms: int
