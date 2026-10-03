import hmac
from dataclasses import dataclass
from datetime import UTC, datetime

from fastapi import Depends, Header
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.db.session import get_db
from app.models import Session, User
from app.utils.errors import ApiError
from app.utils.security import keyed_hash


@dataclass
class AuthContext:
    user: User
    session_id: int


async def current_auth(
    authorization: str | None = Header(default=None), db: AsyncSession = Depends(get_db)
) -> AuthContext:
    """Resolve user + session from the Bearer session token. Client-supplied user IDs are never used."""
    scheme, _, token = (authorization or "").partition(" ")
    if scheme.lower() != "bearer" or not token:
        raise ApiError(401, "UNAUTHENTICATED", "Missing or malformed Authorization header.")
    row = (
        await db.execute(
            select(User, Session.id)
            .join(Session, Session.user_id == User.id)
            .where(Session.token_hash == keyed_hash("sess:" + token), Session.expires_at > datetime.now(UTC))
        )
    ).first()
    if row is None:
        raise ApiError(401, "INVALID_SESSION", "Session is invalid or expired.")
    return AuthContext(user=row[0], session_id=row[1])


async def current_user(auth: AuthContext = Depends(current_auth)) -> User:
    return auth.user


async def require_admin(x_admin_key: str | None = Header(default=None)) -> None:
    if not x_admin_key or not hmac.compare_digest(x_admin_key, get_settings().admin_api_key):
        raise ApiError(403, "FORBIDDEN", "Admin key required.")
