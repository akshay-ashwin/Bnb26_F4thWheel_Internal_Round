# Plan 03 — Backend Core: App Skeleton, Contract Models, Error Envelope, Pools & Test Harness

| Field | Value |
|---|---|
| Design-doc sections | §5 Gateway concerns, §6 Backend API, §11 Conventions + all endpoint tables, §14 failure handling (503 paths) |
| Original owner | Akshay |
| Depends on | Plans 01, 02 |
| Unlocks | Plans 04–14; frontend mocks (15) and simulator (18) can code against the OpenAPI spec |
| Target time | 2 hours |

## 0. Mandatory operating protocol (read before writing anything)

1. Read `CLAUDE.md` (repo root) — it overrides this plan if they conflict.
2. Read `docs/implementation/PLAN_CHANGELOG.md`. If an earlier step edited this plan, the edited text is authoritative.
3. Read the review log of the previous plan in `docs/review-logs/` for handover notes.
4. Run the Pre-flight checks in section 3. Do not start building on a broken foundation.

Standing rules that apply to every line of work in this plan:

- **R1 — No AI attribution.** Never add Claude/Anthropic as author, co-author or contributor; no `Co-Authored-By` trailers, no "Generated with Claude Code" lines in commits or PRs; never touch git identity; never use `--no-verify`.
- **R2 — Better solution wins, and the future is rewritten.** If you find a better approach at any point, implement it (respecting the invariants in CLAUDE.md), write a Deviation Record in `docs/decisions/`, edit every affected later plan, and log it in `PLAN_CHANGELOG.md`.
- **R3 — Refine every step.** Do the Refinement Pass at the end of this plan before declaring it done, and refine the remaining plans if this step taught you something.
- **R4 — Review log.** Write `docs/review-logs/03-backend-core.md` in plain language from the template.

## 1. Goal

A production-shaped FastAPI service where every endpoint in the contract exists with its exact request/response models (returning `501 NOT_IMPLEMENTED` bodies until implemented), every response carries `server_time`, every error uses one envelope, Postgres and Redis pools are managed correctly across 4 uvicorn workers, and a test harness can spin up a clean database per test. At the end of this plan the OpenAPI spec at `/api/docs` IS the frozen contract.

## 2. Scope

In scope: app factory, settings, lifespan, pools, routers, Pydantic models for every endpoint, error hierarchy, middleware ordering, logging, health, OpenAPI snapshot, test fixtures.
Out of scope: endpoint logic (Plans 04–14).

## 3. Pre-flight checks

1. Plan 02 migrations applied; `v_drop_integrity` exists.
2. `docs/contract/README.md` and `error-codes.md` exist (Plan 01).

## 4. Implementation steps

### 4.1 Package layout under `api/app/`

`main` (app factory), `config` (settings), `db` (Postgres pool + transaction helpers), `cache` (Redis client + circuit breaker), `errors` (exception hierarchy + handlers), `schemas/` (Pydantic contract models, one module per area: drops, auth, entries, me, claim, stepup, admin, sim), `routers/` (public, admin, sim), `deps` (dependency providers: db, redis, current session, admin auth, idempotency key), `middleware/` (request id + timing, abuse slot, server_time), `services/` (business logic per area, empty now), `jobs/` (background tasks, empty now), `observability/` (logging, metrics hooks).

Rule: routers stay thin (parse → call service → shape response). Services own logic. This keeps the claim transaction reviewable in one place.

### 4.2 Settings

Typed settings object loaded from env (pydantic-settings). Validate at startup: secrets present and ≥ 32 bytes; refuse to start if `APP_ENV=prod` and `SIM_MODE=true`; log (without secrets) the effective configuration once at boot.

### 4.3 Pools and connection math

- Postgres: asyncpg pool per worker. Budget: `workers × DB_POOL_MAX + admin/sweeper headroom (≈10) < max_connections`. With 4 workers and max_connections 200, start at pool max 30 per worker. Write this formula in the review log.
- Set per-connection defaults on pool init: `statement_timeout` (e.g. 5 s general), `idle_in_transaction_session_timeout` (e.g. 10 s), application_name including worker pid.
- Provide a transaction helper that allows per-transaction `SET LOCAL lock_timeout` and `statement_timeout` (claim uses tighter values, Plan 08).
- Redis: one async client per worker with short socket timeouts (`REDIS_TIMEOUT_MS`, default 50 ms) and a simple circuit breaker: after N consecutive failures, mark Redis "down" for a cool-off period and let callers use fallbacks immediately instead of waiting for timeouts (needed for §14 "Redis down" behaviour). Expose `redis_available()` for services.

### 4.4 Error envelope and exception hierarchy

- One base application error carrying code, HTTP status, message, optional `retry_after_ms`, optional details.
- One subclass per error code from `docs/contract/error-codes.md`.
- Global handlers convert: application errors → envelope; Pydantic validation errors → `400 VALIDATION_ERROR` (the contract uses 400 for `INVALID_PHONE`; keep validation at 400 too for consistency, record the decision); asyncpg connection failures / pool timeouts → `503 SERVICE_UNAVAILABLE` with `Retry-After` header; unexpected exceptions → `500 INTERNAL` with request id, no stack trace leaked.
- When `retry_after_ms` is present, also set the standard `Retry-After` header (seconds, rounded up).
- Error responses ALSO include `server_time` (decide: top-level sibling of `error`; record it in the contract).

### 4.5 `server_time` on every response

Decision (recommended): every success model inherits a base model with a `server_time` field (ISO-8601 UTC with milliseconds) filled at serialization time, so it appears in the OpenAPI schema. Do not inject by rewriting bodies in middleware (slow, and invisible to the schema). Use a fast JSON response class (orjson).

