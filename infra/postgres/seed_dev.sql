-- Dev-only (not a migration; Plan 06's admin API replaces it). Run with psql after `uv run fd migrate`.
WITH d AS (
  INSERT INTO drops (name, capacity, mode, phase, window_s, seed_commit)
  VALUES ('Fair Drop Demo', 500, 'fifo', 'SCHEDULED', 60, 'dev-placeholder')
  RETURNING id)
SELECT create_drop_seats(id) FROM d;
