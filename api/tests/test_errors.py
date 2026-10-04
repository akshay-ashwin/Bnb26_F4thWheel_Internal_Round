import asyncpg
import httpx
import pytest
from fastapi import FastAPI

from app.errors import RateLimited, ServiceUnavailable


@pytest.fixture
async def client(client_own_app: httpx.AsyncClient) -> httpx.AsyncClient:
    """These tests add routes to the app (and one breaks the pool): give them their own app."""
    return client_own_app


def _app(client: httpx.AsyncClient) -> FastAPI:
    app: FastAPI = client.app  # type: ignore[attr-defined]
    return app


async def test_retry_after_header_is_seconds_rounded_up(client: httpx.AsyncClient) -> None:
    @_app(client).get("/api/_t/limited")
    async def limited() -> None:
        raise RateLimited(retry_after_ms=1200)

    response = await client.get("/api/_t/limited")
    assert response.status_code == 429
    assert response.headers["retry-after"] == "2"
    assert response.json()["error"]["retry_after_ms"] == 1200


async def test_unexpected_exception_is_500_with_request_id_and_no_trace(
    client: httpx.AsyncClient,
) -> None:
    @_app(client).get("/api/_t/boom")
    async def boom() -> None:
        raise RuntimeError("secret internal detail")

    response = await client.get("/api/_t/boom", headers={"X-Request-ID": "req-abcdef123456"})
    assert response.status_code == 500
    body = response.json()
    assert body["error"]["code"] == "INTERNAL"
    assert "req-abcdef123456" in body["error"]["message"]
    assert "secret internal detail" not in response.text
    assert "Traceback" not in response.text
    assert response.headers["x-request-id"] == "req-abcdef123456"


@pytest.mark.parametrize(
    "exc",
    [asyncpg.CannotConnectNowError("x"), asyncpg.QueryCanceledError("x"), TimeoutError()],
)
async def test_database_trouble_is_503_with_retry_after(
    client: httpx.AsyncClient, exc: Exception
) -> None:
    @_app(client).get("/api/_t/db")
    async def db() -> None:
        raise exc

    response = await client.get("/api/_t/db")
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "SERVICE_UNAVAILABLE"
    assert int(response.headers["retry-after"]) >= 1


async def test_app_error_subclass_uses_its_own_status(client: httpx.AsyncClient) -> None:
    @_app(client).get("/api/_t/unavail")
    async def unavail() -> None:
        raise ServiceUnavailable(retry_after_ms=500)

    response = await client.get("/api/_t/unavail")
    assert response.status_code == 503 and response.headers["retry-after"] == "1"
