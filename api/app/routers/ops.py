from fastapi import APIRouter, Response

from app.deps import CacheDep, PoolDep, SettingsDep
from app.errors import ErrorCode, error_response
from app.schemas.ops import HealthOut, ReadyOut

router = APIRouter(tags=["ops"])


@router.get("/healthz", response_model=HealthOut, operation_id="healthz")
async def healthz() -> HealthOut:
    """Process is alive. Touches nothing."""
    return HealthOut()


@router.get(
    "/readyz",
    response_model=ReadyOut,
    operation_id="readyz",
    responses={503: {"description": "Postgres unreachable (error envelope with details)."}},
)
async def readyz(pool: PoolDep, cache: CacheDep, settings: SettingsDep) -> Response | ReadyOut:
    """Postgres must answer; Redis down is reported as degraded, not unready."""
    redis_up = await cache.ping()
    try:
        async with pool.acquire(timeout=settings.db_acquire_timeout_ms / 1000) as conn:
            await conn.fetchval("SELECT 1", timeout=0.5)
    except Exception:
        return error_response(
            503,
            ErrorCode.SERVICE_UNAVAILABLE,
            "Postgres is not reachable",
            retry_after_ms=1000,
            details={"postgres": "down", "redis": "up" if redis_up else "down"},
        )
    return ReadyOut(
        status="ready" if redis_up else "degraded",
        postgres="up",
        redis="up" if redis_up else "down",
    )
