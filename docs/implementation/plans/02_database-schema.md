# Plan 02 — Database Schema, Constraints, Migrations & Integrity Views

| Field | Value |
|---|---|
| Design-doc sections | §7 What lives where, §7 Allocation integrity, §12 Database schema, §13 State machine |
| Original owner | Akshay |
| Depends on | Plan 01 |
| Unlocks | Plans 03–11, 14 |
| Target time | 1.5 hours |

## 0. Mandatory operating protocol (read before writing anything)

1. Read `CLAUDE.md` (repo root) — it overrides this plan if they conflict.
2. Read `docs/implementation/PLAN_CHANGELOG.md`. If an earlier step edited this plan, the edited text is authoritative.
3. Read the review log of the previous plan in `docs/review-logs/` for handover notes.
4. Run the Pre-flight checks in section 3. Do not start building on a broken foundation.

Standing rules that apply to every line of work in this plan:

- **R1 — No AI attribution.** Never add Claude/Anthropic as author, co-author or contributor; no `Co-Authored-By` trailers, no "Generated with Claude Code" lines in commits or PRs; never touch git identity; never use `--no-verify`.
- **R2 — Better solution wins, and the future is rewritten.** If you find a better approach at any point, implement it (respecting the invariants in CLAUDE.md), write a Deviation Record in `docs/decisions/`, edit every affected later plan, and log it in `PLAN_CHANGELOG.md`.
- **R3 — Refine every step.** Do the Refinement Pass at the end of this plan before declaring it done, and refine the remaining plans if this step taught you something.
- **R4 — Review log.** Write `docs/review-logs/02-database-schema.md` in plain language from the template.

> Updated by D-001 (2026-10-04): stack rules apply to this plan. Everything runs through Docker Compose and `uv run fd <task>` (no `make`, no host Python or Node), files are LF, Node is 24 LTS (D-002), macOS and Windows are both supported, and fullstack-dev-skills may be used as advice but never its `project:*` workflow commands. CLAUDE.md always wins. See CLAUDE.md "Stack and platform rules" and `docs/decisions/D-001-cross-platform-docker-uv-node22-skills.md`. Read any `make X` below as `uv run fd X`.
> Plan 01 handover (2026-10-04): `api/migrations/` exists (empty, `.gitkeep`). The `migrate` compose service is a stub in profile `tools` using `ghcr.io/amacneil/dbmate:2` (verified: dbmate 2.36.0) with `command: ["--help"]`; replace the command and wire `uv run fd migrate` / `reset-db` (currently print "not implemented yet (Plan 02)"). Its `DATABASE_URL` is built in `infra/docker-compose.yml` from the `POSTGRES_*` variables with `?sslmode=disable` (dbmate needs it). Postgres is reachable from the host on **15432** (remapped from 5432, which a local PostgreSQL often owns); inside the compose network it is `postgres:5432`. `reset-db` must remove the `fairdrop_pgdata` named volume (project name `fairdrop` is set in the root `compose.yaml`).

## 1. Goal

Create the Postgres schema whose constraints make overselling and double-holding structurally impossible, plus the SQL views that prove it live. After this plan, a reviewer can try to break integrity with raw SQL and fail.

## 2. Scope

In scope: migration tooling, all tables, constraints, indexes, enum-like checks, seat-generation routine, integrity view(s), run-history table, settings table, constraint tests.
Out of scope: application code that uses them (later plans).

## 3. Pre-flight checks

1. `uv run fd up` healthy; you can connect to Postgres with `docker compose exec postgres psql ...` (psql lives in the container, not on the host).
2. Plan 01 review log read; directory `api/migrations/` exists.

## 4. Implementation steps

### 4.1 Migration tooling

Choose a plain-SQL migration runner (recommended: dbmate or yoyo, or Alembic used only with raw SQL). The app uses asyncpg without an ORM, so migrations must be hand-written SQL that a reviewer can read. Requirements: numbered, forward-only for the hackathon, each migration transactional, a `uv run fd migrate` task (wrapping the compose `migrate` service), and a `schema.sql` dump committed after each migration for review.

