"""Shared fixtures.

Tests run against a real PostgreSQL whose name ends in `_test` (they truncate every table, so any
other name is refused). `uv run fd test-api` migrates that database with the same dbmate files as
the real one before pytest starts; nothing here creates schema.

Two database roles are used on purpose:
  * the owner role (TEST_DATABASE_URL, a superuser) for fixtures and fault injection;
  * the restricted application role (TEST_APP_DATABASE_URL), exactly what the api will use.

Redis uses a reserved DB index (15) so tests never touch the dev data.
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator
from urllib.parse import urlparse

import asyncpg
import httpx
import pytest

from app.config import Settings, get_settings
from app.main import create_app

TEST_REDIS_URL = "redis://redis:6379/15"
DEAD_REDIS_URL = "redis://127.0.0.1:1/0"

TABLES = (
    "allocations, seats, entries, sessions, idempotency_records, abuse_events, "
    "drop_runs, app_settings, system_state, drops, users"
)


def _database_url(variable: str) -> str:
    url = os.environ.get(variable)
    if not url:
        pytest.exit(f"{variable} is not set: run the tests with `uv run fd test-api`", 2)
    name = urlparse(url).path.lstrip("/")
    if not name.endswith("_test"):
        pytest.exit(f"refusing to run: {variable} points at '{name}' (must end in _test)", 2)
    return url


@pytest.fixture(scope="session")
def owner_url() -> str:
    return _database_url("TEST_DATABASE_URL")


@pytest.fixture(scope="session")
def app_url() -> str:
    return _database_url("TEST_APP_DATABASE_URL")


@pytest.fixture
async def db(owner_url: str) -> AsyncIterator[asyncpg.Connection]:
    """Owner connection on an empty database (every table truncated before the test)."""
    conn = await asyncpg.connect(owner_url)
    try:
        async with conn.transaction():
            # The allocations ledger refuses TRUNCATE unless this transaction-local flag is on.
            await conn.execute("SET LOCAL fairdrop.resetting = 'on'")
            await conn.execute(f"TRUNCATE {TABLES} RESTART IDENTITY CASCADE")
        yield conn
    finally:
        await conn.close()


@pytest.fixture
async def app_db(app_url: str, db: asyncpg.Connection) -> AsyncIterator[asyncpg.Connection]:
    """Connection as the restricted application role (depends on `db` for the clean slate)."""
    conn = await asyncpg.connect(app_url)
    try:
        yield conn
    finally:
        await conn.close()


@pytest.fixture(scope="session")
def base_settings() -> Settings:
    return get_settings()


@pytest.fixture
def test_settings(base_settings: Settings, app_url: str) -> Settings:
    """Settings for the api under test: the test database as the restricted role, test Redis."""
    secret = type(base_settings.database_url)
    return base_settings.model_copy(
        update={"database_url": secret(app_url), "redis_url": TEST_REDIS_URL}
    )


@pytest.fixture
async def clean_db(db: asyncpg.Connection) -> None:
    """Empty database for a test that goes through the api (alias of the `db` clean slate)."""


async def _client(settings: Settings) -> AsyncIterator[httpx.AsyncClient]:
    app = create_app(settings)
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            client.app = app  # type: ignore[attr-defined]
            yield client


@pytest.fixture
async def client(test_settings: Settings) -> AsyncIterator[httpx.AsyncClient]:
    async for c in _client(test_settings.model_copy(update={"sim_mode": True})):
        yield c


@pytest.fixture
async def client_no_sim(test_settings: Settings) -> AsyncIterator[httpx.AsyncClient]:
    async for c in _client(test_settings.model_copy(update={"sim_mode": False})):
        yield c


@pytest.fixture
async def client_redis_down(test_settings: Settings) -> AsyncIterator[httpx.AsyncClient]:
    """Redis points at a dead port: tests fallbacks and the circuit breaker."""
    settings = test_settings.model_copy(update={"redis_url": DEAD_REDIS_URL, "sim_mode": True})
    async for c in _client(settings):
        yield c


@pytest.fixture
def admin_headers(base_settings: Settings) -> dict[str, str]:
    return {"X-Admin-Key": base_settings.admin_key.get_secret_value()}


@pytest.fixture
def sim_headers(base_settings: Settings) -> dict[str, str]:
    return {"X-Sim-Key": base_settings.sim_telemetry_key.get_secret_value()}
