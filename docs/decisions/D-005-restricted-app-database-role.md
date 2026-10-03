# D-005 — The API uses a restricted database role; migrations and test database share one path

Date: 2026-10-04  ·  Raised during: Plan 02  ·  Status: ADOPTED

## The original plan said
Plan 02 §4.6: "App role: SELECT/INSERT/UPDATE on operational tables, INSERT-only on allocations and abuse_events, no DDL", and a `SECURITY DEFINER` reset function. Plan 01 left `DATABASE_URL` pointing at the Postgres superuser (`POSTGRES_USER`) for both the api and the migrations runner, and `fd test-api` / `fd migrate` / `fd reset-db` as stubs (Plan 03 was to implement `test-api`).

## What we do instead
1. A role `fairdrop_app` (NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE, created by migration `20261004100300`). `fd migrate` gives it LOGIN and the password `APP_DB_PASSWORD` (new secret in `.env.example`, sent to psql on stdin, never on a command line). `DATABASE_URL` in `.env.example` now connects as `fairdrop_app`. The migrations runner and the test fixtures keep using the superuser that owns the schema.
2. Narrow grants, no general permissions system: no DDL; no DELETE/TRUNCATE except `DELETE` on `idempotency_records` (TTL cleanup); no INSERT on `drops` or `seats` (only through `create_drop`); column-level UPDATE where a column must never change (`drops.capacity`, `run_no`; `seats` may change only `status`, `entry_id`, `sold_at`; `entries` identity columns); `allocations` and `abuse_events` are SELECT + INSERT only.
3. `SECURITY DEFINER` functions `create_drop` and `admin_reset_drop` with `search_path = pg_catalog, public`, `EXECUTE` revoked from PUBLIC and granted to `fairdrop_app` only. Reset sets a transaction-local flag `fairdrop.resetting`; a trigger on `allocations` refuses UPDATE always and DELETE/TRUNCATE unless the flag is on and the caller is not `fairdrop_app` (so the application role cannot use the flag even if it were ever granted DELETE). The grants are the real access control; the trigger also stops mistakes by the schema owner.
4. `uv run fd migrate` (idempotent, dumps `api/migrations/schema.sql` through dbmate), `uv run fd reset-db` (removes the `fairdrop_pgdata` volume, migrates again) and `uv run fd test-api` (migrates `fairdrop_test` with the same files as the real database, then runs pytest in the api container) are implemented now. `test-api` is pulled forward from Plan 03 because Plan 02's constraint tests need it. The `_test` suffix guard is enforced in `fd` and again in `api/tests/conftest.py`. CI's `api-tests` job calls `uv run fd test-api`.

## Why it is better (evidence)
With the compose superuser as the API login, "append-only ledger" and "no DDL" are claims about code, not about the database: a superuser ignores grants. With the restricted role they are tested: `api/tests/test_roles_and_reset.py` connects as `fairdrop_app` and shows UPDATE, DELETE, TRUNCATE, `CREATE TABLE`, direct seat inserts and capacity changes are refused, and that setting the reset flag as `fairdrop_app` does not help even with DELETE granted. One migration path means the tests run on exactly the schema production gets; nothing re-creates it inside pytest.

## Invariants check
1. 500 seats never 501: **strengthened** (the application cannot insert seats).
2. One identity, one seat: unaffected.
3. Postgres is the source of truth: unaffected (still one database).
4-7. Unaffected.
8. Guarded entry updates: unaffected; the column-level grant on `entries` keeps `status` updatable.

## Contract impact
None for the HTTP API. Environment: new secret `APP_DB_PASSWORD`; `DATABASE_URL` now uses the role `fairdrop_app`. An existing `.env` must be regenerated (`uv run fd secrets --force`, then `uv run fd reset-db`).

## Plans edited
| Plan | Section | Summary of edit |
|---|---|---|
| 02 | §4.1, §4.6, §5 | As-built notes |
| 03 | handover | `DATABASE_URL` is the restricted role; `fd test-api` exists; extend, do not replace |
| 05 | §3 | `idempotency_records` shape and the application grants |
| 06 | §5.2, §5.6 | Seed fields stay updatable by the app; reset keeps phase, seed and mode untouched |
| 07, 09 | entries steps | Which `entries` columns the app role may update |

## Rollback
Point `DATABASE_URL` back at the superuser and remove the grants migration. The trigger and functions keep working for the owner. Nothing in the schema depends on the application role existing.
