# D-004 — Structural seat cap and cross-drop composite keys

Date: 2026-10-04  ·  Raised during: Plan 02  ·  Status: ADOPTED

## The original plan said
Plan 02 §4.3 and design §12: `seats` is "exactly as design" (`UNIQUE(drop_id, seat_no)`, `UNIQUE(drop_id, entry_id)`, the free/entry CHECK), `seats.entry_id` references `entries(id)`, `allocations.drop_id` has no foreign key, and `allocations.seat_id` references `seats(id)`. "500 seats never become 501" rests on "exactly `capacity` seat rows", which the schema never enforced: nothing stopped an extra seat row (`seat_no = 501`), and nothing tied a seat, an entry and an allocation to the same drop.

## What we do instead
1. **Seat cap.** `seats` gets a `capacity` column. `FOREIGN KEY (drop_id, capacity) REFERENCES drops (id, capacity)` (needs `UNIQUE (id, capacity)` on `drops`) and `CHECK (seat_no BETWEEN 1 AND capacity)`. With `UNIQUE(drop_id, seat_no)` a drop can never have more seat rows than its capacity, and `capacity` cannot be changed while seats exist.
2. **Cross-drop keys.** `entries` gets `UNIQUE (id, drop_id)`. `seats (entry_id, drop_id)` and `allocations (entry_id, drop_id)` reference it, so a seat or allocation can only point at an entry of the same drop (MATCH SIMPLE: free seats, where `entry_id` is NULL, skip the check). `seats` gets `UNIQUE (id, entry_id)` and `allocations (seat_id, entry_id)` references it, so a ledger row must agree with the seat row it names.
3. `seats.sold_at` must be set exactly when the seat is sold; `entries.draw_rank >= 1`.
4. Drops and seats are created only by the SQL function `create_drop(...)` (one transaction), so a drop cannot exist without its seats.

## Why it is better (evidence)
Each gap was tried with raw SQL before the fix and is now a test that expects the database to refuse it (`api/tests/test_constraints.py`): `seat_no` 0, 6 or 500 on a capacity-5 drop; a seat holding another drop's entry; an allocation naming a seat that holds a different entry or a different drop; `UPDATE drops SET capacity` while seats exist. Invariant 1 now holds because of structure instead of because the application inserted the right number of rows. Cost: one extra int column per seat row and two extra unique indexes; measured at 52,000 entries the integrity view still runs in about 1-2 ms (review log §7).

## Invariants check
1. 500 seats never 501: **strengthened** (structural cap).
2. One identity, one entry, one seat per drop: **strengthened** (cross-drop keys).
3. Postgres is the source of truth: unaffected.
4. Arrival time never decides winners: unaffected.
5. Bot detection never decides winners: unaffected.
6. Simulator labels never reach decisions: unaffected.
7. Draw determinism from a committed seed: unaffected.
8. Guarded entry updates: unaffected (the claim order, seat first then ledger row, is what the design already says).

## Contract impact
None. No endpoint, field or code changes.

## Plans edited
| Plan | Section | Summary of edit |
|---|---|---|
| 02 | §4.3, §4.4, §4.5 | Notes on the added columns, keys and `create_drop` |
| 06 | §5.2, §5.6 | Create drops through `create_drop`; capacity is immutable; reset through `admin_reset_drop` |
| 08 | §5 step 10-12 | Claim order is forced by the composite FK; ledger rows carry `run_no`; integrity `extra` fields |

## Rollback
Drop the two composite FKs on `allocations`, the `seats_entry_in_drop_fk` and `seats_drop_capacity_fk` constraints, the `seats.capacity` column and the `UNIQUE (id, drop_id)` / `UNIQUE (id, capacity)` / `UNIQUE (id, entry_id)` keys in a new forward migration, and restore `seat_no`-only checks. The integrity view still reports `seats_total = capacity` and `oversold`, so detection stays; only prevention is lost.
