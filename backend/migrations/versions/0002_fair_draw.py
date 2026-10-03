"""fair draw: lifecycle states, seed commitment, entry-set hash, draw results, admission tokens

Revision ID: 0002
Revises: 0001
"""
from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None

UP = [
    "ALTER TABLE drops DROP CONSTRAINT ck_drops_status",
    """ALTER TABLE drops ADD CONSTRAINT ck_drops_status
        CHECK (status IN ('scheduled','open','frozen','drawn','claimable','closed'))""",
    """ALTER TABLE drops
        ADD COLUMN seed_commitment VARCHAR(64),
        ADD COLUMN draw_seed VARCHAR(64),
        ADD COLUMN seed_committed_at TIMESTAMPTZ,
        ADD COLUMN seed_revealed_at TIMESTAMPTZ,
        ADD COLUMN entry_set_hash VARCHAR(64),
        ADD COLUMN frozen_entry_count INTEGER,
        ADD COLUMN frozen_at TIMESTAMPTZ,
        ADD COLUMN drawn_at TIMESTAMPTZ,
        ADD COLUMN claimable_at TIMESTAMPTZ,
        ADD COLUMN closed_at TIMESTAMPTZ""",
    # The stored seed must always hash to the published commitment (seed = its 64-char hex string, UTF-8).
    """ALTER TABLE drops ADD CONSTRAINT ck_drops_seed_matches_commitment
        CHECK (draw_seed IS NULL OR (seed_commitment IS NOT NULL
               AND encode(sha256(convert_to(draw_seed, 'UTF8')), 'hex') = seed_commitment))""",
    # Commitment, seed and entry-set hash are write-once.
    """CREATE FUNCTION drops_fair_fields_immutable() RETURNS trigger AS $$
       BEGIN
         IF OLD.seed_commitment IS NOT NULL AND NEW.seed_commitment IS DISTINCT FROM OLD.seed_commitment THEN
           RAISE EXCEPTION 'seed_commitment is immutable once set';
         END IF;
         IF OLD.draw_seed IS NOT NULL AND NEW.draw_seed IS DISTINCT FROM OLD.draw_seed THEN
           RAISE EXCEPTION 'draw_seed is immutable once set';
         END IF;
         IF OLD.entry_set_hash IS NOT NULL AND NEW.entry_set_hash IS DISTINCT FROM OLD.entry_set_hash THEN
           RAISE EXCEPTION 'entry_set_hash is immutable once set';
         END IF;
         RETURN NEW;
       END; $$ LANGUAGE plpgsql""",
    """CREATE TRIGGER trg_drops_fair_fields_immutable BEFORE UPDATE ON drops
       FOR EACH ROW EXECUTE FUNCTION drops_fair_fields_immutable()""",
    # Last line of defence: no entry can be inserted once the entry set is frozen.
    """CREATE FUNCTION entries_blocked_after_freeze() RETURNS trigger AS $$
       BEGIN
         IF (SELECT status FROM drops WHERE id = NEW.drop_id) IN ('frozen','drawn','claimable') THEN
           RAISE EXCEPTION 'entries are frozen for drop %', NEW.drop_id;
         END IF;
         RETURN NEW;
       END; $$ LANGUAGE plpgsql""",
    """CREATE TRIGGER trg_entries_blocked_after_freeze BEFORE INSERT ON entries
       FOR EACH ROW EXECUTE FUNCTION entries_blocked_after_freeze()""",
    """CREATE TABLE draw_results (
        id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
        drop_id BIGINT NOT NULL REFERENCES drops(id) ON DELETE CASCADE,
        user_id BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        user_public_id VARCHAR(32) NOT NULL,
        score VARCHAR(64) NOT NULL,
        rank INTEGER NOT NULL CONSTRAINT ck_draw_results_rank CHECK (rank >= 1),
        is_winner BOOLEAN NOT NULL,
        CONSTRAINT uq_draw_results_drop_user UNIQUE (drop_id, user_id),
        CONSTRAINT uq_draw_results_drop_rank UNIQUE (drop_id, rank))""",
    "CREATE INDEX ix_draw_results_drop_public ON draw_results (drop_id, user_public_id)",
    """CREATE TABLE admission_tokens (
        id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
        drop_id BIGINT NOT NULL REFERENCES drops(id) ON DELETE CASCADE,
        user_id BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        session_id BIGINT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
        jti VARCHAR(64) NOT NULL UNIQUE,
        issued_at TIMESTAMPTZ NOT NULL DEFAULT now(),
        expires_at TIMESTAMPTZ NOT NULL,
        consumed_at TIMESTAMPTZ,
        CONSTRAINT uq_admission_tokens_drop_user UNIQUE (drop_id, user_id))""",
]


def upgrade() -> None:
    for stmt in UP:
        op.execute(stmt)


def downgrade() -> None:
    for stmt in (
        "DROP TABLE IF EXISTS admission_tokens CASCADE",
        "DROP TABLE IF EXISTS draw_results CASCADE",
        "DROP TRIGGER IF EXISTS trg_entries_blocked_after_freeze ON entries",
        "DROP FUNCTION IF EXISTS entries_blocked_after_freeze()",
        "DROP TRIGGER IF EXISTS trg_drops_fair_fields_immutable ON drops",
        "DROP FUNCTION IF EXISTS drops_fair_fields_immutable()",
        "ALTER TABLE drops DROP CONSTRAINT IF EXISTS ck_drops_seed_matches_commitment",
        """ALTER TABLE drops
            DROP COLUMN seed_commitment, DROP COLUMN draw_seed, DROP COLUMN seed_committed_at,
            DROP COLUMN seed_revealed_at, DROP COLUMN entry_set_hash, DROP COLUMN frozen_entry_count,
            DROP COLUMN frozen_at, DROP COLUMN drawn_at, DROP COLUMN claimable_at, DROP COLUMN closed_at""",
        "UPDATE drops SET status = 'closed' WHERE status IN ('frozen','drawn','claimable')",
        "ALTER TABLE drops DROP CONSTRAINT ck_drops_status",
        "ALTER TABLE drops ADD CONSTRAINT ck_drops_status CHECK (status IN ('scheduled','open','closed'))",
    ):
        op.execute(stmt)
