"""Admin endpoints (X-Admin-Key). Stubs until Plans 06-14."""

from uuid import UUID

from fastapi import APIRouter, Depends, Query
from fastapi.responses import StreamingResponse

from app.deps import require_admin
from app.errors import ErrorCode as E
from app.errors import NotImplementedYet
from app.routers._common import errs
from app.schemas.admin import AbuseConfigIn, AbuseConfigOut, MetricsOut
from app.schemas.drops import (
    CreateDropIn,
    CreateDropOut,
    DrawProofOut,
    IntegrityOut,
    PhaseIn,
    PhaseOut,
)

router = APIRouter(prefix="/admin", tags=["admin"], dependencies=[Depends(require_admin)])


@router.post(
    "/drops",
    response_model=CreateDropOut,
    status_code=201,
    operation_id="adminCreateDrop",
    responses=errs(E.UNAUTHENTICATED),
)
async def create_drop(body: CreateDropIn) -> CreateDropOut:
    raise NotImplementedYet()


@router.post(
    "/drops/{drop_id}/phase",
    response_model=PhaseOut,
    operation_id="adminSetPhase",
    responses=errs(E.UNAUTHENTICATED, E.INVALID_TRANSITION, E.NOT_FOUND),
)
async def set_phase(drop_id: UUID, body: PhaseIn) -> PhaseOut:
    raise NotImplementedYet()


@router.get(
    "/drops/{drop_id}/metrics",
    response_model=MetricsOut,
    operation_id="adminMetrics",
    responses=errs(E.UNAUTHENTICATED, E.NOT_FOUND),
)
async def metrics(drop_id: UUID, window_s: int = Query(default=60, ge=1, le=3600)) -> MetricsOut:
    raise NotImplementedYet()


@router.get(
    "/drops/{drop_id}/integrity",
    response_model=IntegrityOut,
    operation_id="adminIntegrity",
    responses=errs(E.UNAUTHENTICATED, E.NOT_FOUND),
)
async def integrity(drop_id: UUID) -> IntegrityOut:
    """Computed by SQL from v_drop_integrity, never from counters."""
    raise NotImplementedYet()


@router.get(
    "/drops/{drop_id}/export",
    operation_id="adminExport",
    response_class=StreamingResponse,
    responses={
        200: {
            "description": "NDJSON, one row per entry. server_time is in the X-Server-Time header.",
            "content": {"application/x-ndjson": {"schema": {"type": "string"}}},
            "headers": {"X-Server-Time": {"schema": {"type": "string"}}},
        },
        **errs(E.UNAUTHENTICATED, E.NOT_FOUND),
    },
)
async def export(drop_id: UUID) -> StreamingResponse:
    raise NotImplementedYet()


@router.get(
    "/drops/{drop_id}/draw-proof",
    response_model=DrawProofOut,
    operation_id="adminDrawProof",
    responses=errs(E.UNAUTHENTICATED, E.NOT_FOUND, E.INVALID_TRANSITION),
)
async def draw_proof(drop_id: UUID) -> DrawProofOut:
    raise NotImplementedYet()


@router.put(
    "/abuse/config",
    response_model=AbuseConfigOut,
    operation_id="adminAbuseConfig",
    responses=errs(E.UNAUTHENTICATED),
)
async def abuse_config(body: AbuseConfigIn) -> AbuseConfigOut:
    raise NotImplementedYet()
