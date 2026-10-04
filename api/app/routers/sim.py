"""Simulator telemetry. Mounted only when SIM_MODE=true. Writes sim:* keys only (invariant 6)."""

from fastapi import APIRouter, Depends, Request

from app.deps import require_sim_key
from app.errors import ErrorCode as E
from app.routers._common import errs
from app.schemas.sim import TelemetryIn, TelemetryOut
from app.services import admin as admin_service

router = APIRouter(prefix="/sim", tags=["sim"], dependencies=[Depends(require_sim_key)])


@router.post(
    "/telemetry",
    response_model=TelemetryOut,
    operation_id="simTelemetry",
    responses=errs(E.UNAUTHENTICATED, E.NOT_FOUND),
)
async def telemetry(body: TelemetryIn, request: Request) -> TelemetryOut:
    await admin_service.record_telemetry(request.app.state.cache, body)
    return TelemetryOut()
