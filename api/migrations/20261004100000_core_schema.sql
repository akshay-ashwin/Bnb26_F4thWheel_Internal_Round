-- migrate:up

-- Plan 02: core schema. Constraints are the integrity wall (CLAUDE.md invariants 1, 2, 3).
-- Conventions: timestamptz (UTC), uuid ids except seats.id and log tables (bigint identity),
-- status/phase/mode are text + CHECK (not enums: easier to evolve). Forward-only.
-- gen_random_uuid() is built into PostgreSQL 13+, so no pgcrypto extension is needed.

CREATE TABLE drops (
    id             uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    name           text        NOT NULL,
    capacity       int         NOT NULL CONSTRAINT drops_capacity_positive CHECK (capacity > 0),
    mode           text        NOT NULL CONSTRAINT drops_mode_valid CHECK (mode IN ('fair', 'fifo')),
    phase          text        NOT NULL CONSTRAINT drops_phase_valid
                   CHECK (phase IN ('SCHEDULED', 'OPEN', 'CLOSED', 'DRAWN', 'CLAIMING', 'DONE')),
    reg_opens_at   timestamptz,
    reg_closes_at  timestamptz,
    window_s       int         NOT NULL CONSTRAINT drops_window_positive CHECK (window_s > 0),
    claim_window_s int         NOT NULL DEFAULT 120
                   CONSTRAINT drops_claim_window_positive CHECK (claim_window_s > 0),
    run_no         int         NOT NULL DEFAULT 1 CONSTRAINT drops_run_no_positive CHECK (run_no >= 1),
    seed_commit    text        NOT NULL,   -- published before OPEN (hash definition: Plan 06 / 09)
    seed           text,                   -- see Plan 06 section 5.2: stored from creation, revealed at draw
    entry_set_hash text,                   -- set when entries close
    created_at     timestamptz NOT NULL DEFAULT now(),
    drawn_at       timestamptz,
    closed_at      timestamptz,
    done_at        timestamptz,
    CONSTRAINT drops_reg_window_ordered
        CHECK (reg_opens_at IS NULL OR reg_closes_at IS NULL OR reg_closes_at > reg_opens_at),
    -- Target of seats(drop_id, capacity): lets a seat row carry its drop's capacity (D-004).
    CONSTRAINT drops_id_capacity_key UNIQUE (id, capacity)
);

