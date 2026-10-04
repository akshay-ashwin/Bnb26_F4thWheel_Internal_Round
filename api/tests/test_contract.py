"""The OpenAPI spec is the frozen contract: shape checks, plus behaviour of the stubs."""

from typing import Any

import httpx
import pytest

from app.errors import ErrorCode

EXPECTED_OPERATIONS = {
    ("get", "/api/drops/{drop_id}"),
    ("post", "/api/auth/otp/request"),
    ("post", "/api/auth/otp/verify"),
    ("post", "/api/drops/{drop_id}/entries"),
    ("get", "/api/drops/{drop_id}/me"),
    ("post", "/api/drops/{drop_id}/claim"),
    ("post", "/api/drops/{drop_id}/step-up"),
    ("post", "/api/admin/drops"),
    ("post", "/api/admin/drops/{drop_id}/phase"),
    ("get", "/api/admin/drops/{drop_id}/metrics"),
    ("get", "/api/admin/drops/{drop_id}/integrity"),
    ("get", "/api/admin/drops/{drop_id}/export"),
    ("get", "/api/admin/drops/{drop_id}/draw-proof"),
    ("put", "/api/admin/abuse/config"),
    ("post", "/api/sim/telemetry"),
    ("get", "/api/healthz"),
    ("get", "/api/readyz"),
}
DROP = "0b6c5d34-8c40-4d6d-9d57-2a53f3c9a001"
KEY = {"Idempotency-Key": "11111111-1111-1111-1111-111111111111"}


async def _spec(client: httpx.AsyncClient) -> dict[str, Any]:
    response = await client.get("/api/openapi.json")
    assert response.status_code == 200
    spec: dict[str, Any] = response.json()
    return spec


async def test_all_fifteen_endpoints_plus_ops_are_in_the_spec(client: httpx.AsyncClient) -> None:
    spec = await _spec(client)
    found = {(m, p) for p, item in spec["paths"].items() for m in item}
    assert found == EXPECTED_OPERATIONS


async def test_no_default_422_and_error_envelope_is_documented(client: httpx.AsyncClient) -> None:
    spec = await _spec(client)
    schemas = spec["components"]["schemas"]
    assert "HTTPValidationError" not in schemas
    assert set(schemas["ErrorCode"]["enum"]) == {c.value for c in ErrorCode}
    assert "NOT_IMPLEMENTED" not in schemas["ErrorCode"]["enum"]
    for path in spec["paths"].values():
        for op in path.values():
            assert "422" not in op["responses"] or "ErrorResponse" in str(op["responses"]["422"])


async def test_every_response_model_requires_server_time(client: httpx.AsyncClient) -> None:
    schemas = (await _spec(client))["components"]["schemas"]
    top_level = [
        "DropOut",
        "CreateDropOut",
        "PhaseOut",
        "IntegrityOut",
        "DrawProofOut",
        "OtpRequestOut",
        "OtpVerifyOut",
        "EntryOut",
        "MeOut",
        "ClaimOut",
        "StepUpOut",
        "MetricsOut",
        "AbuseConfigOut",
        "TelemetryOut",
        "HealthOut",
        "ReadyOut",
        "ErrorResponse",
    ]
    for name in top_level:
        assert "server_time" in schemas[name]["required"], name


async def test_sim_route_absent_when_sim_mode_is_off(client_no_sim: httpx.AsyncClient) -> None:
    spec = (await client_no_sim.get("/api/openapi.json")).json()
    assert "/api/sim/telemetry" not in spec["paths"]
    response = await client_no_sim.post("/api/sim/telemetry", json={})
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "NOT_FOUND"


