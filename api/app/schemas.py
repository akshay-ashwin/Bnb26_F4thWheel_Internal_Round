"""Pydantic models for every contract endpoint (docs/contract/README.md). Every response model
carries `server_time`, filled when the model is created (so it is also in the OpenAPI schema)."""

from __future__ import annotations

from typing import Any, ClassVar, Literal

from pydantic import BaseModel, ConfigDict, Field, model_serializer

from app.errors import now_iso

EntryStatus = Literal[
    "REGISTERED",
    "OFFERED",
    "STEP_UP_REQUIRED",
    "WAITLISTED",
    "NOT_SELECTED",
    "OFFER_EXPIRED",
    "ALLOCATED",
    "DISQUALIFIED",
]
Phase = Literal["SCHEDULED", "OPEN", "CLOSED", "DRAWN", "CLAIMING", "DONE"]
Mode = Literal["fair", "fifo"]


class ApiModel(BaseModel):
    """Base for responses: `server_time` always; optional fields listed in `omit_if_none` are
    left out of the JSON (the contract marks them `field?`)."""

    model_config = ConfigDict(extra="forbid")
    omit_if_none: ClassVar[tuple[str, ...]] = ()
    server_time: str = Field(default_factory=now_iso)

    @model_serializer(mode="wrap")
    def _omit(self, handler: Any) -> Any:
        data = handler(self)
        for name in self.omit_if_none:
            if data.get(name) is None:
                data.pop(name, None)
        return data


class RequestModel(BaseModel):
    model_config = ConfigDict(extra="ignore")


# --- health ---
class HealthOut(ApiModel):
    status: str = "ok"


class ReadyOut(ApiModel):
    status: str
    postgres: bool
    redis: bool


# --- auth ---
class OtpRequestIn(RequestModel):
    phone: str = Field(min_length=3, max_length=32)
    device_id: str = Field(min_length=8, max_length=128, pattern=r"^[A-Za-z0-9._:\-]+$")


class OtpRequestOut(ApiModel):
    omit_if_none: ClassVar[tuple[str, ...]] = ("dev_otp",)
    request_id: str
    expires_in_s: int
    dev_otp: str | None = None


class OtpVerifyIn(RequestModel):
    request_id: str = Field(min_length=8, max_length=128)
    otp: str = Field(min_length=4, max_length=12)
    device_id: str = Field(min_length=8, max_length=128, pattern=r"^[A-Za-z0-9._:\-]+$")


class OtpVerifyOut(ApiModel):
    session_token: str
    user_public_id: str


# --- drops, entries, me ---
class DropOut(ApiModel):
    omit_if_none: ClassVar[tuple[str, ...]] = ("seed", "entry_set_hash")
    id: str
    name: str
    capacity: int
    mode: Mode
    phase: Phase
    reg_opens_at: str | None
    reg_closes_at: str | None
    claim_window_s: int
    seats_remaining: int
    seed_commit: str
    seed: str | None = None
    entry_set_hash: str | None = None


class EntryOut(ApiModel):
    entry_id: str
    status: EntryStatus


class AllocationOut(BaseModel):
    allocation_id: str
    seat_no: int
    confirmed_at: str


class MeEntry(BaseModel):
    entry_id: str
    status: EntryStatus
    rank: int | None = None
    waitlist_pos: int | None = None
    offer_expires_at: str | None = None
    step_up_required: bool = False
    admission_token: str | None = None
    dev_otp: str | None = None  # SIM_MODE only

    @model_serializer(mode="wrap")
    def _omit(self, handler: Any) -> Any:
        data = handler(self)
        if data.get("dev_otp") is None:
            data.pop("dev_otp", None)
        return data


class MeOut(ApiModel):
    phase: Phase
    entry: MeEntry | None
    allocation: AllocationOut | None
    poll_after_ms: int


class ClaimIn(RequestModel):
    admission_token: str = Field(min_length=10, max_length=2048)


class ClaimOut(ApiModel):
    allocation_id: str
    seat_no: int
    confirmed_at: str


class StepUpIn(RequestModel):
    otp: str = Field(min_length=4, max_length=12)


class StepUpOut(ApiModel):
    status: EntryStatus


# --- admin ---
class DropCreateIn(RequestModel):
    name: str = Field(min_length=1, max_length=200)
    capacity: int = Field(default=500, ge=1, le=10_000)
    mode: Mode
    window_s: int = Field(ge=1, le=3600)
    claim_window_s: int = Field(default=120, ge=1, le=3600)


class DropCreateOut(ApiModel):
    drop_id: str
    seed_commit: str


class PhaseIn(RequestModel):
    action: Literal["open", "close", "draw", "reset"]
    mode: Mode | None = None  # only meaningful with reset


class PhaseOut(ApiModel):
    phase: Phase


class IntegrityOut(ApiModel):
    seats_total: int
    sold: int
    free: int
    oversold: int
    duplicate_entries_with_seats: int
    invariant_ok: bool
    extra: dict[str, int]


class StepUps(BaseModel):
    issued: int
    passed: int
    failed: int


class Latency(BaseModel):
    p50: float
    p95: float
    p99: float


class MetricsOut(ApiModel):
    """Contract fields first; fields after `step_ups` are additions for the dashboard/evaluator."""

    rps_series: list[dict[str, int]]
    outcomes_series: dict[str, list[int]]
    latency: Latency
    error_rate: float
    active_sessions: int
    entries: int
    offers: int
    allocated: int
    remaining: int
    flagged_entries: int
    step_ups: StepUps
    # additions
    phase: Phase
    mode: Mode
    run_no: int
    capacity: int
    oversold: int
    invariant_ok: bool
    claims_ok: int
    claims_sold_out: int
    blocked_requests: int
    throttled_requests: int
    duplicate_requests: int
    rate_limited_by_layer: dict[str, list[int]]
    window_s: int
    metrics_dropped: int


class DropListItem(BaseModel):
    id: str
    name: str
    mode: Mode
    phase: Phase
    run_no: int
    capacity: int


class DropListOut(ApiModel):
    drops: list[DropListItem]


class SimLatestOut(ApiModel):
    latest: dict[str, Any] | None


class DrawProofOut(ApiModel):
    omit_if_none: ClassVar[tuple[str, ...]] = ("eligible_public_ids", "ranked_public_ids")
    seed_commit: str
    seed: str
    entry_set_hash: str
    algorithm: str
    drop_id: str
    run_no: int
    eligible_public_ids: list[str] | None = None
    ranked_public_ids: list[str] | None = None


class TelemetryIn(RequestModel):
    run_id: str = Field(max_length=200)
    attack_phase: str = Field(max_length=100)
    clients_by_label: dict[str, int] = Field(default_factory=dict)
    identities_by_label: dict[str, int] = Field(default_factory=dict)
    requests_by_label: dict[str, int] = Field(default_factory=dict)
    fairness_live: dict[str, Any] | None = None


class EmptyOut(ApiModel):
    pass


class AbuseConfigIn(RequestModel):
    layers: dict[str, bool] = Field(default_factory=dict)
    thresholds: dict[str, Any] = Field(default_factory=dict)


class AbuseConfigOut(ApiModel):
    layers: dict[str, bool]
    thresholds: dict[str, Any]
