from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import Field

from app.schemas.base import ApiRequest, ApiResponse, Mode, Phase


class DropOut(ApiResponse):
    id: UUID
    name: str
    capacity: int
    mode: Mode
    phase: Phase
    reg_opens_at: datetime | None
    reg_closes_at: datetime | None
    claim_window_s: int
    seats_remaining: int
    seed_commit: str
    seed: str | None = Field(description="Only after the draw (reveal).")
    entry_set_hash: str | None = Field(description="Only after the draw.")


class CreateDropIn(ApiRequest):
    name: str = Field(min_length=1, max_length=120)
    capacity: int = Field(default=500, ge=1, le=500)
    mode: Mode
    window_s: int = Field(ge=1, le=86400)
    claim_window_s: int = Field(default=120, ge=1, le=3600)


class CreateDropOut(ApiResponse):
    drop_id: UUID
    seed_commit: str


class PhaseIn(ApiRequest):
    action: Literal["open", "close", "draw", "reset"]


class PhaseOut(ApiResponse):
    phase: Phase


class IntegrityOut(ApiResponse):
    seats_total: int
    sold: int
    free: int
    oversold: int
    duplicate_entries_with_seats: int
    invariant_ok: bool


class DrawProofOut(ApiResponse):
    seed_commit: str
    seed: str
    entry_set_hash: str
    algorithm: str = "HMAC_SHA256(seed, drop_id‖user_public_id) asc"
