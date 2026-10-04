-- migrate:up

-- "Re-verify from the same device returns the existing session" must hold even when two verifies
-- for one user and device land at the same moment. The old index was a plain lookup index, so both
-- could insert. A partial UNIQUE index makes the second insert a conflict that the code resolves
-- by selecting the winner's row. Revoking a session (revoked_at set) frees the slot.
-- Additive and idempotent in effect: no data is changed, no column or API shape changes.
DROP INDEX IF EXISTS sessions_user_device_active_idx;
CREATE UNIQUE INDEX sessions_user_device_active_uidx
    ON sessions (user_id, device_id) WHERE revoked_at IS NULL;

-- migrate:down

-- Forward-only (see 20261004100000_core_schema.sql).
