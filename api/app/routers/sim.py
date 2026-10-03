"""Simulator telemetry (mounted only when SIM_MODE=true). The backend never reads ground-truth
labels in any decision path; this endpoint only stores them for the dashboard."""

from __future__ import annotations

from fastapi import APIRouter, Header, Request

from app.deps import settings_of
from app.errors import AppError
from app.schemas import EmptyOut, TelemetryIn
from app.services import admin as admin_service

router = APIRouter(prefix="/api/sim", tags=["sim"])


@router.post("/telemetry", response_model=EmptyOut)
async def telemetry(
    body: TelemetryIn, request: Request, x_sim_key: str = Header(default="")
) -> EmptyOut:
    if not admin_service.check_sim_key(settings_of(request), x_sim_key):
        raise AppError("UNAUTHENTICATED", "Telemetry key required")
    await admin_service.record_telemetry(request.app.state.cache, body)
    return EmptyOut()
