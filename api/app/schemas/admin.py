from pydantic import Field

from app.schemas.base import ApiObject, ApiRequest, ApiResponse, Mode, Phase


class RpsPoint(ApiObject):
    t: int = Field(description="Unix seconds")
    total: int


class Latency(ApiObject):
    p50: float
    p95: float
    p99: float


class StepUps(ApiObject):
    issued: int
    passed: int
    failed: int


class MetricsOut(ApiResponse):
    rps_series: list[RpsPoint]
    outcomes_series: dict[str, list[int]] = Field(
        description="Each array is aligned with rps_series. Keys: accepted, rate_limited, "
        "token_rejected, duplicate (more may be added in Plan 14)."
    )
    latency: Latency
    error_rate: float
    active_sessions: int
    entries: int
    offers: int
    allocated: int
    remaining: int
    flagged_entries: int
    step_ups: StepUps
    # Additions for the dashboard and evaluator (see docs/contract/additions.md).
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


class AbuseConfigIn(ApiRequest):
    layers: dict[str, bool]
    thresholds: dict[str, float] = Field(default_factory=dict)


class AbuseConfigOut(ApiResponse):
    layers: dict[str, bool]
    thresholds: dict[str, float]
