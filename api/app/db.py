"""Postgres: one asyncpg pool per worker, short timeouts, transaction helper.

Connection budget (also enforced at startup in config.Settings):
    workers x DB_POOL_MAX + headroom  <  max_connections - 3 reserved
    4 x 30 + 10 = 130 < 197
A saturated or dead database must turn into a fast 503, never a hang: acquire and connect both
have ~1 s timeouts, and errors.DB_UNAVAILABLE maps every such failure to SERVICE_UNAVAILABLE.
"""

import json
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import asyncpg

from app.config import Settings


async def _init_connection(conn: asyncpg.Connection) -> None:
    """Read and write json/jsonb columns as Python objects (idempotency, settings, run archive)."""
    for kind in ("json", "jsonb"):
        await conn.set_type_codec(kind, encoder=json.dumps, decoder=json.loads, schema="pg_catalog")


async def create_pool(settings: Settings) -> asyncpg.Pool:
    pool: asyncpg.Pool | None = await asyncpg.create_pool(
        settings.database_url.get_secret_value(),
        min_size=settings.db_pool_min,
        max_size=settings.db_pool_max,
        init=_init_connection,
        timeout=settings.db_connect_timeout_ms / 1000,
        command_timeout=settings.db_statement_timeout_ms / 1000 + 1,
        server_settings={
            "application_name": f"fairdrop-api-{os.getpid()}",
            "statement_timeout": str(settings.db_statement_timeout_ms),
            "idle_in_transaction_session_timeout": str(settings.db_idle_in_tx_timeout_ms),
        },
    )
    if pool is None:
        raise RuntimeError("asyncpg returned no pool")
    return pool


@asynccontextmanager
async def acquire(pool: asyncpg.Pool, timeout_s: float) -> AsyncIterator[asyncpg.Connection]:
    async with pool.acquire(timeout=timeout_s) as conn:
        yield conn


@asynccontextmanager
async def transaction(
    pool: asyncpg.Pool,
    *,
    acquire_timeout_s: float = 1.0,
    lock_timeout_ms: int | None = None,
    statement_timeout_ms: int | None = None,
) -> AsyncIterator[asyncpg.Connection]:
    """A transaction with optional per-transaction SET LOCAL timeouts (the claim uses tight ones).

    SET LOCAL ends with the transaction, so pooled connections never keep a tight timeout.
    """
    async with pool.acquire(timeout=acquire_timeout_s) as conn, conn.transaction():
        if lock_timeout_ms is not None:
            await conn.execute(f"SET LOCAL lock_timeout = {int(lock_timeout_ms)}")
        if statement_timeout_ms is not None:
            await conn.execute(f"SET LOCAL statement_timeout = {int(statement_timeout_ms)}")
        yield conn
