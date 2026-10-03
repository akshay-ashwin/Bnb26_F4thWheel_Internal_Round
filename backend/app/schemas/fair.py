from datetime import datetime

from pydantic import BaseModel, Field


class CreateDropIn(BaseModel):
    name: str = Field(default="Fair Drop", max_length=200)
    total_seats: int = Field(default=500, ge=1, le=100_000)
    mode: str = Field(default="fair", pattern="^(fair|fifo)$")


class DemoUsersIn(BaseModel):
    count: int = Field(ge=1, le=60_000)
    drop_id: int | None = None  # if set, every user also enters this drop
    enter: bool = True


class DemoUserOut(BaseModel):
    user_public_id: str
    session_token: str


class DemoUsersOut(BaseModel):
    users: list[DemoUserOut]
    entered: bool


class CommitOut(BaseModel):
    drop_id: int
    status: str
    seed_commitment: str
    seed_committed_at: datetime


class FreezeOut(BaseModel):
    drop_id: int
    status: str
    entry_set_hash: str
    frozen_entry_count: int
    frozen_at: datetime


class RevealOut(BaseModel):
    drop_id: int
    status: str
    seed: str
    seed_commitment: str


class DrawOut(BaseModel):
    drop_id: int
    status: str
    entry_set_hash: str
    entry_count: int
    winner_count: int
    drawn_at: datetime


class TransitionOut(BaseModel):
    drop_id: int
    status: str


class FairnessOut(BaseModel):
    drop_id: int
    mode: str
    status: str
    total_seats: int
    seed_commitment: str | None
    seed_revealed: bool
    seed: str | None  # null until revealed
    entry_set_hash: str | None
    frozen_entry_count: int | None
    frozen_at: datetime | None
    draw_status: str  # pending | complete
    winner_count: int
    drawn_at: datetime | None
    algorithm: dict


class FrozenEntriesOut(BaseModel):
    drop_id: int
    entry_set_hash: str
    total: int
    offset: int
    user_public_ids: list[str]  # ascending, canonical order


class ResultRow(BaseModel):
    rank: int
    user_public_id: str
    score: str
    is_winner: bool


class ResultsOut(BaseModel):
    drop_id: int
    total: int
    winner_count: int
    offset: int
    results: list[ResultRow]


class AdmissionTokenOut(BaseModel):
    drop_id: int
    admission_token: str
    expires_at: datetime