@pytest.mark.parametrize(
    ("method", "path", "body", "headers"),
    [
        ("GET", f"/api/drops/{DROP}", None, {}),
        ("POST", "/api/auth/otp/request", {"phone": "+14155550123", "device_id": "d1"}, {}),
        ("POST", "/api/auth/otp/verify", {"request_id": "r", "otp": "1", "device_id": "d"}, {}),
        ("POST", f"/api/drops/{DROP}/entries", {}, {}),
        ("GET", f"/api/drops/{DROP}/me", None, {}),
        ("POST", f"/api/drops/{DROP}/claim", {"admission_token": "t"}, KEY),
        ("POST", f"/api/drops/{DROP}/step-up", {"otp": "123456"}, KEY),
    ],
)
async def test_public_stubs_return_501_envelope(
    client: httpx.AsyncClient,
    method: str,
    path: str,
    body: dict[str, Any] | None,
    headers: dict[str, str],
) -> None:
    response = await client.request(method, path, json=body, headers=headers)
    assert response.status_code == 501
    payload = response.json()
    assert payload["error"]["code"] == "NOT_IMPLEMENTED"
    assert payload["server_time"].endswith("Z")
    assert response.headers["x-request-id"]


async def test_admin_and_sim_stubs(
    client: httpx.AsyncClient, admin_headers: dict[str, str], sim_headers: dict[str, str]
) -> None:
    admin_calls: list[tuple[str, str, dict[str, Any] | None]] = [
        ("POST", "/api/admin/drops", {"name": "x", "mode": "fair", "window_s": 60}),
        ("POST", f"/api/admin/drops/{DROP}/phase", {"action": "open"}),
        ("GET", f"/api/admin/drops/{DROP}/metrics?window_s=60", None),
        ("GET", f"/api/admin/drops/{DROP}/integrity", None),
        ("GET", f"/api/admin/drops/{DROP}/export", None),
        ("GET", f"/api/admin/drops/{DROP}/draw-proof", None),
        ("PUT", "/api/admin/abuse/config", {"layers": {"L1": True}}),
    ]
    for method, path, body in admin_calls:
        denied = await client.request(method, path, json=body)
        assert denied.status_code == 401, path
        assert denied.json()["error"]["code"] == "UNAUTHENTICATED"
        wrong = await client.request(method, path, json=body, headers={"X-Admin-Key": "nope"})
        assert wrong.status_code == 401, path
        ok = await client.request(method, path, json=body, headers=admin_headers)
        assert ok.status_code == 501, path
    telemetry = {
        "run_id": "r",
        "attack_phase": "flood",
        "clients_by_label": {},
        "identities_by_label": {},
        "requests_by_label": {},
    }
    assert (await client.post("/api/sim/telemetry", json=telemetry)).status_code == 401
    ok = await client.post("/api/sim/telemetry", json=telemetry, headers=sim_headers)
    assert ok.status_code == 501


async def test_validation_error_is_400_envelope_and_never_echoes_input(
    client: httpx.AsyncClient,
) -> None:
    phone = "+14155550123"
    response = await client.post(
        "/api/auth/otp/request", json={"phone": phone, "device_id": "d", "bogus": "x"}
    )
    assert response.status_code == 400
    body = response.json()
    assert body["error"]["code"] == "VALIDATION_ERROR"
    assert body["error"]["details"]["fields"][0]["field"] == "body.bogus"
    assert phone not in response.text


async def test_bad_uuid_and_missing_idempotency_key(client: httpx.AsyncClient) -> None:
    bad = await client.get("/api/drops/not-a-uuid")
    assert bad.status_code == 400 and bad.json()["error"]["code"] == "VALIDATION_ERROR"
    missing = await client.post(f"/api/drops/{DROP}/claim", json={"admission_token": "t"})
    assert missing.status_code == 400
    assert missing.json()["error"]["code"] == "IDEMPOTENCY_KEY_MISSING"


async def test_unknown_path_and_wrong_method_use_the_envelope(client: httpx.AsyncClient) -> None:
    unknown = await client.get("/api/nope")
    assert unknown.status_code == 404 and unknown.json()["error"]["code"] == "NOT_FOUND"
    wrong = await client.delete(f"/api/drops/{DROP}")
    assert wrong.status_code == 405 and "server_time" in wrong.json()