CREATE TABLE users (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    public_id       text        NOT NULL UNIQUE,  -- random, used in the draw and in exports
    phone_hash      text        NOT NULL UNIQUE,  -- HMAC(pepper, phone): one phone = one user
    first_device_id text,
    first_ip        inet,
    created_at      timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE sessions (
    id         uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id    uuid        NOT NULL REFERENCES users (id),
    device_id  text,
    ip         inet,
    ua_hash    text,
    created_at timestamptz NOT NULL DEFAULT now(),
    revoked_at timestamptz
);
CREATE INDEX sessions_user_id_idx ON sessions (user_id);
-- "Re-verify returns the existing session for the same device".
CREATE INDEX sessions_user_device_active_idx ON sessions (user_id, device_id) WHERE revoked_at IS NULL;

CREATE TABLE entries (
    id                uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    drop_id           uuid        NOT NULL REFERENCES drops (id),
    user_id           uuid        NOT NULL REFERENCES users (id),
    status            text        NOT NULL CONSTRAINT entries_status_valid
                      CHECK (status IN ('REGISTERED', 'OFFERED', 'STEP_UP_REQUIRED', 'WAITLISTED',
                                        'NOT_SELECTED', 'OFFER_EXPIRED', 'ALLOCATED', 'DISQUALIFIED')),
    entered_at        timestamptz NOT NULL DEFAULT now(),
    client_ip         inet,
    device_id         text,
    risk_score        int         NOT NULL DEFAULT 0,
    risk_flags        jsonb       NOT NULL DEFAULT '[]',
    draw_rank         int         CONSTRAINT entries_draw_rank_positive CHECK (draw_rank >= 1),
    offer_expires_at  timestamptz,
    offered_at        timestamptz,
    allocated_at      timestamptz,
    step_up_passed_at timestamptz,
    status_changed_at timestamptz NOT NULL DEFAULT now(),
    run_no            int         NOT NULL CONSTRAINT entries_run_no_positive CHECK (run_no >= 1),
    -- One entry per identity per drop. UNIQUE(drop_id, draw_rank): a rank is used once; the
    -- many NULL ranks before the draw are fine because NULLs are distinct in a unique index.
    CONSTRAINT entries_drop_user_key UNIQUE (drop_id, user_id),
    CONSTRAINT entries_drop_draw_rank_key UNIQUE (drop_id, draw_rank),
    -- Target of the composite FKs that keep seats and allocations inside one drop (D-004).
    CONSTRAINT entries_id_drop_key UNIQUE (id, drop_id)
);
CREATE INDEX entries_drop_status_rank_idx ON entries (drop_id, status, draw_rank);
CREATE INDEX entries_offer_expiry_idx ON entries (drop_id, offer_expires_at) WHERE status = 'OFFERED';

-- One physical row per seat. An allocation IS a seat row flipping free -> sold (design section 7).
CREATE TABLE seats (
    id       bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    drop_id  uuid        NOT NULL,
    -- Copy of drops.capacity. With the FK below and the CHECK, seat_no is confined to
    -- 1..capacity, and UNIQUE(drop_id, seat_no) then makes more than `capacity` rows impossible.
    capacity int         NOT NULL,
    seat_no  int         NOT NULL,
    status   text        NOT NULL CONSTRAINT seats_status_valid CHECK (status IN ('free', 'sold')),
    entry_id uuid,
    sold_at  timestamptz,
    CONSTRAINT seats_drop_capacity_fk FOREIGN KEY (drop_id, capacity) REFERENCES drops (id, capacity),
    CONSTRAINT seats_seat_no_within_capacity CHECK (seat_no BETWEEN 1 AND capacity),
    CONSTRAINT seats_drop_seat_no_key UNIQUE (drop_id, seat_no),
    -- One seat per entry. NULL entry_id (free seats) is distinct, so many free seats coexist.
    -- Deliberately NOT "NULLS NOT DISTINCT": that would allow only one free seat per drop.
    CONSTRAINT seats_drop_entry_key UNIQUE (drop_id, entry_id),
    -- An entry can only hold a seat of its own drop (MATCH SIMPLE: skipped while entry_id is NULL).
    CONSTRAINT seats_entry_in_drop_fk FOREIGN KEY (entry_id, drop_id) REFERENCES entries (id, drop_id),
    CONSTRAINT seats_free_iff_no_entry CHECK ((status = 'free') = (entry_id IS NULL)),
    CONSTRAINT seats_sold_iff_sold_at CHECK ((status = 'sold') = (sold_at IS NOT NULL)),
    -- Target of allocations(seat_id, entry_id): an allocation must agree with its seat row.
    CONSTRAINT seats_id_entry_key UNIQUE (id, entry_id)
);
-- The claim takes the lowest free seat (ORDER BY seat_no): a partial index in that order finds
-- it without walking past sold seats.
CREATE INDEX seats_free_idx ON seats (drop_id, seat_no) WHERE status = 'free';

-- Append-only ledger. Immutability is enforced in 20261004100100 (trigger) and 20261004100300 (grants).
CREATE TABLE allocations (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    drop_id         uuid        NOT NULL,
    entry_id        uuid        NOT NULL UNIQUE,
    seat_id         bigint      NOT NULL UNIQUE,
    idempotency_key uuid        NOT NULL,
    run_no          int         NOT NULL CONSTRAINT allocations_run_no_positive CHECK (run_no >= 1),
    created_at      timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT allocations_entry_in_drop_fk FOREIGN KEY (entry_id, drop_id) REFERENCES entries (id, drop_id),
    -- The seat named here must currently hold exactly this entry. Together with the two FKs on
    -- seats this also pins seat, entry and allocation to the same drop.
    CONSTRAINT allocations_seat_entry_fk FOREIGN KEY (seat_id, entry_id) REFERENCES seats (id, entry_id)
);
-- v_drop_integrity reads the ledger per drop.
CREATE INDEX allocations_drop_idx ON allocations (drop_id);

CREATE TABLE idempotency_records (
    user_id      uuid        NOT NULL REFERENCES users (id),
    key          uuid        NOT NULL,
    drop_id      uuid        NOT NULL REFERENCES drops (id),
    endpoint     text        NOT NULL,
    request_hash text        NOT NULL,
    response     jsonb       NOT NULL,
    status_code  int         NOT NULL,
    created_at   timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (user_id, key)
);
CREATE INDEX idempotency_records_drop_idx ON idempotency_records (drop_id);
CREATE INDEX idempotency_records_created_at_idx ON idempotency_records (created_at);

-- Sampled abuse log (full counts live in Redis). No FK on drop_id: events may outlive a drop.
CREATE TABLE abuse_events (
    id        bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    ts        timestamptz NOT NULL DEFAULT now(),
    drop_id   uuid,
    layer     text,
    action    text,
    key_type  text,
    key_value text,
    user_id   uuid,
    detail    jsonb
);
CREATE INDEX abuse_events_drop_ts_idx ON abuse_events (drop_id, ts);

-- One row per run, so the dashboard can ghost the previous run after a reset.
CREATE TABLE drop_runs (
    id         bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    drop_id    uuid        NOT NULL REFERENCES drops (id),
    run_no     int         NOT NULL CONSTRAINT drop_runs_run_no_positive CHECK (run_no >= 1),
    mode       text        NOT NULL CONSTRAINT drop_runs_mode_valid CHECK (mode IN ('fair', 'fifo')),
    started_at timestamptz,
    ended_at   timestamptz,
    summary    jsonb,      -- metrics snapshot
    scorecard  jsonb,      -- uploaded by the evaluator (Plan 19)
    CONSTRAINT drop_runs_drop_run_key UNIQUE (drop_id, run_no)
);

-- Abuse layer toggles and thresholds (Plan 12); Redis caches them.
CREATE TABLE app_settings (
    key        text PRIMARY KEY,
    value      jsonb       NOT NULL,
    updated_at timestamptz NOT NULL DEFAULT now()
);

-- Sweeper heartbeat and similar (Plan 11).
CREATE TABLE system_state (
    key        text PRIMARY KEY,
    value      jsonb       NOT NULL,
    updated_at timestamptz NOT NULL DEFAULT now()
);

-- migrate:down

-- Forward-only for the hackathon (Plan 02 section 4.1). `uv run fd reset-db` rebuilds from scratch.