> Updated by D-001 (2026-10-04): the runner is never installed on the host. Use dbmate's official container image through the compose `migrate` service (this is why dbmate is the recommended default; yoyo/Alembic would run inside the `api` image). The `schema.sql` dump must be written by the tool inside the container (dbmate `dump`), not by a shell redirect: PowerShell 5.1's `>` re-encodes output as UTF-16 and can add CRs. Migrations and `schema.sql` are LF (Plan 01 §4.2).

### 4.2 Extensions and conventions

- Enable `pgcrypto` (for `gen_random_uuid()`).
- All timestamps `timestamptz`, stored UTC. All IDs `uuid` except `seats.id` and log tables (`bigserial`).
- Status/phase/mode columns are `text` with `CHECK` constraints (not Postgres enums — easier to evolve in a hackathon; record this).

### 4.3 Tables (from design doc §12, with the additions below — each addition is a recorded decision)

1. **drops** — exactly as design: id, name, capacity (>0), mode (fair|fifo), phase (SCHEDULED|OPEN|CLOSED|DRAWN|CLAIMING|DONE), reg_opens_at, reg_closes_at, claim_window_s (default 120), seed_commit (not null), seed (null until reveal), entry_set_hash, created_at.
   Additions: `window_s int NOT NULL` (registration window length — the admin create call supplies it but the design schema has no column for it); `run_no int NOT NULL DEFAULT 1` (incremented on reset, so FIFO and Fair runs on the same drop are distinguishable for the ghost overlay); `drawn_at`, `closed_at`, `done_at` timestamps (audit + dashboard story strip).
2. **users** — as design: id, public_id (unique, random), phone_hash (unique), first_device_id, first_ip, created_at.
3. **sessions** — as design: id, user_id FK, device_id, ip, ua_hash, created_at, revoked_at; index on user_id. Addition: index on `(user_id, device_id) WHERE revoked_at IS NULL` to support "re-verify returns existing session for same device".
4. **entries** — as design, including the full status CHECK list (REGISTERED, OFFERED, STEP_UP_REQUIRED, WAITLISTED, NOT_SELECTED, OFFER_EXPIRED, ALLOCATED, DISQUALIFIED), `UNIQUE(drop_id, user_id)`, `UNIQUE(drop_id, draw_rank)`, the two indexes.
   Additions: `offered_at`, `allocated_at`, `step_up_passed_at`, `status_changed_at` timestamps; `run_no int` copied from drop at insert; `request_count int default 0` is NOT added (request counts belong to metrics/simulator, not the decision table).
5. **seats** — exactly as design: bigserial id, drop_id, seat_no, status (free|sold), entry_id FK nullable, sold_at, `UNIQUE(drop_id, seat_no)`, `UNIQUE(drop_id, entry_id)`, `CHECK ((status='free') = (entry_id IS NULL))`, partial index on free seats. Note: Postgres treats NULLs as distinct in unique constraints, so many free seats with `entry_id NULL` are allowed — this is the desired behaviour; verify it in tests.

