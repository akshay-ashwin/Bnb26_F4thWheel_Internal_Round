-- migrate:up
CREATE EXTENSION IF NOT EXISTS pgcrypto;

CREATE TABLE drops (
  id               uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  name             text NOT NULL,
  capacity         int  NOT NULL CHECK (capacity > 0),
  mode             text NOT NULL CHECK (mode IN ('fair','fifo')),
  phase            text NOT NULL CHECK (phase IN ('SCHEDULED','OPEN','CLOSED','DRAWN','CLAIMING','DONE')),
  window_s         int  NOT NULL CHECK (window_s > 0),
  run_no           int  NOT NULL DEFAULT 1,
  reg_opens_at     timestamptz,
  reg_closes_at    timestamptz,
  claim_window_s   int  NOT NULL DEFAULT 120,
  seed_commit      text NOT NULL,
  seed             text,
  entry_set_hash   text,
  drawn_at         timestamptz,
  closed_at        timestamptz,
  done_at          timestamptz,
  created_at       timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE users (
  id               uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  public_id        text NOT NULL UNIQUE,
  phone_hash       text NOT NULL UNIQUE,
  first_device_id  text,
  first_ip         inet,
  created_at       timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE sessions (
  id               uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id          uuid NOT NULL REFERENCES users(id),
  device_id        text,
  ip               inet,
  ua_hash          text,
  created_at       timestamptz NOT NULL DEFAULT now(),
  revoked_at       timestamptz
);
CREATE INDEX sessions_user_idx ON sessions (user_id);
CREATE INDEX sessions_user_device_live_idx ON sessions (user_id, device_id) WHERE revoked_at IS NULL;

CREATE TABLE entries (
  id                 uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  drop_id            uuid NOT NULL REFERENCES drops(id),
  user_id            uuid NOT NULL REFERENCES users(id),
  status             text NOT NULL CHECK (status IN ('REGISTERED','OFFERED','STEP_UP_REQUIRED',
                       'WAITLISTED','NOT_SELECTED','OFFER_EXPIRED','ALLOCATED','DISQUALIFIED')),
  entered_at         timestamptz NOT NULL DEFAULT now(),
  client_ip          inet,
  device_id          text,
  risk_score         int NOT NULL DEFAULT 0,
  risk_flags         jsonb NOT NULL DEFAULT '[]',
  draw_rank          int,
  offer_expires_at   timestamptz,
  offered_at         timestamptz,
  allocated_at       timestamptz,
  step_up_passed_at  timestamptz,
  status_changed_at  timestamptz NOT NULL DEFAULT now(),
  run_no             int NOT NULL DEFAULT 1,
  UNIQUE (drop_id, user_id),
  UNIQUE (drop_id, draw_rank)
);
CREATE INDEX entries_drop_status_rank_idx ON entries (drop_id, status, draw_rank);
CREATE INDEX entries_offer_expiry_idx ON entries (drop_id, offer_expires_at) WHERE status = 'OFFERED';

CREATE TABLE seats (
  id        bigserial PRIMARY KEY,
  drop_id   uuid NOT NULL REFERENCES drops(id),
  seat_no   int  NOT NULL,
  status    text NOT NULL CHECK (status IN ('free','sold')),
  entry_id  uuid REFERENCES entries(id),
  sold_at   timestamptz,
  UNIQUE (drop_id, seat_no),
  UNIQUE (drop_id, entry_id),
  CHECK ((status = 'free') = (entry_id IS NULL))
);
CREATE INDEX seats_free_idx ON seats (drop_id) WHERE status = 'free';

CREATE TABLE allocations (
  id               uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  drop_id          uuid NOT NULL REFERENCES drops(id),
  entry_id         uuid NOT NULL UNIQUE REFERENCES entries(id),
  seat_id          bigint NOT NULL UNIQUE REFERENCES seats(id),
  idempotency_key  uuid NOT NULL,
  run_no           int NOT NULL DEFAULT 1,
  created_at       timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX allocations_drop_idx ON allocations (drop_id);

CREATE TABLE idempotency_records (
  user_id       uuid NOT NULL,
  key           uuid NOT NULL,
  drop_id       uuid,
  endpoint      text NOT NULL,
  request_hash  text NOT NULL,
  response      jsonb NOT NULL,
  status_code   int  NOT NULL,
  created_at    timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (user_id, key)
);

CREATE TABLE abuse_events (
  id         bigserial PRIMARY KEY,
  ts         timestamptz NOT NULL DEFAULT now(),
  drop_id    uuid,
  layer      text,
  action     text,
  key_type   text,
  key_value  text,
  user_id    uuid,
  detail     jsonb
);
CREATE INDEX abuse_events_drop_ts_idx ON abuse_events (drop_id, ts);

CREATE TABLE drop_runs (
  drop_id     uuid NOT NULL REFERENCES drops(id),
  run_no      int  NOT NULL,
  mode        text NOT NULL,
  started_at  timestamptz,
  ended_at    timestamptz,
  summary     jsonb,
  scorecard   jsonb,
  UNIQUE (drop_id, run_no)
);

CREATE TABLE app_settings (
  key         text PRIMARY KEY,
  value       jsonb NOT NULL,
  updated_at  timestamptz NOT NULL DEFAULT now()
);

-- Seats are created in the same transaction as the drop (call this right after the INSERT).
CREATE FUNCTION create_drop_seats(p_drop_id uuid) RETURNS void
LANGUAGE sql AS $$
  INSERT INTO seats (drop_id, seat_no, status)
  SELECT d.id, g, 'free' FROM drops d, generate_series(1, d.capacity) g WHERE d.id = p_drop_id;
$$;

-- Reset a drop for a new run. Application code regenerates the seed (CSPRNG) and phase afterwards.
CREATE FUNCTION admin_reset_drop(p_drop_id uuid) RETURNS int
LANGUAGE plpgsql AS $$
DECLARE new_run int;
BEGIN
  DELETE FROM allocations WHERE drop_id = p_drop_id;
  UPDATE seats SET status = 'free', entry_id = NULL, sold_at = NULL WHERE drop_id = p_drop_id;
  DELETE FROM entries WHERE drop_id = p_drop_id;
  DELETE FROM idempotency_records WHERE drop_id = p_drop_id;
  UPDATE drops SET run_no = run_no + 1 WHERE id = p_drop_id RETURNING run_no INTO new_run;
  RETURN new_run;
END $$;

-- Live proof. Constraints already block duplicates; the interesting failures are inconsistencies
-- between seats, allocations and entry statuses.
CREATE VIEW v_drop_integrity AS
SELECT
  d.id AS drop_id,
  d.capacity,
  COALESCE(s.total, 0)  AS seats_total,
  COALESCE(s.sold, 0)   AS sold,
  COALESCE(s.total, 0) - COALESCE(s.sold, 0) AS free,
  GREATEST(0, COALESCE(s.sold, 0) - d.capacity) AS oversold,
  COALESCE(dup.n, 0)    AS duplicate_entries_with_seats,
  COALESCE(a.n, 0)      AS allocations_count,
  COALESCE(sw.n, 0)     AS sold_without_allocation,
  COALESCE(aw.n, 0)     AS allocation_without_sold_seat,
  COALESCE(e.n, 0)      AS entries_allocated_count,
  ABS(COALESCE(e.n, 0) - COALESCE(a.n, 0)) AS entries_allocated_mismatch,
  (   COALESCE(s.total, 0) = d.capacity
  AND COALESCE(s.sold, 0) <= d.capacity
  AND COALESCE(dup.n, 0) = 0
  AND COALESCE(sw.n, 0) = 0
  AND COALESCE(aw.n, 0) = 0
  AND COALESCE(e.n, 0) = COALESCE(a.n, 0)
  AND COALESCE(a.n, 0) = COALESCE(s.sold, 0)
  ) AS invariant_ok
FROM drops d
LEFT JOIN LATERAL (
  SELECT count(*) AS total, count(*) FILTER (WHERE status = 'sold') AS sold
  FROM seats WHERE drop_id = d.id) s ON true
LEFT JOIN LATERAL (
  SELECT count(*) AS n FROM (
    SELECT entry_id FROM seats WHERE drop_id = d.id AND entry_id IS NOT NULL
    GROUP BY entry_id HAVING count(*) > 1) x) dup ON true
LEFT JOIN LATERAL (SELECT count(*) AS n FROM allocations WHERE drop_id = d.id) a ON true
LEFT JOIN LATERAL (
  SELECT count(*) AS n FROM seats st
  WHERE st.drop_id = d.id AND st.status = 'sold'
    AND NOT EXISTS (SELECT 1 FROM allocations al WHERE al.seat_id = st.id)) sw ON true
LEFT JOIN LATERAL (
  SELECT count(*) AS n FROM allocations al JOIN seats st ON st.id = al.seat_id
  WHERE al.drop_id = d.id AND st.status <> 'sold') aw ON true
LEFT JOIN LATERAL (
  SELECT count(*) AS n FROM entries WHERE drop_id = d.id AND status = 'ALLOCATED') e ON true;

-- migrate:down
DROP VIEW v_drop_integrity;
DROP FUNCTION admin_reset_drop(uuid);
DROP FUNCTION create_drop_seats(uuid);
DROP TABLE app_settings, drop_runs, abuse_events, idempotency_records, allocations, seats,
  entries, sessions, users, drops;
