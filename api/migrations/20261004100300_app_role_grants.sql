-- migrate:up

-- The API connects as `fairdrop_app`, never as the superuser that owns the tables (D-005).
-- Narrow on purpose: no DDL, no TRUNCATE, no DELETE except where listed, column-level UPDATE
-- where a column must never change, INSERT-only on the ledgers. Not a general permissions system.
--
-- The role is created NOLOGIN here (roles are cluster-wide; this file also runs on the test
-- database). `uv run fd migrate` then sets LOGIN and the password from .env (APP_DB_PASSWORD).
DO $$
BEGIN
    IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'fairdrop_app') THEN
        CREATE ROLE fairdrop_app NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOREPLICATION;
    END IF;
END
$$;

REVOKE CREATE ON SCHEMA public FROM PUBLIC;
GRANT USAGE ON SCHEMA public TO fairdrop_app;

-- drops: created only through create_drop(). capacity, id and created_at never change.
GRANT SELECT ON drops TO fairdrop_app;
GRANT UPDATE (name, mode, phase, reg_opens_at, reg_closes_at, window_s, claim_window_s,
              seed_commit, seed, entry_set_hash, drawn_at, closed_at, done_at)
    ON drops TO fairdrop_app;

GRANT SELECT, INSERT ON users TO fairdrop_app;
GRANT SELECT, INSERT ON sessions TO fairdrop_app;
GRANT UPDATE (revoked_at) ON sessions TO fairdrop_app;

-- entries: no DELETE (reset goes through admin_reset_drop). Identity columns never change.
GRANT SELECT, INSERT ON entries TO fairdrop_app;
GRANT UPDATE (status, risk_score, risk_flags, draw_rank, offer_expires_at, offered_at,
              allocated_at, step_up_passed_at, status_changed_at)
    ON entries TO fairdrop_app;

-- seats: rows come from create_drop() only; the claim may change just these three columns.
GRANT SELECT ON seats TO fairdrop_app;
GRANT UPDATE (status, entry_id, sold_at) ON seats TO fairdrop_app;

-- ledgers: append-only for the application.
GRANT SELECT, INSERT ON allocations TO fairdrop_app;
GRANT SELECT, INSERT ON abuse_events TO fairdrop_app;

GRANT SELECT, INSERT, DELETE ON idempotency_records TO fairdrop_app;  -- DELETE: TTL cleanup
GRANT UPDATE (response, status_code) ON idempotency_records TO fairdrop_app;

GRANT SELECT, INSERT ON drop_runs TO fairdrop_app;
GRANT UPDATE (ended_at, summary, scorecard) ON drop_runs TO fairdrop_app;

GRANT SELECT, INSERT, UPDATE ON app_settings TO fairdrop_app;
GRANT SELECT, INSERT, UPDATE ON system_state TO fairdrop_app;

GRANT SELECT ON v_drop_integrity TO fairdrop_app;

GRANT EXECUTE ON FUNCTION create_drop(text, int, text, int, int, text, text, timestamptz, timestamptz)
    TO fairdrop_app;
GRANT EXECUTE ON FUNCTION admin_reset_drop(uuid) TO fairdrop_app;

-- migrate:down

-- Forward-only (see 20261004100000_core_schema.sql).