> Updated by D-004 (2026-10-04): `seats` also carries `capacity` (FK `(drop_id, capacity)` to `drops(id, capacity)` plus `CHECK (seat_no BETWEEN 1 AND capacity)`), so more than `capacity` seat rows is impossible; `entry_id` is tied to the same drop by the composite FK `(entry_id, drop_id)` to `entries(id, drop_id)`; `sold_at` is set exactly when sold; the free-seat index is `(drop_id, seat_no) WHERE status = 'free'` (the claim orders by `seat_no`; measured, the design's `(drop_id)` index was never chosen and the claim walked 9,900 sold seats, see the Plan 02 review log). The NULL-distinct behaviour of `UNIQUE(drop_id, entry_id)` is kept on purpose and tested, including the index metadata (`indnullsnotdistinct = false`).
6. **allocations** — append-only ledger as design: id, drop_id, entry_id UNIQUE FK, seat_id UNIQUE FK, idempotency_key, created_at. Addition: `run_no`. Add a trigger or revoke UPDATE/DELETE rights from the app role so the ledger is append-only during a run (reset is done by an admin-only routine that is allowed to clear it; see 4.6).

> Updated by D-004 and D-005 (2026-10-04): `allocations` is tied to its entry and seat by composite FKs `(entry_id, drop_id)` and `(seat_id, entry_id)`, has an index on `drop_id`, and is append-only by two layers: the application role has only SELECT and INSERT, and a trigger refuses UPDATE always and DELETE/TRUNCATE outside `admin_reset_drop`.
7. **idempotency_records** — as design, PK (user_id, key). Addition: `drop_id` column and index on `created_at` for cleanup.
8. **abuse_events** — as design, with index (drop_id, ts).

New tables (additions, record each):

9. **drop_runs** — one row per completed run: drop_id, run_no, mode, started_at, ended_at, summary jsonb (metrics snapshot), scorecard jsonb (uploaded by the evaluator in Plan 19), unique (drop_id, run_no). Purpose: the dashboard's ghosted FIFO-vs-Fair overlay needs the previous run's data after a reset.
10. **app_settings** — single-row-per-key configuration: key text PK, value jsonb, updated_at. Holds the abuse layer toggles and thresholds (Plan 12) so they survive API restarts; Redis caches them.
11. **system_state** — key text PK, value jsonb, updated_at. Holds the sweeper heartbeat used to detect Postgres/process outages and extend offers (Plan 11).

### 4.4 Seat generation routine

A SQL function (or a documented statement used inside the drop-creation transaction) that inserts `capacity` rows with seat_no 1..capacity, status free. Must run in the SAME transaction as the drop insert so a drop can never exist without its seats. Also a "reset seats" routine used by Plan 06 reset.

> Updated by D-004 (2026-10-04): as built, `create_drop(name, capacity, mode, window_s, claim_window_s, seed_commit, seed?, reg_opens_at?, reg_closes_at?)` creates the drop (phase `SCHEDULED`) and its seats in one call and returns the id. The application role has no INSERT right on `drops` or `seats`, so this function is the only way to create them. The reset routine is `admin_reset_drop` (section 4.6).

### 4.5 Integrity views (the live proof, design doc §11 `/integrity`)

Create a view `v_drop_integrity` returning per drop: seats_total (count of seat rows), sold, free, capacity, oversold (= greatest(0, sold − capacity), and additionally a hard check that seats_total = capacity), duplicate_entries_with_seats (entries holding > 1 seat — must be 0 by constraint, still computed), allocations_count, sold_without_allocation (sold seats with no allocation row), allocation_without_sold_seat, entries_allocated_count, entries_allocated_mismatch (ALLOCATED entries vs allocations), and `invariant_ok` = all of the above are consistent. These cross-table checks matter: constraints already block duplicates, so the interesting failures are inconsistencies between seats, allocations and entry statuses.

Make sure the view uses indexes and returns within ~10 ms at 52,000 entries / 500 seats (it is polled every second by the dashboard).

> Updated by D-004 (2026-10-04): as built, the view reads `entries` exactly once (this drop's ALLOCATED entries, via the `(drop_id, status, draw_rank)` index) and answers every check from three small per-drop sets, so the cost follows the seat count, not the entry count. Required field names are unchanged; two extra fields exist for Plan 08's `extra` object: `sold_seat_entry_not_allocated` and `free_seat_with_sold_at`. A drop with no seat rows reads `invariant_ok = false`. Measured numbers are in the review log.

### 4.6 Roles and reset

- App role: SELECT/INSERT/UPDATE on operational tables, INSERT-only on allocations and abuse_events, no DDL.

> Updated by D-005 (2026-10-04): role `fairdrop_app`, narrow grants (no INSERT on `drops`/`seats`, column-level UPDATE, no DELETE except idempotency cleanup), the reset flag is a transaction-local setting checked together with the caller's identity, and `create_drop` / `admin_reset_drop` are `SECURITY DEFINER` with a pinned `search_path` and `EXECUTE` revoked from PUBLIC. `admin_reset_drop(drop_id)` returns the new `run_no` and also clears `drawn_at`, `closed_at`, `done_at` and `entry_set_hash`; it leaves `phase`, `mode`, the seed fields, users, sessions and `drop_runs` alone.
- A `SECURITY DEFINER` function `admin_reset_drop(drop_id)` (or an admin-role connection used only by the reset path) that, in one transaction: deletes allocations for the drop, frees all seats, deletes entries, deletes idempotency records for the drop, increments run_no. Seed regeneration happens in application code (Plan 06) because the seed must come from a CSPRNG in the app.

### 4.7 Seed data for development

A dev-only seed script description: create one demo drop "Fair Drop Demo" capacity 500 mode fifo in SCHEDULED. Plan 06 replaces this with the admin API.

> Updated by D-005 (2026-10-04): as built, `api/seeds/dev_demo_drop.sql` (run with `docker compose exec -T postgres psql ... < ...` or the command in `api/README.md`) calls `create_drop`. It is not a migration, so the test database never gets a demo drop.

## 5. Constraint tests (write them now; they run in CI)

Using pytest against the real Postgres:

1. Inserting two entries with the same (drop, user) fails with a unique violation.
2. Setting two seats of the same drop to the same entry fails.
3. Setting a seat to sold with entry_id NULL fails the CHECK; setting free with an entry_id fails.
4. Inserting two allocations for the same entry fails; for the same seat fails.
5. Two different free seats with NULL entry_id coexist (NULLs distinct).
6. Two entries with the same draw_rank in one drop fail; NULL ranks for many entries are fine.
7. App role cannot UPDATE or DELETE allocations.
8. Drop + seats creation is atomic: simulate failure mid-transaction → no drop and no seats remain.
9. `v_drop_integrity` returns invariant_ok = true on an empty drop, on a fully sold drop, and correctly flags an artificially inserted inconsistency (inserted by a superuser connection in the test).
10. Performance: with 52,000 synthetic entries and 500 sold seats, the integrity view runs < 20 ms (record actual).

> Updated by D-005 (2026-10-04): the constraint tests run with `uv run fd test-api`, which migrates `fairdrop_test` with the same dbmate files as the real database before pytest starts. Test 10 asserts the plan shape (no sequential scan of `entries`) and a loose 100 ms regression ceiling; the real numbers (p50/p95/max over 50 runs per state) are printed and recorded in the review log, not asserted at 10 ms.

## 6. Decisions you must make and record

| Decision | Recommended default |
|---|---|
| Migration runner | dbmate |
| Text + CHECK vs enum | text + CHECK |
| Added columns/tables (window_s, run_no, timestamps, drop_runs, app_settings, system_state) | adopt; list each in review log |
| Append-only enforcement for allocations | role permissions + reset via admin function |

## 7. Verification / Definition of Done

All tests in §5 pass; `schema.sql` committed; `uv run fd migrate` is idempotent (running twice is harmless); review log includes the measured integrity-view latency.

## 8. Plan-update obligations

- Plans 06, 07, 08, 09, 11 reference column names — update them if you renamed anything.
- Plan 14 (`/integrity`, `/export`) and Plan 19 (evaluator) depend on the view's field names.
- If you chose not to add `drop_runs`, update Plans 06, 14, 17, 19 (ghost overlay data source).

## 9. Review log must explain

- In plain words, why 501 sold seats is impossible at the database level (physical rows + CHECK + UNIQUEs), with the example "two people grab the last seat".
- Every addition to the design-doc schema and why.
- What `v_drop_integrity` checks beyond the constraints, and why cross-table checks matter.
- Test results, including the integrity view latency number you measured.

## Close-out sequence (do all of these, in order)

1. **Definition of Done.** Run every check in the Verification section. Record the actual numbers (tests passed, latencies, oversold counts) — never write a number you did not observe.
2. **Refinement Pass (R3).** Re-read the full diff as a strict reviewer; remove dead code and debug output; check names against `docs/GLOSSARY.md`; walk each failure path (Postgres slow, Redis gone, request retried, two tabs at once); run the whole test suite, not just the new tests; ask "is there a simpler way?" and apply R2 if yes.
3. **Downstream sync (R2).** Open each plan listed in "Plan-update obligations" (and any other later plan you affected). Edit stale instructions, mark them `> Updated by D-00X`, and append to `PLAN_CHANGELOG.md`. If nothing changed, write "no downstream changes" in the review log.
4. **Review log (R4).** Write `docs/review-logs/02-database-schema.md` from the template, covering every item in this plan's "Review log must explain" list.
5. **Attribution check (R1).** List unpushed commits with their author and full message; confirm the author is the repo owner and no message contains AI attribution. Fix by rewording before pushing if needed.
6. **Commit** the plan's final state with a conventional message (e.g. `feat(alloc): atomic claim with skip-locked seats`) and the repo owner's identity.
7. **Chat summary.** 5–10 lines: what is done, key numbers, deviations, link to the review log, what Plan 03 starts with.
