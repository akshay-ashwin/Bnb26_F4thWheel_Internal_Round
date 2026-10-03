import time

import httpx
from fastapi import FastAPI


async def test_healthz_ok_with_server_time(client: httpx.AsyncClient) -> None:
    response = await client.get("/api/healthz")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok" and body["server_time"].endswith("Z")


async def test_readyz_ready(client: httpx.AsyncClient) -> None:
    response = await client.get("/api/readyz")
    assert response.status_code == 200
    body = response.json()
    assert (body["status"], body["postgres"], body["redis"]) == ("ready", "up", "up")


async def test_readyz_degraded_when_redis_is_down_and_healthz_still_ok(
    client_redis_down: httpx.AsyncClient,
) -> None:
    ready = await client_redis_down.get("/api/readyz")
    assert ready.status_code == 200
    assert ready.json()["status"] == "degraded" and ready.json()["redis"] == "down"
    assert (await client_redis_down.get("/api/healthz")).status_code == 200


async def test_readyz_is_503_envelope_when_postgres_is_gone(client: httpx.AsyncClient) -> None:
    app: FastAPI = client.app  # type: ignore[attr-defined]
    await app.state.pool.close()
    started = time.perf_counter()
    response = await client.get("/api/readyz")
    assert time.perf_counter() - started < 2
    assert response.status_code == 503
    body = response.json()
    assert body["error"]["code"] == "SERVICE_UNAVAILABLE"
    assert body["error"]["details"]["postgres"] == "down"
    assert "retry-after" in response.headers