### 4.6 Middleware order (outermost first) — document this diagram in the review log

1. Request ID + timing (assigns `X-Request-ID`, measures latency, feeds metrics hook later).
2. Error safety net.
3. Abuse layers L1–L3 slot (no-op now; Plan 12 fills it). It must run before routing and must never touch Postgres.
4. Routing → dependencies (session auth, admin auth, idempotency key extraction) → handler.

### 4.7 Routers and endpoint stubs

Create every endpoint from the contract with exact paths under `/api`, methods, request models, response models, and documented error responses:

- Public: `GET /drops/{id}`, `POST /auth/otp/request`, `POST /auth/otp/verify`, `POST /drops/{id}/entries`, `GET /drops/{id}/me`, `POST /drops/{id}/claim`, `POST /drops/{id}/step-up`.
- Admin (require `X-Admin-Key`): `POST /admin/drops`, `POST /admin/drops/{id}/phase`, `GET /admin/drops/{id}/metrics`, `GET /admin/drops/{id}/integrity`, `GET /admin/drops/{id}/export`, `GET /admin/drops/{id}/draw-proof`, `PUT /admin/abuse/config`.
- Sim: `POST /sim/telemetry` (only mounted when `SIM_MODE=true`).
- Ops: `GET /healthz` (process alive), `GET /readyz` (Postgres + Redis reachable; Redis failure reports degraded, not unready).

Each stub returns `501` with a `NOT_IMPLEMENTED` envelope (add this code to error-codes.md as dev-only). Admin auth uses constant-time comparison.

Model details to get right now (they are the contract):
- `GET /me` model: phase, `entry` nullable object (entry_id, status, rank?, waitlist_pos?, offer_expires_at?, step_up_required, admission_token?), `allocation` nullable (allocation_id, seat_no, confirmed_at), `poll_after_ms`. Add `dev_otp?` inside entry for step-up in SIM_MODE only (decision from Plan 11 foresight; mark as SIM-only field).
- Claim request `{admission_token}`; claim response `{allocation_id, seat_no, confirmed_at}`.
- All status strings as string literals/enums matching the DB CHECK lists exactly.

### 4.8 Logging

Structured JSON logs: timestamp, level, request_id, route, status, latency_ms, worker pid, user_public_id when known. Never log phone numbers, OTPs, session tokens, admission tokens, or the seed before reveal. Add a log-scrubbing filter as defence in depth.

### 4.9 OpenAPI snapshot

Export the OpenAPI JSON to `docs/contract/openapi.json` via a make target. CI later compares the live spec to the snapshot and fails if they differ without a contract change note (Plan 14 adds the check). Freeze: after this plan, update `docs/contract/README.md` header to "FROZEN".

### 4.10 Test harness

- Fixtures: app client (httpx AsyncClient against the ASGI app), a clean database per test module (truncate all tables in FK-safe order; faster than recreating), flushed Redis DB index reserved for tests, admin headers, helper to create a verified session (later plans implement it).
- A concurrency helper that fires N requests truly concurrently (gather) with distinct sessions — used heavily in Plans 05, 07, 08, 10.
- A "Redis down" fixture that points the client at a dead port to test fallbacks.

## 5. Verification / Definition of Done

1. `/api/docs` lists all 15 contract endpoints + ops endpoints with full schemas.
2. Every stub returns the envelope with `server_time`; a validation error returns `400 VALIDATION_ERROR` in the envelope.
3. Killing Postgres → API requests that need it return `503` with `Retry-After` within ~1 s (not hanging); `/readyz` reports unready. Killing Redis → `/readyz` reports degraded, `/healthz` ok.
4. Starting with `APP_ENV=prod SIM_MODE=true` fails fast with a clear message.
5. `docs/contract/openapi.json` committed; contract README says FROZEN.
6. Pool math documented; 4 workers start without exhausting connections.

## 6. Plan-update obligations

- If you changed the `server_time` placement in errors or added fields (`dev_otp`, `NOT_IMPLEMENTED`), update Plans 15, 16, 18 (client parsing).
- If pool sizes differ, update Plan 19 tuning section with the starting values.

## 7. Review log must explain

- The request's journey through middleware → dependencies → handler, as a numbered story.
- How the error envelope is produced and what a client should do for each class of error.
- How Redis failure is detected fast (circuit breaker) and why that matters during a 50k-user burst.
- That the contract is now frozen and where the canonical copy lives.

## Close-out sequence (do all of these, in order)

1. **Definition of Done.** Run every check in the Verification section. Record the actual numbers (tests passed, latencies, oversold counts) — never write a number you did not observe.
2. **Refinement Pass (R3).** Re-read the full diff as a strict reviewer; remove dead code and debug output; check names against `docs/GLOSSARY.md`; walk each failure path (Postgres slow, Redis gone, request retried, two tabs at once); run the whole test suite, not just the new tests; ask "is there a simpler way?" and apply R2 if yes.
3. **Downstream sync (R2).** Open each plan listed in "Plan-update obligations" (and any other later plan you affected). Edit stale instructions, mark them `> Updated by D-00X`, and append to `PLAN_CHANGELOG.md`. If nothing changed, write "no downstream changes" in the review log.
4. **Review log (R4).** Write `docs/review-logs/03-backend-core.md` from the template, covering every item in this plan's "Review log must explain" list.
5. **Attribution check (R1).** List unpushed commits with their author and full message; confirm the author is the repo owner and no message contains AI attribution. Fix by rewording before pushing if needed.
6. **Commit** the plan's final state with a conventional message (e.g. `feat(alloc): atomic claim with skip-locked seats`) and the repo owner's identity.
7. **Chat summary.** 5–10 lines: what is done, key numbers, deviations, link to the review log, what Plan 04 starts with.
