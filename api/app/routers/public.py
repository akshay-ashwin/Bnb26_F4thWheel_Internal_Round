"""Public endpoints. Stubs until Plans 04-11; shapes and error codes are the frozen contract."""

from uuid import UUID

from fastapi import APIRouter, Depends

from app.deps import IdempotencyKey, session_credential
from app.errors import ErrorCode as E
from app.errors import NotImplementedYet
from app.routers._common import errs
from app.schemas.auth import OtpRequestIn, OtpRequestOut, OtpVerifyIn, OtpVerifyOut
from app.schemas.claim import ClaimIn, ClaimOut
from app.schemas.drops import DropOut
from app.schemas.entries import EntryIn, EntryOut
from app.schemas.me import MeOut
from app.schemas.stepup import StepUpIn, StepUpOut

router = APIRouter(tags=["public"])
_session = [Depends(session_credential)]


@router.get(
    "/drops/{drop_id}",
    response_model=DropOut,
    operation_id="getDrop",
    responses=errs(E.NOT_FOUND),
)
async def get_drop(drop_id: UUID) -> DropOut:
    """Public drop state. Read-only; cacheable for 1 s."""
    raise NotImplementedYet()


@router.post(
    "/auth/otp/request",
    response_model=OtpRequestOut,
    operation_id="requestOtp",
    responses=errs(E.OTP_THROTTLED, E.INVALID_PHONE, E.RATE_LIMITED),
)
async def request_otp(body: OtpRequestIn) -> OtpRequestOut:
    """Same phone within 30 s returns the same request_id. dev_otp only when SIM_MODE."""
    raise NotImplementedYet()


@router.post(
    "/auth/otp/verify",
    response_model=OtpVerifyOut,
    operation_id="verifyOtp",
    responses=errs(E.OTP_INVALID, E.OTP_EXPIRED, E.RATE_LIMITED),
)
async def verify_otp(body: OtpVerifyIn) -> OtpVerifyOut:
    """Sets the fd_session cookie. Re-verify returns the existing session for the same device."""
    raise NotImplementedYet()


@router.post(
    "/drops/{drop_id}/entries",
    response_model=EntryOut,
    status_code=201,
    operation_id="createEntry",
    dependencies=_session,
    responses={
        200: {"model": EntryOut, "description": "Repeat call: same body as the 201."},
        **errs(E.WINDOW_CLOSED, E.WINDOW_NOT_OPEN, E.UNAUTHENTICATED, E.RATE_LIMITED),
    },
)
async def create_entry(drop_id: UUID, body: EntryIn) -> EntryOut:
    """Idempotent by UNIQUE(drop, user). Idempotency-Key is optional here."""
    raise NotImplementedYet()


@router.get(
    "/drops/{drop_id}/me",
    response_model=MeOut,
    operation_id="getMe",
    dependencies=_session,
    responses=errs(E.UNAUTHENTICATED, E.RATE_LIMITED),
)
async def get_me(drop_id: UUID) -> MeOut:
    """The UI's single source of truth. Read-only."""
    raise NotImplementedYet()


@router.post(
    "/drops/{drop_id}/claim",
    response_model=ClaimOut,
    operation_id="claim",
    dependencies=_session,
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
async def claim(drop_id: UUID, body: ClaimIn, idempotency_key: IdempotencyKey) -> ClaimOut:
    """Requires Idempotency-Key. Same key or same entry returns the same 200."""
    raise NotImplementedYet()


@router.post(
    "/drops/{drop_id}/step-up",
    response_model=StepUpOut,
    operation_id="stepUp",
    dependencies=_session,
    responses=errs(E.OTP_INVALID, E.OFFER_EXPIRED, E.IDEMPOTENCY_KEY_MISSING),
)
async def step_up(drop_id: UUID, body: StepUpIn, idempotency_key: IdempotencyKey) -> StepUpOut:
    """Requires Idempotency-Key. A repeat after success returns 200."""
    raise NotImplementedYet()
