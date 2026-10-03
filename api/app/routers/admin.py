"""Admin endpoints (X-Admin-Key). Exempt from the limiter (see abuse.limiter.SKIPPED_PREFIXES)."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import StreamingResponse

from app.deps import pool_of, require_admin, settings_of
from app.errors import now_iso
from app.schemas import (
    AbuseConfigIn,
    AbuseConfigOut,
    DrawProofOut,
    DropCreateIn,
    DropCreateOut,
    DropListItem,
    DropListOut,
    IntegrityOut,
    MetricsOut,
    PhaseIn,
    PhaseOut,
    SimLatestOut,
)
from app.services import admin as admin_service
from app.services import draw, drops, lifecycle

router = APIRouter(prefix="/api/admin", tags=["admin"], dependencies=[Depends(require_admin)])


def _id(raw: str) -> uuid.UUID:
    return drops.parse_drop_id(raw)


@router.post("/drops", response_model=DropCreateOut, status_code=201)
async def create_drop(body: DropCreateIn, request: Request) -> DropCreateOut:
    drop_id, commit = await lifecycle.create_drop(pool_of(request), body)
    return DropCreateOut(drop_id=str(drop_id), seed_commit=commit)


@router.post("/drops/{drop_id}/phase", response_model=PhaseOut)
async def phase(drop_id: str, body: PhaseIn, request: Request) -> PhaseOut:
    new_phase = await lifecycle.apply_action(
        pool_of(request),
        request.app.state.cache,
        settings_of(request),
        request.app.state.metrics,
        _id(drop_id),
        body.action,
        mode=body.mode,
    )
    return PhaseOut(phase=new_phase)  # type: ignore[arg-type]


@router.get("/drops/{drop_id}/draw-proof", response_model=DrawProofOut)
async def draw_proof(drop_id: str, request: Request) -> DrawProofOut:
    return DrawProofOut(**await draw.proof(pool_of(request), _id(drop_id), public=False))


# --- read models ---


@router.get("/drops", response_model=DropListOut)
async def list_drops(request: Request) -> DropListOut:
    """Addition (small): lets the dashboard pick the drop without hard-coding ids."""
    items = await admin_service.list_drops(pool_of(request))
    return DropListOut(drops=[DropListItem(**i) for i in items])


@router.get("/drops/{drop_id}/integrity", response_model=IntegrityOut)
async def integrity(drop_id: str, request: Request) -> IntegrityOut:
    return IntegrityOut(**await admin_service.integrity(pool_of(request), _id(drop_id)))


@router.get("/drops/{drop_id}/metrics", response_model=MetricsOut)
async def metrics(
    drop_id: str, request: Request, window_s: int = Query(default=60, ge=1, le=3600)
) -> MetricsOut:
    return await admin_service.metrics(
        pool_of(request),
        request.app.state.cache,
        _id(drop_id),
        window_s,
        request.app.state.metrics.dropped,
    )


@router.get("/drops/{drop_id}/export")
async def export(drop_id: str, request: Request) -> StreamingResponse:
    parsed = _id(drop_id)
    await admin_service.integrity(pool_of(request), parsed)  # 404 before streaming starts
    return StreamingResponse(
        admin_service.export_rows(pool_of(request), parsed),
        media_type="application/x-ndjson",
        headers={"X-Server-Time": now_iso()},
    )


@router.get("/drops/{drop_id}/sim", response_model=SimLatestOut)
async def sim_latest(drop_id: str, request: Request) -> SimLatestOut:
    """Addition: the latest simulator telemetry ("ground truth") for the dashboard. Display only."""
    _id(drop_id)
    return SimLatestOut(latest=await admin_service.latest_telemetry(request.app.state.cache))


@router.put("/abuse/config", response_model=AbuseConfigOut)
async def put_abuse_config(body: AbuseConfigIn, request: Request) -> AbuseConfigOut:
    config = await admin_service.put_abuse_config(pool_of(request), request.app.state.cache, body)
    return AbuseConfigOut(**config)


@router.get("/abuse/config", response_model=AbuseConfigOut)
async def get_abuse_config(request: Request) -> AbuseConfigOut:
    return AbuseConfigOut(**await admin_service.get_abuse_config(pool_of(request)))
