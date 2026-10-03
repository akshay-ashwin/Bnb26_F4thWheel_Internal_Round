-- Development only (Plan 02 section 4.7). NOT a migration: the test database never gets this row.
-- Creates one demo drop "Fair Drop Demo": capacity 500, mode fifo, phase SCHEDULED, with its 500
-- seats, through create_drop() so the drop and its seats appear together. Safe to run twice.
-- Plan 06 replaces this with POST /admin/drops (which generates a real seed and commitment).
--
--   macOS / Linux:  docker compose exec -T postgres sh -c 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB"' < api/seeds/dev_demo_drop.sql
--   PowerShell:     Get-Content api/seeds/dev_demo_drop.sql | docker compose exec -T postgres sh -c 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB"'
SELECT create_drop(
    'Fair Drop Demo', 500, 'fifo', 300, 120,
    encode(sha256(convert_to('dev-demo-seed', 'UTF8')), 'hex')
)
WHERE NOT EXISTS (SELECT 1 FROM drops WHERE name = 'Fair Drop Demo');

SELECT drop_id, seats_total, sold, free, capacity, invariant_ok
FROM v_drop_integrity
WHERE drop_id = (SELECT id FROM drops WHERE name = 'Fair Drop Demo');
