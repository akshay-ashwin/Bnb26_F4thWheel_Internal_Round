-- migrate:up

-- v_drop_integrity: the live proof behind GET /admin/drops/{id}/integrity (design section 11),
-- computed from the tables every time, never from counters.
--
-- Constraints already make most bad states unrepresentable. This view checks what a constraint
-- cannot express: that seats, allocations and entry statuses AGREE with each other (for example
-- a sold seat with no allocation, or an ALLOCATED entry that holds no seat). The columns after
-- `invariant_ok` are extra cross-table checks (Plan 08 exposes them under `extra`).
--
-- Shape rules:
--  * No join between the three tables, so nothing can multiply rows and inflate a count. The
--    view takes three small per-drop sets once (this drop's seats, its ledger rows, its
--    ALLOCATED entries) and answers every question with an independent scalar subquery over
--    them (counts and EXISTS only).
--  * `entries` is the one big table (52,000+ rows per drop). It is read exactly once, through
--    the (drop_id, status, draw_rank) index, for status = 'ALLOCATED': at most `capacity` rows.
--    Cost therefore scales with the seat count, not the entry count. (An earlier draft probed
--    `entries` once per seat and once per ledger row; measured, that was 2-4x slower.)
--  * "Entry is not ALLOCATED" is checked as "entry is not in this drop's ALLOCATED set", so an
--    entry of another drop is caught by the same check (blocked by the composite FKs anyway).
--  * Every metric is wrapped in COALESCE and invariant_ok in COALESCE(..., false), so a NULL can
--    never make the check pass by accident.
CREATE VIEW v_drop_integrity AS
SELECT
    m.drop_id,
    m.seats_total,
    m.sold,
    m.free,
    m.capacity,
    m.oversold,
    m.duplicate_entries_with_seats,
    m.allocations_count,
    m.sold_without_allocation,
    m.allocation_without_sold_seat,
    m.entries_allocated_count,
    m.entries_allocated_mismatch,
    COALESCE(
        m.seats_total = m.capacity
        AND m.oversold = 0
        AND m.duplicate_entries_with_seats = 0
        AND m.sold = m.allocations_count
        AND m.sold = m.entries_allocated_count
        AND m.sold_without_allocation = 0
        AND m.allocation_without_sold_seat = 0
        AND m.entries_allocated_mismatch = 0
        AND m.sold_seat_entry_not_allocated = 0
        AND m.free_seat_with_sold_at = 0,
        false
    ) AS invariant_ok,
    -- extra cross-table checks (both must be 0)
    m.sold_seat_entry_not_allocated,
    m.free_seat_with_sold_at
FROM (
    SELECT
        d.id AS drop_id,
        d.capacity,
        COALESCE(x.seats_total, 0) AS seats_total,
        COALESCE(x.sold, 0) AS sold,
        COALESCE(x.free, 0) AS free,
        GREATEST(0, COALESCE(x.sold, 0) - d.capacity) AS oversold,
        COALESCE(x.duplicate_entries_with_seats, 0) AS duplicate_entries_with_seats,
        COALESCE(x.allocations_count, 0) AS allocations_count,
        COALESCE(x.sold_without_allocation, 0) AS sold_without_allocation,
        COALESCE(x.allocation_without_sold_seat, 0) AS allocation_without_sold_seat,
        COALESCE(x.entries_allocated_count, 0) AS entries_allocated_count,
        -- ALLOCATED entries with no ledger row + ledger rows whose entry is not ALLOCATED
        COALESCE(x.allocated_without_ledger_row, 0)
            + COALESCE(x.ledger_entry_not_allocated, 0) AS entries_allocated_mismatch,
        COALESCE(x.sold_seat_entry_not_allocated, 0) AS sold_seat_entry_not_allocated,
        COALESCE(x.free_seat_with_sold_at, 0) AS free_seat_with_sold_at
    FROM drops d
    CROSS JOIN LATERAL (
        WITH
        e AS MATERIALIZED (   -- this drop's ALLOCATED entries (index range scan)
            SELECT en.id FROM entries en WHERE en.drop_id = d.id AND en.status = 'ALLOCATED'
        ),
        s AS MATERIALIZED (   -- this drop's seat rows
            SELECT se.id, se.status, se.entry_id, se.sold_at FROM seats se WHERE se.drop_id = d.id
        ),
        a AS MATERIALIZED (   -- this drop's ledger rows
            SELECT al.seat_id, al.entry_id FROM allocations al WHERE al.drop_id = d.id
        )
        SELECT
            (SELECT count(*) FROM s)::int AS seats_total,
            (SELECT count(*) FROM s WHERE s.status = 'sold')::int AS sold,
            (SELECT count(*) FROM s WHERE s.status = 'free')::int AS free,
            -- entries holding more than one seat (blocked by UNIQUE(drop_id, entry_id); still computed)
            (SELECT count(*) FROM (
                SELECT 1 FROM s WHERE s.entry_id IS NOT NULL GROUP BY s.entry_id HAVING count(*) > 1
            ) multi)::int AS duplicate_entries_with_seats,
            (SELECT count(*) FROM a)::int AS allocations_count,
            -- sold seats that no ledger row points at
            (SELECT count(*) FROM s
              WHERE s.status = 'sold'
                AND NOT EXISTS (SELECT 1 FROM a WHERE a.seat_id = s.id))::int AS sold_without_allocation,
            -- ledger rows whose seat is missing or not sold
            (SELECT count(*) FROM a
              WHERE NOT EXISTS (SELECT 1 FROM s WHERE s.id = a.seat_id AND s.status = 'sold')
            )::int AS allocation_without_sold_seat,
            (SELECT count(*) FROM e)::int AS entries_allocated_count,
            (SELECT count(*) FROM e
              WHERE NOT EXISTS (SELECT 1 FROM a WHERE a.entry_id = e.id))::int AS allocated_without_ledger_row,
            (SELECT count(*) FROM a
              WHERE NOT EXISTS (SELECT 1 FROM e WHERE e.id = a.entry_id))::int AS ledger_entry_not_allocated,
            -- sold seats whose entry is not an ALLOCATED entry of this drop
            (SELECT count(*) FROM s
              WHERE s.status = 'sold'
                AND NOT EXISTS (SELECT 1 FROM e WHERE e.id = s.entry_id))::int AS sold_seat_entry_not_allocated,
            (SELECT count(*) FROM s
              WHERE s.status = 'free' AND s.sold_at IS NOT NULL)::int AS free_seat_with_sold_at
    ) x
) m;

-- migrate:down

-- Forward-only (see 20261004100000_core_schema.sql).
