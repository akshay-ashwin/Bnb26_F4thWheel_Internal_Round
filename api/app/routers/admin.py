"""Admin endpoints (X-Admin-Key). Declarations are the frozen contract; bodies call the services."""

from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import StreamingResponse

from app.clock import server_time
from app.deps import pool_of, require_admin, settings_of
from app.errors import ErrorCode as E
from app.routers._common import errs
from app.schemas.admin import AbuseConfigIn, AbuseConfigOut, MetricsOut
from app.schemas.drops import (
    CreateDropIn,
    CreateDropOut,
    DrawProofOut,
    DropListItem,
    DropListOut,
    IntegrityOut,
    PhaseIn,
    PhaseOut,
    SimLatestOut,
)
from app.services import admin as admin_service
from app.services import draw, lifecycle

router = APIRouter(prefix="/admin", tags=["admin"], dependencies=[Depends(require_admin)])


@router.post(
    "/drops",
    response_model=CreateDropOut,
    status_code=201,
    operation_id="adminCreateDrop",
    responses=errs(E.UNAUTHENTICATED),
)
async def create_drop(body: CreateDropIn, request: Request) -> CreateDropOut:
    drop_id, commit = await lifecycle.create_drop(pool_of(request), body)
    return CreateDropOut(drop_id=drop_id, seed_commit=commit)


@router.post(
    "/drops/{drop_id}/phase",
    response_model=PhaseOut,
    operation_id="adminSetPhase",
    responses=errs(E.UNAUTHENTICATED, E.INVALID_TRANSITION, E.NOT_FOUND),
)
async def set_phase(drop_id: UUID, body: PhaseIn, request: Request) -> PhaseOut:
    phase = await lifecycle.apply_action(
        pool_of(request),
        request.app.state.cache,
        settings_of(request),
        request.app.state.metrics,
        drop_id,
        body.action,
        mode=body.mode,
    )
    return PhaseOut(phase=phase)  # type: ignore[arg-type]


@router.get(
    "/drops/{drop_id}/metrics",
    response_model=MetricsOut,
    operation_id="adminMetrics",
    responses=errs(E.UNAUTHENTICATED, E.NOT_FOUND),
)
async def metrics(
    drop_id: UUID, request: Request, window_s: int = Query(default=60, ge=1, le=3600)
) -> MetricsOut:
    return await admin_service.metrics(
        pool_of(request),
        request.app.state.cache,
        drop_id,
        window_s,
        request.app.state.metrics.dropped,
    )


@router.get(
    "/drops/{drop_id}/integrity",
    response_model=IntegrityOut,
    operation_id="adminIntegrity",
    responses=errs(E.UNAUTHENTICATED, E.NOT_FOUND),
)
async def integrity(drop_id: UUID, request: Request) -> IntegrityOut:
    """Computed by SQL from v_drop_integrity, never from counters."""
    return IntegrityOut(**await admin_service.integrity(pool_of(request), drop_id))


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
async def export(drop_id: UUID, request: Request) -> StreamingResponse:
    await admin_service.integrity(pool_of(request), drop_id)  # 404 before streaming starts
    return StreamingResponse(
        admin_service.export_rows(pool_of(request), drop_id),
        media_type="application/x-ndjson",
        headers={"X-Server-Time": server_time()},
    )


@router.get(
    "/drops/{drop_id}/draw-proof",
    response_model=DrawProofOut,
    operation_id="adminDrawProof",
    responses=errs(E.UNAUTHENTICATED, E.NOT_FOUND, E.INVALID_TRANSITION),
)
async def draw_proof(drop_id: UUID, request: Request) -> DrawProofOut:
    return DrawProofOut(**await draw.proof(pool_of(request), drop_id, public=False))


@router.put(
    "/abuse/config",
    response_model=AbuseConfigOut,
    operation_id="adminAbuseConfig",
    responses=errs(E.UNAUTHENTICATED),
)
async def abuse_config(body: AbuseConfigIn, request: Request) -> AbuseConfigOut:
    config = await admin_service.put_abuse_config(pool_of(request), request.app.state.cache, body)
    return AbuseConfigOut(**config)


# --- additions (docs/contract/additions.md) ---


@router.get(
    "/abuse/config",
    response_model=AbuseConfigOut,
    operation_id="adminGetAbuseConfig",
    responses=errs(E.UNAUTHENTICATED),
)
async def get_abuse_config(request: Request) -> AbuseConfigOut:
    return AbuseConfigOut(**await admin_service.get_abuse_config(pool_of(request)))


@router.get(
    "/drops",
    response_model=DropListOut,
    operation_id="adminListDrops",
    responses=errs(E.UNAUTHENTICATED),
)
async def list_drops(request: Request) -> DropListOut:
    """Lets the dashboard pick the drop without hard-coding ids."""
    items = await admin_service.list_drops(pool_of(request))
    return DropListOut(drops=[DropListItem(**i) for i in items])


@router.get(
    "/drops/{drop_id}/sim",
    response_model=SimLatestOut,
    operation_id="adminSimLatest",
    responses=errs(E.UNAUTHENTICATED),
)
async def sim_latest(drop_id: UUID, request: Request) -> SimLatestOut:
    """The latest simulator telemetry ("ground truth") for the dashboard. Display only."""
    return SimLatestOut(latest=await admin_service.latest_telemetry(request.app.state.cache))
