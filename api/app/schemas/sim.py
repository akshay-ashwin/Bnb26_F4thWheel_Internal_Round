from typing import Any

from app.schemas.base import ApiRequest, ApiResponse


class TelemetryIn(ApiRequest):
    run_id: str
    attack_phase: str
    clients_by_label: dict[str, int]
    identities_by_label: dict[str, int]
    requests_by_label: dict[str, int]
    fairness_live: dict[str, Any] | None = None  # addition: the live evaluator's numbers


class TelemetryOut(ApiResponse):
    pass
