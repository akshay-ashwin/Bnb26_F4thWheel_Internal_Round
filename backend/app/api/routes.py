from fastapi import APIRouter, Depends, Header, Response
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import AuthContext, current_auth, current_user, require_admin
from app.db.redis import redis_client
from app.db.session import get_db
from app.models import User
from app.schemas.auth import OtpRequest, OtpRequestResponse, OtpVerify, OtpVerifyResponse
from app.schemas.drops import DropOut, EntryOut, IntegrityOut, MeOut
from app.services import auth as auth_svc
from app.services import claim as claim_svc
from app.services import drops as drops_svc
from app.config import get_settings

router = APIRouter()


@router.get("/health")
async def health(db: AsyncSession = Depends(get_db)) -> JSONResponse:
    checks = {}
    try:
        await db.execute(text("SELECT 1"))
        checks["postgres"] = "ok"
    except Exception:
        checks["postgres"] = "down"
    try:
        await redis_client.ping()
        checks["redis"] = "ok"
    except Exception:
        checks["redis"] = "down"
    healthy = checks["postgres"] == "ok"  # redis is only connected/checked here, not required yet
    return JSONResponse({"status": "ok" if healthy else "unhealthy", **checks}, status_code=200 if healthy else 503)


@router.post("/api/auth/otp/request", response_model=OtpRequestResponse)
async def otp_request(body: OtpRequest, db: AsyncSession = Depends(get_db)) -> OtpRequestResponse:
    code, ttl = await auth_svc.request_otp(db, body.phone)
    return OtpRequestResponse(
        message="SIM_MODE: no SMS sent; use demo_otp." if get_settings().sim_mode else "OTP sent.",
        expires_in_seconds=ttl, demo_otp=code if get_settings().sim_mode else None,
    )


@router.post("/api/auth/otp/verify", response_model=OtpVerifyResponse)
async def otp_verify(body: OtpVerify, db: AsyncSession = Depends(get_db)) -> OtpVerifyResponse:
    token, expires, public_id = await auth_svc.verify_otp(db, body.phone, body.otp)
    return OtpVerifyResponse(session_token=token, expires_at=expires, user_public_id=public_id)


@router.get("/api/drops/{drop_id}", response_model=DropOut)
async def get_drop(drop_id: int, db: AsyncSession = Depends(get_db)) -> DropOut:
    return await drops_svc.drop_view(db, drop_id)


@router.post("/api/drops/{drop_id}/entries", response_model=EntryOut)
async def create_entry(
    drop_id: int, response: Response, user: User = Depends(current_user), db: AsyncSession = Depends(get_db)
) -> EntryOut:
    entry, created = await drops_svc.create_entry(db, drop_id, user)
    response.status_code = 201 if created else 200
    return entry


@router.get("/api/drops/{drop_id}/me", response_model=MeOut)
async def get_me(drop_id: int, user: User = Depends(current_user), db: AsyncSession = Depends(get_db)) -> MeOut:
    return await drops_svc.me(db, drop_id, user)


@router.post("/api/drops/{drop_id}/claim")
async def claim(
    drop_id: int,
    auth: AuthContext = Depends(current_auth),
    db: AsyncSession = Depends(get_db),
    idempotency_key: str | None = Header(default=None, max_length=200),
    admission_token: str | None = Header(default=None, alias="X-Admission-Token", max_length=2000),
) -> JSONResponse:
    status, body = await claim_svc.claim(
        db, drop_id, auth.user, idempotency_key, session_id=auth.session_id, admission_token=admission_token
    )
    return JSONResponse(body, status_code=status)


@router.get("/api/admin/drops/{drop_id}/integrity", response_model=IntegrityOut, dependencies=[Depends(require_admin)])
async def integrity(drop_id: int, db: AsyncSession = Depends(get_db)) -> IntegrityOut:
    return await drops_svc.integrity(db, drop_id)
