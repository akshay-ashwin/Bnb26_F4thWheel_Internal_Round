"""Public endpoints. Declarations (shapes, error codes) are the frozen contract; the bodies call
the services. Routers stay thin: parse, call a service, shape the response."""

from typing import Any
from uuid import UUID

from fastapi import APIRouter, Request, Response
from fastapi.responses import JSONResponse

from app import netutil
from app.clock import server_time
from app.deps import (
    IdempotencyUuid,
    SessionDep,
    client_ip,
    optional_idempotency_uuid,
    pool_of,
    settings_of,
)
from app.errors import ErrorCode as E
from app.routers._common import errs
from app.schemas.auth import OtpRequestIn, OtpRequestOut, OtpVerifyIn, OtpVerifyOut
from app.schemas.claim import ClaimIn, ClaimOut
from app.schemas.drops import DrawProofOut, DropOut
from app.schemas.entries import EntryIn, EntryOut
from app.schemas.me import MeOut
from app.schemas.stepup import StepUpIn, StepUpOut
from app.services import auth, claim, draw, drops, entries, idempotency, stepup, tokens

router = APIRouter(tags=["public"])

COOKIE_NAME = "fd_session"


def _replay(status_code: int, payload: dict[str, Any]) -> JSONResponse:
    """A success response with a fresh `server_time` (stored payloads never contain one)."""
    return JSONResponse({**payload, "server_time": server_time()}, status_code=status_code)


@router.get(
    "/drops/{drop_id}",
    response_model=DropOut,
    operation_id="getDrop",
    responses=errs(E.NOT_FOUND),
)
async def get_drop(drop_id: UUID, request: Request) -> DropOut:
    """Public drop state. Read-only; cacheable for 1 s."""
    return await drops.get_drop(pool_of(request), drop_id)


@router.post(
    "/auth/otp/request",
    response_model=OtpRequestOut,
    operation_id="requestOtp",
    responses=errs(E.OTP_THROTTLED, E.INVALID_PHONE, E.RATE_LIMITED),
)
async def request_otp(body: OtpRequestIn, request: Request) -> OtpRequestOut:
    """Same phone within 30 s returns the same request_id. dev_otp only when SIM_MODE."""
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


@router.post(
    "/auth/otp/verify",
    response_model=OtpVerifyOut,
    operation_id="verifyOtp",
    responses=errs(E.OTP_INVALID, E.OTP_EXPIRED, E.RATE_LIMITED),
)
async def verify_otp(body: OtpVerifyIn, request: Request, response: Response) -> OtpVerifyOut:
    """Sets the fd_session cookie. Re-verify returns the existing session for the same device."""
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
        domain=settings.cookie_domain or None,
    )
    return OtpVerifyOut(
        session_token=verified.session_token, user_public_id=verified.user_public_id
    )


@router.post(
    "/drops/{drop_id}/entries",
    response_model=EntryOut,
    status_code=201,
    operation_id="createEntry",
    responses={
        200: {"model": EntryOut, "description": "Repeat call: same body as the 201."},
        **errs(E.WINDOW_CLOSED, E.WINDOW_NOT_OPEN, E.UNAUTHENTICATED, E.RATE_LIMITED),
    },
)
async def create_entry(
    drop_id: UUID, body: EntryIn, request: Request, session: SessionDep
) -> JSONResponse:
    """Idempotent by UNIQUE(drop, user). Idempotency-Key is optional here."""
    result = await entries.register_entry(
        pool_of(request),
        settings_of(request),
        request.app.state.metrics,
        session,
        drop_id,
        ip=client_ip(request),
        ua_hash=netutil.ua_hash(request),
        key=optional_idempotency_uuid(request),
        req_hash=idempotency.request_hash(
            "POST", "/api/drops/{id}/entries", {"drop_id": str(drop_id)}
        ),
    )
    return _replay(result.status_code, result.payload)


@router.get(
    "/drops/{drop_id}/me",
    response_model=MeOut,
    operation_id="getMe",
    responses=errs(E.UNAUTHENTICATED, E.RATE_LIMITED),
)
async def get_me(drop_id: UUID, request: Request, session: SessionDep) -> MeOut:
    """The UI's single source of truth. Read-only."""
    settings = settings_of(request)
    state = request.app.state

    async def dev_otp(row: Any, sess: Any) -> str | None:
        """STEP_UP_REQUIRED entries get a challenge on first `/me`; SIM_MODE shows the code."""
        if row["status"] != "STEP_UP_REQUIRED" or row["offer_expires_at"] is None:
            return None
        return await stepup.ensure_challenge(
            pool_of(request),
            state.cache,
            settings,
            state.sms,
            state.metrics,
            entry_id=row["entry_id"],
            user_id=sess.user_id,
            offer_expires_at=row["offer_expires_at"],
        )

    return await entries.build_me(
        pool_of(request),
        state.cache,
        settings,
        session,
        drop_id,
        mint_token=lambda row, sess: tokens.mint_for_me(settings, row, sess),
        dev_otp_for=dev_otp,
    )


@router.post(
    "/drops/{drop_id}/claim",
    response_model=ClaimOut,
    operation_id="claim",
    summary="Claim",
    responses=errs(
        E.TOKEN_INVALID,
        E.NOT_OFFERED,
        E.OFFER_EXPIRED,
        E.SOLD_OUT,
        E.STEP_UP_REQUIRED,
        E.IDEMPOTENCY_KEY_REUSED,
        E.IDEMPOTENCY_KEY_MISSING,
        E.RATE_LIMITED,
    ),
)
async def claim_seat(
    drop_id: UUID, body: ClaimIn, request: Request, session: SessionDep, key: IdempotencyUuid
) -> JSONResponse:
    """Requires Idempotency-Key. Same key or same entry returns the same 200."""
    result = await claim.claim(
        pool_of(request),
        settings_of(request),
        request.app.state.metrics,
        session,
        drop_id,
        token=body.admission_token,
        key=key,
        tasks=request.app.state.tasks,
    )
    return _replay(result.status_code, result.payload)


@router.post(
    "/drops/{drop_id}/step-up",
    response_model=StepUpOut,
    operation_id="stepUp",
    responses=errs(E.OTP_INVALID, E.OFFER_EXPIRED, E.IDEMPOTENCY_KEY_MISSING),
)
async def step_up(
    drop_id: UUID, body: StepUpIn, request: Request, session: SessionDep, key: IdempotencyUuid
) -> JSONResponse:
    """Requires Idempotency-Key. A repeat after success returns 200."""
    state = request.app.state
    result = await stepup.verify_step_up(
        pool_of(request),
        state.cache,
        settings_of(request),
        state.sms,
        state.metrics,
        session,
        drop_id,
        otp=body.otp,
        key=key,
    )
    return _replay(result.status_code, result.payload)


@router.get(
    "/drops/{drop_id}/draw-proof",
    response_model=DrawProofOut,
    operation_id="publicDrawProof",
    responses=errs(E.NOT_FOUND, E.INVALID_TRANSITION),
)
async def public_draw_proof(drop_id: UUID, request: Request) -> DrawProofOut:
    """Addition: the proof plus the sorted eligible public ids and the ranked order, so anyone can
    re-run the draw (docs/contract/draw.md). Only after the draw."""
    return DrawProofOut(**await draw.proof(pool_of(request), drop_id, public=True))
