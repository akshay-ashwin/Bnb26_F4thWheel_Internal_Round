# Review Log — Plan 02: Database Schema, Constraints, Migrations & Integrity Views

Date: 2026-10-04  ·  Commits: none yet (work is uncommitted in the `plan-02` worktree, on top of `bfda598`)  ·  Status: DONE WITH CAVEATS

> CONTRACT CHANGE — teammates must know: **none to the HTTP API.** Two things change for developers: (1) `DATABASE_URL` in `.env.example` now logs in as the restricted role `fairdrop_app` and there is a new secret `APP_DB_PASSWORD`; an existing `.env` must be regenerated (`uv run fd secrets --force`, then `uv run fd reset-db`). (2) Plain `docker compose run api pytest` no longer works; use `uv run fd test-api` (CI was updated). Plan 08 will expose the integrity view's extra fields under `extra`; the six public fields are unchanged.

**Caveats in one place:** `uv` is not installed on the machine I used, so I ran the tasks as `PYTHONPATH=tools python3 -m fd <task>` (same code); the git hooks, `uv run fd lint` as one command, and CI were not run (lint was run by hand: ruff and mypy in the `api` container for `api/`, and in a `python:3.12-slim` container for `tools/`). Only macOS was used. The latency numbers come from a very busy laptop (see section 7). `api/migrations/schema.sql` is for reading, not for loading (section 8).

## 1. What this step was for

Build the PostgreSQL schema whose constraints make overselling and double-holding impossible instead of merely unlikely, a SQL view that proves it live, and the tooling to migrate and test it. After this plan a reviewer can try to break integrity with raw SQL and fail.

## 2. What I built

- `api/migrations/20261004100000_core_schema.sql`: all tables and constraints (drops, users, sessions, entries, seats, allocations, idempotency_records, abuse_events, drop_runs, app_settings, system_state).
- `api/migrations/20261004100100_functions.sql`: `create_drop()` (drop plus seats in one transaction), the append-only trigger on `allocations`, `admin_reset_drop()`.
- `api/migrations/20261004100200_integrity_view.sql`: `v_drop_integrity`.
- `api/migrations/20261004100300_app_role_grants.sql`: the restricted role `fairdrop_app` and its grants.
- `api/migrations/schema.sql`: the reviewable dump, written by dbmate itself.
- `api/seeds/dev_demo_drop.sql`: dev-only demo drop (not a migration).
- `api/tests/`: `conftest.py`, `helpers.py`, `test_constraints.py`, `test_roles_and_reset.py`, `test_integrity_view.py`, `test_integrity_view_perf.py` (61 tests with the existing health test).
- `tools/fd/tasks.py`, `cli.py`, `repo.py`: `fd migrate`, `fd reset-db`, `fd test-api` (plus `tools/tests/test_tasks.py`).
- `infra/docker-compose.yml` (migrate service), `.env.example`, `.github/workflows/ci.yml`, `README.md`, `api/README.md`, `docs/GLOSSARY.md`.
- `docs/decisions/D-004-...md`, `D-005-...md`, plan edits and `PLAN_CHANGELOG.md` lines (section 5).

## 3. How it works, in simple words

**Why 501 sold seats is impossible.** A drop of 500 seats is 500 rows in `seats`, created together with the drop. Selling a seat means changing one existing row from `free` to `sold` and writing the buyer's entry id into it. There is no counter that could be read twice. Take the last seat: Anna and Ben both press Claim at the same moment. Each claim runs in one database transaction and asks for "the lowest free seat, skipping rows someone else is already locking". Anna's transaction locks seat 500. Ben's finds no free row and is told SOLD_OUT; nothing is written for him. If a bug somehow let both through, the database itself still refuses: seat 500 can hold only one entry (`UNIQUE(drop_id, entry_id)` plus the allocation's own uniqueness on `seat_id`), an entry can hold only one seat, and there is no way to add a 501st row because every seat carries its drop's capacity and `seat_no` must be between 1 and that capacity (D-004).

