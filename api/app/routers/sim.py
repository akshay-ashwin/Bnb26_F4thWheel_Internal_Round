"""Simulator telemetry. Mounted only when SIM_MODE=true. Writes sim:* keys only (invariant 6)."""

from fastapi import APIRouter, Depends

from app.deps import require_sim_key
from app.errors import ErrorCode as E
from app.errors import NotImplementedYet
from app.routers._common import errs
from app.schemas.sim import TelemetryIn, TelemetryOut

router = APIRouter(prefix="/sim", tags=["sim"], dependencies=[Depends(require_sim_key)])


@router.post(
    "/telemetry",
    response_model=TelemetryOut,
    operation_id="simTelemetry",
    responses=errs(E.UNAUTHENTICATED, E.NOT_FOUND),
)
async def telemetry(body: TelemetryIn) -> TelemetryOut:
    raise NotImplementedYet()
