from fastapi.testclient import TestClient

from app.main import app


def test_healthz_returns_ok_with_server_time() -> None:
    response = TestClient(app).get("/api/healthz")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert "server_time" in body
