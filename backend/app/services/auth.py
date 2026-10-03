from datetime import UTC, datetime, timedelta

from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.models import OtpCode, Session, User
from app.utils.errors import ApiError
from app.utils.security import keyed_hash, new_otp, new_public_id, new_token, normalize_phone


def _phone_hash(phone: str) -> str:
    norm = normalize_phone(phone)
    if norm is None:
        raise ApiError(422, "INVALID_PHONE", "Phone number is not valid.")
    return keyed_hash("phone:" + norm)


async def request_otp(db: AsyncSession, phone: str) -> tuple[str | None, int]:
    """SIM_MODE only: no SMS is sent. The OTP is returned to the caller for local testing."""
    settings = get_settings()
    if not settings.sim_mode:
        raise ApiError(501, "SMS_NOT_CONFIGURED", "No SMS provider configured; enable SIM_MODE for demos.")
    ph = _phone_hash(phone)
    code = new_otp()
    db.add(
        OtpCode(
            phone_hash=ph,
            code_hash=keyed_hash(f"otp:{ph}:{code}"),
            expires_at=datetime.now(UTC) + timedelta(seconds=settings.otp_ttl_seconds),
        )
    )
    await db.commit()
    return code, settings.otp_ttl_seconds


async def verify_otp(db: AsyncSession, phone: str, otp: str) -> tuple[str, datetime, str]:
    settings = get_settings()
    ph = _phone_hash(phone)
    now = datetime.now(UTC)
    rec = (
        await db.execute(
            select(OtpCode)
            .where(OtpCode.phone_hash == ph, OtpCode.consumed_at.is_(None), OtpCode.expires_at > now)
            .order_by(OtpCode.id.desc())
            .limit(1)
            .with_for_update()
        )
    ).scalar_one_or_none()
    bad = ApiError(401, "INVALID_OTP", "OTP is invalid or expired.")
    if rec is None or rec.attempts >= settings.otp_max_attempts:
        raise bad
    if rec.code_hash != keyed_hash(f"otp:{ph}:{otp}"):
        await db.execute(update(OtpCode).where(OtpCode.id == rec.id).values(attempts=OtpCode.attempts + 1))
        await db.commit()
        raise bad
    rec.consumed_at = now

    # get-or-create user (unique phone_hash is the final guard against races)
    await db.execute(insert(User).values(public_id=new_public_id(), phone_hash=ph).on_conflict_do_nothing())
    user = (await db.execute(select(User).where(User.phone_hash == ph))).scalar_one()

    token = new_token()
    expires = now + timedelta(minutes=settings.session_ttl_minutes)
    db.add(Session(user_id=user.id, token_hash=keyed_hash("sess:" + token), expires_at=expires))
    await db.commit()
    return token, expires, user.public_id
