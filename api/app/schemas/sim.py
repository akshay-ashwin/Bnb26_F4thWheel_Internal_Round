from app.schemas.base import ApiRequest, ApiResponse


class TelemetryIn(ApiRequest):
    run_id: str
    attack_phase: str
    clients_by_label: dict[str, int]
    identities_by_label: dict[str, int]
    requests_by_label: dict[str, int]


class TelemetryOut(ApiResponse):
    pass
