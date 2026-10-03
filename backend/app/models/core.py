from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Integer,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base

_now = func.now()


def _ts(**kw) -> Mapped[datetime]:
    return mapped_column(DateTime(timezone=True), server_default=_now, **kw)


class User(Base):
    __tablename__ = "users"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    public_id: Mapped[str] = mapped_column(String(32), unique=True)
    phone_hash: Mapped[str] = mapped_column(String(64), unique=True)
    created_at: Mapped[datetime] = _ts()


class OtpCode(Base):
    """Demo OTP storage (hashed). Replaceable with a real provider."""

    __tablename__ = "otp_codes"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    phone_hash: Mapped[str] = mapped_column(String(64), index=True)
    code_hash: Mapped[str] = mapped_column(String(64))
    attempts: Mapped[int] = mapped_column(Integer, server_default="0")
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = _ts()


class Session(Base):
    __tablename__ = "sessions"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    created_at: Mapped[datetime] = _ts()
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class Drop(Base):
    __tablename__ = "drops"
    __table_args__ = (
        CheckConstraint("mode IN ('fifo','fair')", name="ck_drops_mode"),
        CheckConstraint(
            "status IN ('scheduled','open','frozen','drawn','claimable','closed')", name="ck_drops_status"
        ),
        CheckConstraint("total_seats >= 0", name="ck_drops_total_seats"),
    )
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    total_seats: Mapped[int] = mapped_column(Integer)
    mode: Mapped[str] = mapped_column(String(16), server_default="fifo")
    status: Mapped[str] = mapped_column(String(16), server_default="open")
    entry_start: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    entry_end: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = _ts()
    # Fair Draw (see utils/draw.py). seed/commitment/entry_set_hash are write-once (DB trigger).
    seed_commitment: Mapped[str | None] = mapped_column(String(64))
    draw_seed: Mapped[str | None] = mapped_column(String(64))  # secret until seed_revealed_at is set
    seed_committed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    seed_revealed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    entry_set_hash: Mapped[str | None] = mapped_column(String(64))
    frozen_entry_count: Mapped[int | None] = mapped_column(Integer)
    frozen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    drawn_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    claimable_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Entry(Base):
    __tablename__ = "entries"
    __table_args__ = (
        UniqueConstraint("drop_id", "user_id", name="uq_entries_drop_user"),
        CheckConstraint("status IN ('pending','claimed')", name="ck_entries_status"),
    )
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    drop_id: Mapped[int] = mapped_column(ForeignKey("drops.id", ondelete="CASCADE"))
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    status: Mapped[str] = mapped_column(String(16), server_default="pending")
    created_at: Mapped[datetime] = _ts()


class Seat(Base):
    __tablename__ = "seats"
    __table_args__ = (
        UniqueConstraint("drop_id", "seat_number", name="uq_seats_drop_number"),
        UniqueConstraint("drop_id", "id", name="uq_seats_drop_id_id"),
        CheckConstraint("status IN ('available','allocated')", name="ck_seats_status"),
    )
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    drop_id: Mapped[int] = mapped_column(ForeignKey("drops.id", ondelete="CASCADE"))
    seat_number: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(16), server_default="available")


class Allocation(Base):
    __tablename__ = "allocations"
    __table_args__ = (
        UniqueConstraint("drop_id", "user_id", name="uq_allocations_drop_user"),
        UniqueConstraint("seat_id", name="uq_allocations_seat"),
        # seat must belong to the same drop as the allocation
        ForeignKeyConstraint(["drop_id", "seat_id"], ["seats.drop_id", "seats.id"], name="fk_allocations_seat_drop"),
    )
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    drop_id: Mapped[int] = mapped_column(ForeignKey("drops.id", ondelete="CASCADE"))
    seat_id: Mapped[int] = mapped_column(BigInteger)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    created_at: Mapped[datetime] = _ts()


class IdempotencyRecord(Base):
    __tablename__ = "idempotency_records"
    __table_args__ = (UniqueConstraint("user_id", "endpoint", "key", name="uq_idem_user_endpoint_key"),)
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    endpoint: Mapped[str] = mapped_column(String(200))
    key: Mapped[str] = mapped_column(String(200))
    response_status: Mapped[int | None] = mapped_column(Integer)
    response_body: Mapped[dict | None] = mapped_column(JSONB)
    created_at: Mapped[datetime] = _ts()


class DrawResult(Base):
    __tablename__ = "draw_results"
    __table_args__ = (
        UniqueConstraint("drop_id", "user_id", name="uq_draw_results_drop_user"),
        UniqueConstraint("drop_id", "rank", name="uq_draw_results_drop_rank"),
    )
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    drop_id: Mapped[int] = mapped_column(ForeignKey("drops.id", ondelete="CASCADE"))
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    user_public_id: Mapped[str] = mapped_column(String(32))
    score: Mapped[str] = mapped_column(String(64))
    rank: Mapped[int] = mapped_column(Integer)
    is_winner: Mapped[bool] = mapped_column(Boolean)


class AdmissionToken(Base):
    """Server-side state for the signed admission token: one live token per (drop, user)."""

    __tablename__ = "admission_tokens"
    __table_args__ = (UniqueConstraint("drop_id", "user_id", name="uq_admission_tokens_drop_user"),)
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    drop_id: Mapped[int] = mapped_column(ForeignKey("drops.id", ondelete="CASCADE"))
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    session_id: Mapped[int] = mapped_column(ForeignKey("sessions.id", ondelete="CASCADE"))
    jti: Mapped[str] = mapped_column(String(64), unique=True)
    issued_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=_now)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


__all__ = [
    "User", "OtpCode", "Session", "Drop", "Entry", "Seat", "Allocation", "IdempotencyRecord",
    "DrawResult", "AdmissionToken",
]
