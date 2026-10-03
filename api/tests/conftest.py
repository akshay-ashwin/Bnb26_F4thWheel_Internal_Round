"""Database test fixtures.

Tests run against a real PostgreSQL whose name ends in `_test` (they truncate every table, so any
other name is refused). `uv run fd test-api` migrates that database with the same dbmate files as
the real one before pytest starts; nothing here creates schema.

Two connections are used on purpose:
  * the owner role (TEST_DATABASE_URL, a superuser) for fixtures and fault injection;
  * the restricted application role (TEST_APP_DATABASE_URL), exactly what the api will use.
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator
from urllib.parse import urlparse

import asyncpg
import pytest

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