**What the integrity view adds.** The constraints stop bad rows from being written one at a time. They cannot say "the seats table, the allocations ledger and the entries' statuses agree with each other". `v_drop_integrity` checks exactly that, every time it is read, from the tables (never from counters): sold seats without a ledger row, ledger rows pointing at a seat that is not sold, ALLOCATED entries without a ledger row, and so on. `invariant_ok` is true only when everything agrees, and a drop with no seat rows reads false.

**What the application can and cannot do.** The API logs in as `fairdrop_app`. It can claim a seat (update three columns of a seat row, add a ledger row) but cannot add or delete seats, create drops except through `create_drop()`, change a drop's capacity, or rewrite the ledger. The reset routine is the one place that deletes ledger rows, and the trigger on the ledger checks a transaction-local flag and who is asking.

## 4. Why I did it this way

- **Structural seat cap and cross-drop keys (D-004).** The design relied on "exactly `capacity` rows" but nothing enforced it. Each gap was tried with raw SQL; each is now a failing-by-design test. Cost is one int column per seat row.
- **Restricted role (D-005).** With the compose superuser as the API login, "append-only" and "no DDL" would be promises about code (a superuser ignores grants). With `fairdrop_app` they are tested.
- **Column-level UPDATE grants** where a column must never change. The reset flag alone would be bypassable (any session can set a custom setting); that is why the grants are the primary control and the trigger a second layer that also refuses the app role.
- **Text + CHECK instead of enums, `timestamptz` everywhere, uuid ids except `seats.id` and log tables** as the plan said. `GENERATED ALWAYS AS IDENTITY` instead of `bigserial`; no `pgcrypto` (`gen_random_uuid()` is built in since PostgreSQL 13).
- **One migration path.** `fd test-api` runs dbmate against `fairdrop_test` and only then pytest; nothing in pytest recreates the schema. The `_test` suffix guard is checked in `fd` and again in `conftest.py`.
- **Rejected:** Write-once triggers on `seed_commit`/`seed`/`entry_set_hash` (from the old prototype). Plan 06 regenerates seed and commitment on reset and defines the hashing, so a write-once rule now would fight it. Plan 06/09 can add it with the reset flag.

## 5. Changes from the plan (Rule R2)

| What the plan said | What I did instead | Why | Deviation record | Future plans edited |
|---|---|---|---|---|
| `seats` exactly as design | Added `seats.capacity` (FK + CHECK), cross-drop composite FKs, `sold_at` CHECK, `UNIQUE(id, entry_id)` | Nothing enforced "exactly `capacity` rows" or "same drop" | D-004 | 02, 06, 08, 14 |
| App role with SELECT/INSERT/UPDATE on operational tables | Narrow grants, no INSERT on drops/seats, column-level UPDATE, `create_drop()` as the only creator | Make the integrity claims true at the database, not only in code | D-005 | 02, 03, 05, 06, 07, 09 |
| `fd test-api` is Plan 03's job | Implemented now; CI `api-tests` calls it | Plan 02's tests need a migrated `fairdrop_test` | D-005 | 03 |
| Free-seat index `ON seats (drop_id) WHERE status='free'` | `(drop_id, seat_no) WHERE status='free'` | Claim orders by `seat_no`; measured below | D-004 | 08 |
| `v_drop_integrity`: ~10 ms, test < 20 ms | Plan shape + loose 100 ms ceiling asserted; real numbers printed and recorded here | Timing asserts flake on shared runners | none (plan §6 allows) | 02 |
| `v_drop_integrity` extras not specified | Two extra fields: `sold_seat_entry_not_allocated`, `free_seat_with_sold_at`. A separate `cross_drop_violations` field was tried and removed (the composite FKs make it unrepresentable and it cost an extra pass over `entries`; an entry of another drop is still caught by `sold_seat_entry_not_allocated`) | Measured | none | 08 |
| `admin_reset_drop`: delete allocations, free seats, delete entries and idempotency records, bump `run_no` | Same, and it also clears `drawn_at`, `closed_at`, `done_at`, `entry_set_hash` | Those describe the finished run | none | 06 |
| `idempotency_records` with `drop_id` | `drop_id NOT NULL` with FK | Reset deletes by drop; Plan 05 always has a drop | none | 05 |
| `seed` null until reveal (design) | `create_drop` takes an optional `seed` | Plan 06 §5.2 stores the seed from creation | none | 06 |

