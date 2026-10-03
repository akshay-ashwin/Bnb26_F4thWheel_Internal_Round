"""Postgres pool and transaction helper. One pool per worker; no connection per request."""

from __future__ import annotations

import json
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import asyncpg

from app.config import Settings


async def _init_connection(conn: asyncpg.Connection) -> None:
    """Read and write json/jsonb as Python objects."""
    for kind in ("json", "jsonb"):
        await conn.set_type_codec(kind, encoder=json.dumps, decoder=json.loads, schema="pg_catalog")


async def create_pool(settings: Settings) -> asyncpg.Pool:
    pool: asyncpg.Pool = await asyncpg.create_pool(
        settings.database_url,
        min_size=settings.db_pool_min,
        max_size=settings.db_pool_max,
        init=_init_connection,
        server_settings={
            "application_name": f"fairdrop-api-{os.getpid()}",
            "statement_timeout": "5000",
            "idle_in_transaction_session_timeout": "10000",
        },
    )
    return pool


@asynccontextmanager
async def transaction(
    pool: asyncpg.Pool,
    *,
    lock_timeout_ms: int | None = None,
    statement_timeout_ms: int | None = None,
    acquire_timeout_s: float = 3.0,
) -> AsyncIterator[asyncpg.Connection]:
    """Acquire a connection and run one READ COMMITTED transaction.

    Per-transaction timeouts use SET LOCAL, so they never leak to the next user of the connection.
    A pool that cannot hand out a connection in `acquire_timeout_s` raises asyncio.TimeoutError,
    which the error handlers turn into 503 SERVICE_UNAVAILABLE.
    """
    async with pool.acquire(timeout=acquire_timeout_s) as conn, conn.transaction():
        if lock_timeout_ms is not None:
            await conn.execute(f"SET LOCAL lock_timeout = {int(lock_timeout_ms)}")
        if statement_timeout_ms is not None:
            await conn.execute(f"SET LOCAL statement_timeout = {int(statement_timeout_ms)}")
        yield conn
