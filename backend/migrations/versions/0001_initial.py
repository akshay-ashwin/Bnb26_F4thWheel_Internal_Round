"""initial schema + demo drop (500 seats)

Revision ID: 0001
Revises:
"""
from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None

DDL = [
    """CREATE TABLE users (
        id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
        public_id VARCHAR(32) NOT NULL UNIQUE,
        phone_hash VARCHAR(64) NOT NULL UNIQUE,
        created_at TIMESTAMPTZ NOT NULL DEFAULT now())""",
    """CREATE TABLE otp_codes (
        id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
        phone_hash VARCHAR(64) NOT NULL,
        code_hash VARCHAR(64) NOT NULL,
        attempts INTEGER NOT NULL DEFAULT 0,
        expires_at TIMESTAMPTZ NOT NULL,
        consumed_at TIMESTAMPTZ,
        created_at TIMESTAMPTZ NOT NULL DEFAULT now())""",
    "CREATE INDEX ix_otp_codes_phone_hash ON otp_codes (phone_hash)",
    """CREATE TABLE sessions (
        id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
        user_id BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        token_hash VARCHAR(64) NOT NULL UNIQUE,
        created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
        expires_at TIMESTAMPTZ NOT NULL)""",
    "CREATE INDEX ix_sessions_user_id ON sessions (user_id)",
    """CREATE TABLE drops (
        id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
        name VARCHAR(200) NOT NULL,
        total_seats INTEGER NOT NULL CONSTRAINT ck_drops_total_seats CHECK (total_seats >= 0),
        mode VARCHAR(16) NOT NULL DEFAULT 'fifo' CONSTRAINT ck_drops_mode CHECK (mode IN ('fifo','fair')),
        status VARCHAR(16) NOT NULL DEFAULT 'open'
            CONSTRAINT ck_drops_status CHECK (status IN ('scheduled','open','closed')),
        entry_start TIMESTAMPTZ NOT NULL,
        entry_end TIMESTAMPTZ NOT NULL,
        created_at TIMESTAMPTZ NOT NULL DEFAULT now())""",
    """CREATE TABLE entries (
        id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
        drop_id BIGINT NOT NULL REFERENCES drops(id) ON DELETE CASCADE,
        user_id BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        status VARCHAR(16) NOT NULL DEFAULT 'pending'
            CONSTRAINT ck_entries_status CHECK (status IN ('pending','claimed')),
        created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
        CONSTRAINT uq_entries_drop_user UNIQUE (drop_id, user_id))""",
    "CREATE INDEX ix_entries_drop_order ON entries (drop_id, created_at, id)",
    """CREATE TABLE seats (
        id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
        drop_id BIGINT NOT NULL REFERENCES drops(id) ON DELETE CASCADE,
        seat_number INTEGER NOT NULL,
        status VARCHAR(16) NOT NULL DEFAULT 'available'
            CONSTRAINT ck_seats_status CHECK (status IN ('available','allocated')),
        CONSTRAINT uq_seats_drop_number UNIQUE (drop_id, seat_number),
        CONSTRAINT uq_seats_drop_id_id UNIQUE (drop_id, id))""",
    """CREATE TABLE allocations (
        id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
        drop_id BIGINT NOT NULL REFERENCES drops(id) ON DELETE CASCADE,
        seat_id BIGINT NOT NULL,
        user_id BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
        CONSTRAINT uq_allocations_drop_user UNIQUE (drop_id, user_id),
        CONSTRAINT uq_allocations_seat UNIQUE (seat_id),
        CONSTRAINT fk_allocations_seat_drop FOREIGN KEY (drop_id, seat_id) REFERENCES seats (drop_id, id))""",
    """CREATE TABLE idempotency_records (
        id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
        user_id BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        endpoint VARCHAR(200) NOT NULL,
        key VARCHAR(200) NOT NULL,
        response_status INTEGER,
        response_body JSONB,
        created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
        CONSTRAINT uq_idem_user_endpoint_key UNIQUE (user_id, endpoint, key))""",
    # Demo drop: exactly 500 physical seats.
    """INSERT INTO drops (name, total_seats, mode, status, entry_start, entry_end)
       VALUES ('Fair Drop Demo', 500, 'fifo', 'open', now() - interval '1 hour', now() + interval '365 days')""",
    """INSERT INTO seats (drop_id, seat_number)
       SELECT (SELECT id FROM drops WHERE name = 'Fair Drop Demo'), n FROM generate_series(1, 500) AS n""",
]


def upgrade() -> None:
    for stmt in DDL:
        op.execute(stmt)


def downgrade() -> None:
    for t in ("idempotency_records", "allocations", "seats", "entries", "drops", "sessions", "otp_codes", "users"):
        op.execute(f"DROP TABLE IF EXISTS {t} CASCADE")