## 6. Refinements made during this step (Rule R3)

- **First draft of the integrity view was wrong in shape.** Its plan contained a hash join that scans all of `entries` (54,000 rows) to check at most 500 seats, only hidden because the test data had no sold seats yet. My own plan-structure assertion caught it. Second draft forced per-row primary-key lookups, but `LIMIT 1` on tiny tables made the planner pick a sequential scan per probe (1,580 buffers for 500 probes). Third (final) draft reads `entries` once and compares three small per-drop sets: 86 buffers total.
- **Bugs found by my own tests:** two tests asserted the wrong constraint name (Postgres checks CHECK constraints alphabetically and unique indexes before foreign keys); fixed the tests, the schema was right.
- **`fd migrate` after `reset-db` failed once** (`driver: bad connection`): a fresh Postgres volume restarts the server after initialisation, just after the health check goes green. `fd` now retries the (idempotent) migration up to 3 times.
- **`.env.example` change broke two existing `fd` unit tests** (they expected the superuser in `DATABASE_URL`); updated them to assert the opposite (restricted role, never the superuser password).
- Ran ruff format/check and mypy strict; added a mypy override because asyncpg ships no type information.

## 7. How to verify it yourself

From the repo root (the same on PowerShell; with `uv` installed use `uv run fd ...`):

```
uv run fd secrets --force     # regenerate .env (adds APP_DB_PASSWORD)
uv run fd reset-db            # fresh volume, all four migrations, writes api/migrations/schema.sql
uv run fd migrate             # run again: nothing to apply, schema.sql byte-identical
uv run fd test-api            # migrates fairdrop_test, then pytest
uv run fd test-api -- -s -k perf   # prints the latency numbers below
```

What I observed (macOS, Docker Desktop, PostgreSQL 16.15):

- **Tests:** `fd test-api`: **61 passed** (25 constraint tests, 17 role/reset/atomicity tests, 17 integrity-view tests, 1 performance test, 1 existing health test), run twice in a row from a fresh volume. `fd` unit tests: 27 passed, 5 skipped (pre-existing skips; I did not investigate why). ruff, ruff format and mypy strict clean for `api/` and `tools/`.
- **Integrity view, 52,000 entries in one drop (plus a second drop with 2,000), 500 seats, `ANALYZE` after the load, 50 timed runs per state after 3 warm-ups.** Three clean runs of the final view, asyncpg round trip, ms (`p50 / p95 / max`):

  | State | Run 1 | Run 2 | Run 3 |
  |---|---|---|---|
  | empty (0 sold) | 0.81 / 2.28 / 7.31 | 0.46 / 0.55 / 1.04 | 0.41 / 1.20 / 1.68 |
  | half sold (250) | 1.68 / 2.83 / 8.31 | 0.94 / 2.61 / 8.90 | 1.06 / 2.25 / 3.63 |
  | fully sold (500) | 1.76 / 2.10 / 3.40 | 1.34 / 1.71 / 2.55 | 1.68 / 1.93 / 2.56 |

  Server-side execution (`EXPLAIN (ANALYZE)` execution time, what psql would show; I did not separately time psql) for the fully sold state: p50 1.70 / 1.74 / 1.70, p95 1.91 / 2.51 / 2.11, max 2.62 / 2.91 / 2.60. Planning time p50 about 0.3 ms (paid once per prepared statement). Final plan at 500 sold: `Execution Time: 1.268 ms`, `Buffers: shared hit=86`; `entries` is read once with a bitmap index scan on `entries_drop_status_rank_idx` (63 heap blocks); no sequential scan of `entries`.
