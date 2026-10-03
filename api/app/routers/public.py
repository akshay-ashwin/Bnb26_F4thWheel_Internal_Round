"""Public endpoints (docs/contract/README.md). Routers stay thin: parse, call a service, shape."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Request, Response
from fastapi.responses import JSONResponse

from app import netutil
from app.deps import (
    COOKIE_NAME,
    Session,
    client_ip,
    current_session,
    parse_idempotency_key,
    pool_of,
    require_idempotency_key,
    settings_of,
)
from app.errors import now_iso
from app.schemas import (
    ClaimIn,
    ClaimOut,
    DrawProofOut,
    DropOut,
    EntryOut,
    MeOut,
    OtpRequestIn,
    OtpRequestOut,
    OtpVerifyIn,
    OtpVerifyOut,
    StepUpIn,
    StepUpOut,
)
from app.services import auth, claim, draw, drops, entries, idempotency, stepup, tokens

router = APIRouter(prefix="/api", tags=["public"])


@router.post("/auth/otp/request", response_model=OtpRequestOut)
async def otp_request(body: OtpRequestIn, request: Request) -> OtpRequestOut:
    result = await auth.request_otp(
        settings_of(request),
        request.app.state.cache,
        request.app.state.sms,
        phone=body.phone,
        device_id=body.device_id,
        ip=client_ip(request),
    )
    return OtpRequestOut(
        request_id=result.request_id, expires_in_s=result.expires_in_s, dev_otp=result.dev_otp
    )


@router.post("/auth/otp/verify", response_model=OtpVerifyOut)
async def otp_verify(body: OtpVerifyIn, request: Request, response: Response) -> OtpVerifyOut:
    settings = settings_of(request)
    verified = await auth.verify_otp(
        settings,
        request.app.state.cache,
        pool_of(request),
        request_id=body.request_id,
        otp=body.otp,
        device_id=body.device_id,
        ip=client_ip(request),
        ua_hash=netutil.ua_hash(request),
    )
    response.set_cookie(
        COOKIE_NAME,
        verified.session_token,
        max_age=settings.session_ttl_s,
        httponly=True,
        samesite="lax",
        secure=settings.cookie_secure,
        path="/api",
        domain=settings.cookie_domain,
    )
    return OtpVerifyOut(
        session_token=verified.session_token, user_public_id=verified.user_public_id
    )


# --- drops, entries, me ---


def _replay(status_code: int, payload: dict[str, Any]) -> JSONResponse:
    """Success response with a fresh `server_time` (the stored payload never contains one)."""
    return JSONResponse({**payload, "server_time": now_iso()}, status_code=status_code)


@router.get("/drops/{drop_id}", response_model=DropOut, response_model_exclude_none=False)
async def get_drop(drop_id: str, request: Request) -> DropOut:
    return await drops.get_drop(pool_of(request), drops.parse_drop_id(drop_id))


@router.post("/drops/{drop_id}/entries", response_model=EntryOut, status_code=201)
async def create_entry(
    drop_id: str, request: Request, session: Session = Depends(current_session)
) -> JSONResponse:
    parsed = drops.parse_drop_id(drop_id)
    key = parse_idempotency_key(request, required=False)
    result = await entries.register_entry(
        pool_of(request),
        settings_of(request),
        request.app.state.metrics,
        session,
        parsed,
        ip=client_ip(request),
        ua_hash=netutil.ua_hash(request),
        key=key,
        req_hash=idempotency.request_hash(
            "POST", "/api/drops/{id}/entries", {"drop_id": str(parsed)}
        ),
    )
    return _replay(result.status_code, result.payload)


@router.get("/drops/{drop_id}/me", response_model=MeOut)
async def get_me(
    drop_id: str, request: Request, session: Session = Depends(current_session)
) -> MeOut:
    return await entries.build_me(
        pool_of(request),
        request.app.state.cache,
        settings_of(request),
        session,
        drops.parse_drop_id(drop_id),
        mint_token=lambda row, sess: tokens.mint_for_me(settings_of(request), row, sess),
        dev_otp_for=lambda row, sess: _dev_otp(request, row, sess),
    )


async def _dev_otp(request: Request, row: Any, session: Session) -> str | None:
    """STEP_UP_REQUIRED entries get a challenge on first `/me`; SIM_MODE shows the code."""
    if row["status"] != "STEP_UP_REQUIRED" or row["offer_expires_at"] is None:
        return None
    state = request.app.state
    return await stepup.ensure_challenge(
        pool_of(request),
        state.cache,
        settings_of(request),
        state.sms,
        state.metrics,
        entry_id=row["entry_id"],
        user_id=session.user_id,
        offer_expires_at=row["offer_expires_at"],
    )


@router.post("/drops/{drop_id}/claim", response_model=ClaimOut)
async def claim_seat(
    drop_id: str, body: ClaimIn, request: Request, session: Session = Depends(current_session)
) -> JSONResponse:
    key = require_idempotency_key(request)
    result = await claim.claim(
        pool_of(request),
        settings_of(request),
        request.app.state.metrics,
        session,
        drops.parse_drop_id(drop_id),
        token=body.admission_token,
        key=key,
        tasks=request.app.state.tasks,
    )
    return _replay(result.status_code, result.payload)


@router.get("/drops/{drop_id}/draw-proof", response_model=DrawProofOut)
async def public_draw_proof(drop_id: str, request: Request) -> DrawProofOut:
    """Contract addition: the same proof as the admin endpoint plus the sorted eligible public ids
    and the ranked order, so anyone can re-run the draw. Only available after the draw."""
    return DrawProofOut(
        **await draw.proof(pool_of(request), drops.parse_drop_id(drop_id), public=True)
    )


@router.post("/drops/{drop_id}/step-up", response_model=StepUpOut)
async def step_up(
    drop_id: str, body: StepUpIn, request: Request, session: Session = Depends(current_session)
) -> JSONResponse:
    key = require_idempotency_key(request)
    state = request.app.state
    result = await stepup.verify_step_up(
        pool_of(request),
        state.cache,
        settings_of(request),
        state.sms,
        state.metrics,
        session,
        drops.parse_drop_id(drop_id),
        otp=body.otp,
        key=key,
    )
    return _replay(result.status_code, result.payload)
