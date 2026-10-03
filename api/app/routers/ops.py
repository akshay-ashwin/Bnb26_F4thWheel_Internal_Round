"""Ops endpoints: liveness and readiness."""

from __future__ import annotations

from fastapi import APIRouter, Request

from app.schemas import HealthOut, ReadyOut

router = APIRouter(prefix="/api", tags=["ops"])


@router.get("/healthz", response_model=HealthOut)
async def healthz() -> HealthOut:
    """Process is alive. Deliberately touches neither Postgres nor Redis."""
    return HealthOut()


@router.get("/readyz", response_model=ReadyOut)
async def readyz(request: Request) -> ReadyOut:
    """Postgres and Redis reachability. Redis down is "degraded", not "unready"."""
    state = request.app.state
    postgres = False
    try:
        async with state.pool.acquire(timeout=1.0) as conn:
            postgres = (await conn.fetchval("SELECT 1")) == 1
    except Exception:
        postgres = False
    redis_ok = await state.cache.ping()
    status = "ok" if postgres and redis_ok else ("degraded" if postgres else "unready")
    return ReadyOut(status=status, postgres=postgres, redis=redis_ok)