- **Honest caveat on those numbers:** the laptop was heavily loaded the whole time (load average 17 to 35 on 8 CPUs, another Postgres stack running). Runs of the same final view during the worst contention were much worse: fully sold asyncpg p50 8.43, p95 24.21, max 42.86 ms and server p95 52 ms. Of six runs of the final view, five had p95 at or below 2.5 ms and one (heavy contention) did not meet the 10 ms target. The CI assertion is therefore only a structural one plus a 100 ms ceiling.
- **Free-seat index** (9,900 of 10,000 seats sold, `EXPLAIN (ANALYZE, BUFFERS)` of the claim's lowest-free-seat query): with `(drop_id, seat_no) WHERE status='free'`: 133 buffers, 2.1 ms. With the design's `(drop_id) WHERE status='free'` the planner ignored it and walked the unique index past 9,900 sold seats: 19,899 buffers, 5.4 ms (same as with no partial index: 3.9 ms). The 133 includes dead index entries from the same transaction's updates; after VACUUM it is smaller.
- **The three kinds of refusal, tried by hand through the tests:** unique violation (`entries_drop_user_key`), foreign-key violation (`seats_entry_in_drop_fk`, `allocations_seat_entry_fk`), check violation (`seats_seat_no_within_capacity`), permission denied for `fairdrop_app` on ledger UPDATE/DELETE/TRUNCATE, `CREATE TABLE`, direct seat insert, capacity change.

## 8. Risks, limitations, and things I'm unsure about

- **`schema.sql` is for reading, not loading.** dbmate's image uses `pg_dump` 18.6 against server 16.15; the file contains `SET transaction_timeout` (unknown to PostgreSQL 16) and a `\restrict` line. Do not use `dbmate load` on it. The migration files are the source of truth.
- **Latency numbers are from a busy laptop** (section 7), and `uv`-based flows (hooks, `uv run fd lint`, CI) were not run. CI has still never run.
- **`create_drop` and `admin_reset_drop` are executable by `fairdrop_app` by design** (the admin API calls them), so a compromised API process could reset a drop. That is not an integrity breach (the ledger rows are deleted by a defined routine), but it is not "append-only against the application" in the strongest sense. The strongest statement the tests support: the application cannot rewrite or delete ledger rows by SQL; it can only call the reset routine.
- **The reset flag is a plain custom setting.** Any session can set it; protection is the grants plus the caller check in the trigger. A superuser can still do anything (they own the schema).
- **`drops.capacity` can never change** (the seats' foreign key). A different size means a new drop. Plan 06 already creates drops with a capacity.
- **Column-level UPDATE grants can be too narrow for a later plan.** If a plan needs another column it needs a new migration (the plans were annotated).
- **No write-once rule on `seed_commit`/`seed`/`entry_set_hash`** (section 4).
- The `fd test-api` retry loop hides a genuinely broken database for up to 6 seconds; it prints each failed attempt.
- Port conflicts on this machine (an older stack using 8000 and 6379, which `fd doctor` reports) did not matter here because only Postgres (15432) was started.

## 9. What the next plan needs to know

Plan 03: `DATABASE_URL` is the restricted role; do not build the pool from `POSTGRES_*`. `fd test-api` already exists (extend it); `api/tests/conftest.py` gives `db` (owner connection on an empty database) and `app_db` (restricted role) fixtures and `helpers.py` has `make_drop`, `make_entry`, `claim`, etc. The pytest event loop is session-scoped (`pyproject.toml`). The health check may now check `SELECT 1` as `fairdrop_app` (Plan 01's note asked Plan 03 to decide).
Plans 05-09: see the `> Updated by D-004/D-005` notes in each plan (which columns the app may update, `run_no` is required on entries and ledger rows, drops are created only by `create_drop`, reset goes through `admin_reset_drop` and leaves phase/mode/seed alone).
Plan 08: claim order is seat first, then ledger row; query `v_drop_integrity` with `WHERE drop_id = $1`.

## 10. Attribution check (Rule R1)

No commit exists yet, so there is nothing to scan. Nothing I wrote contains an AI co-author trailer, "generated with" line, Claude Code URL or robot emoji (searched the changed and new files). The session's tooling suggested a `Co-Authored-By` trailer for commits; I will not add it, per `CLAUDE.md`. I did not change `git config`; the worktree inherits the clone's identity (`HaleHenry-rig <akshay.kumar1472006@gmail.com>`), which is not the repository owner identity named in the Plan 01 log, so the human must confirm the identity before the first commit. `--no-verify` was not used; the commit hooks are not installed in this worktree (`fd hooks` needs `uv`).
