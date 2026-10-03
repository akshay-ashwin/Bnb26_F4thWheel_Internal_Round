# Backend handoff (Saanvi: abuse and simulator, Ameya: frontend and dashboard)

Everything below describes code that exists and is tested in `api/`. The frozen contract is
`docs/contract/README.md`; what the backend adds on top is in `docs/contract/additions.md`; the draw
recipe is `docs/contract/draw.md`. Base path `/api`. Every response (and every error) has `server_time`.

## Run it

```
uv run fd secrets --force && uv run fd reset-db        # once: new APP_DB_PASSWORD, schema
docker compose up -d --wait api                        # needs API_RELOAD=false for load runs
uv run fd test-api                                     # real Postgres + Redis (db index 15)
docker compose run --rm --no-deps -e PYTHONPATH=/app api python scripts/stress.py   # real-HTTP stress
```

Env vars the backend reads (all in `.env.example`): `DATABASE_URL` (restricted role `fairdrop_app`),
`REDIS_URL`, `PHONE_PEPPER`, `SESSION_SECRET`, `TOKEN_SIGNING_KEY`, `ADMIN_KEY`, `SIM_TELEMETRY_KEY`,
`SIM_MODE`, `APP_ENV` (`prod` + `SIM_MODE=true` refuses to start), `COOKIE_SECURE`, `COOKIE_DOMAIN`,
`TRUSTED_PROXY_CIDRS`, `DB_POOL_MIN/MAX` (30 per worker; 4 workers = 120 of Postgres's 200),
`UVICORN_WORKERS`, `API_RELOAD` (must be `false` for any measurement). Optional knobs: `STEP_UP_THRESHOLD`
(60), `DRAW_GRACE_S` (3), `TOKEN_TTL_S` (60), `OTP_DEDUPE_S` (30), `RUN_JOBS`, `JOB_INTERVAL_S` (1).

## Flow in one page

1. `POST /auth/otp/request {phone, device_id}` -> `{request_id, expires_in_s, dev_otp?}`. `device_id`: 8-128 chars of `A-Za-z0-9._:-`. `phone`: an Indian mobile (10 digits starting 6-9; `+91`, `91`, `0` prefixes and spaces/dashes accepted), else `400 INVALID_PHONE`. Same phone within 30 s returns the same request. `dev_otp` only when `SIM_MODE=true`.
2. `POST /auth/otp/verify {request_id, otp, device_id}` -> `{session_token, user_public_id}` + `Set-Cookie fd_session` (httpOnly, SameSite=Lax, Path=/api, 24 h). The OTP is single use, 5 attempts, bound to the device that asked.
3. Send the session as the cookie **or** `Authorization: Bearer <token>` (cookie wins if both are present). A session is identified by its token only: **IP, device and user agent do not affect validity** (tested: three IPs and user agents, one session).
4. `POST /drops/{id}/entries` (optional `Idempotency-Key`) -> `201 {entry_id, status:"REGISTERED"}`; a repeat is `200` with the same body. `403 WINDOW_NOT_OPEN` / `WINDOW_CLOSED`, `404`.
5. Poll `GET /drops/{id}/me` every `poll_after_ms` (server-chosen, jittered, 500-30000 ms).
6. When `entry.admission_token` is present, `POST /drops/{id}/claim {admission_token}` with a **required** `Idempotency-Key` -> `200 {allocation_id, seat_no, confirmed_at}`.
7. Flagged winners (`status: STEP_UP_REQUIRED`) have `step_up_required: true`, no token, and (SIM_MODE) `dev_otp`; they call `POST /drops/{id}/step-up {otp}` with a required key -> `200 {status:"OFFERED"}`, then `/me` shows a token.

FIFO drops: entries and claims are open together; `/me` gives a token to a `REGISTERED` entry while the drop is `OPEN`; the request shapes are identical. Fair drops: window, then admin `close`, then `draw`, then offers.

### `/me` states (what each screen shows)

| `phase` / `entry.status` | Meaning | `poll_after_ms` (base) |
| --- | --- | --- |
| `SCHEDULED` | not open yet | 5000 |
| `OPEN`, no entry | can enter | 3000 |
| `OPEN`, `REGISTERED` (Fair) | in, waiting | 4000 |
| `CLOSED` | window over, draw pending | 1500 |
| `OFFERED` | won; token present; `offer_expires_at` | 1000 |
| `STEP_UP_REQUIRED` | won, must enter the code; `offer_expires_at` | 1000 |
| `WAITLISTED` | `waitlist_pos` (1 = next); seats free up as offers expire | 2000 |
| `ALLOCATED` | `allocation: {allocation_id, seat_no, confirmed_at}` | 15000 (terminal) |
| `NOT_SELECTED`, `OFFER_EXPIRED` | final | 15000 (terminal) |
| FIFO `OPEN` | claim now | 1000 |

Redis down doubles the poll interval. Use `server_time` and `offer_expires_at` for timers, never the client clock.

### Errors the UI must handle

`401 UNAUTHENTICATED` sign in again. `401 TOKEN_INVALID` call `/me` for a fresh token and retry the claim **with the same Idempotency-Key** (expired and session-bound tokens look the same). `403 NOT_OFFERED`, `409 OFFER_EXPIRED`, `409 SOLD_OUT` terminal. `423 STEP_UP_REQUIRED` show the code screen. `422 IDEMPOTENCY_KEY_REUSED` is a client bug (one key per user *action*). `429 RATE_LIMITED` / `OTP_THROTTLED` wait `retry_after_ms` (also the `Retry-After` header, seconds). `503 SERVICE_UNAVAILABLE` retry with the same key and backoff.

## Ameya

* Keep one `Idempotency-Key` per user action (for example "claim drop X") in `sessionStorage` until a terminal answer; reuse it on every retry. The backend replays the stored success (identical body, fresh `server_time`).
* Five tabs share one session and one entry: all five claims return the same seat. A token only works for the session that fetched it, so each tab must use the token from its own `/me`.
* Wi-Fi to mobile data does not log anyone out. Only an explicit revoke or 24 h expiry does.
* Draw-verify button: implement `docs/contract/draw.md` against `GET /api/drops/{id}/draw-proof` (public, after the draw).
* Dashboard data: `GET /admin/drops/{id}/metrics?window_s=60` (`X-Admin-Key`), `.../integrity`, `.../sim` (simulator ground truth, display only), `GET /admin/drops` to find the drop id, `.../export` (NDJSON, privacy-safe), `POST /admin/drops/{id}/phase {action: open|close|draw|reset, mode?}`.

## Saanvi

**Limiter insertion point.** `api/app/abuse/limiter.py`: `async def check(request) -> Decision`, where
`Decision(outcome="allow"|"reject", layer="L1".."L8"|None, code=<error code>|None, retry_after_ms=int|None)`.
`api/app/middleware.py` (`GatewayMiddleware`) calls it once per HTTP request, before routing, for every
path except `/api/healthz`, `/api/readyz`, `/api/admin/*` and `/api/sim/telemetry`. A reject becomes the
contract envelope with `Retry-After` (seconds, rounded up) and is counted as `rate_limited` and
`rate_limited:Lx`. If `check` raises, the request is served (fail open) and the error is logged.
Replace only the body of `check`; no route changes. Inside `check`: `request.app.state.settings`,
`.cache` (Redis through a circuit breaker: `await cache.run(lambda r: ...)` raises `RedisUnavailableError`;
`cache.client` is the raw client), `.metrics`; helpers in `app/netutil.py` (`client_ip(request, settings)`,
`network_of(ip)`, `ua_hash(request)`) and `app/security.py` (`verify_session_token(secret, token)` returns the
session UUID **without any database call**, `session_token_from(request)` is in `app/deps.py`). L1-L3
must not touch Postgres.

**Genuine users.** Nothing in the backend reserves capacity for authenticated users, and a duplicate
entry request costs three Postgres round trips, so key the buckets by session/user first and size them for
a genuine person who retries five times plus polling. Measured with the default allow-all limiter
(see below), 200 genuine users sending five entry attempts each during a 20,000-request junk flood all
ended with exactly one entry.

**Risk hooks** (`api/app/abuse/risk.py`, defaults are no-ops): `otp_request_guard(...)` raise
`AppError("OTP_THROTTLED", retry_after_ms=...)`; `on_identity_verified(...)`; `score_entry(...)` returns
`(risk_score, risk_flags)` and is stored on the entry before the insert (failures never block entry:
`["risk_unavailable"]`); `rescore_eligible(conn, drop_id)` runs inside the draw transaction before scores
are read. The draw turns `risk_score >= STEP_UP_THRESHOLD` (default 60) winners into
`STEP_UP_REQUIRED`; the backend never decides who is a bot and never down-weights anyone.
Not in the contract but worth using: `risk_flags` are shown in `/export`.

**Config and telemetry.** `PUT /admin/abuse/config {layers:{L1..L8:bool}, thresholds:{}}` stores to
`app_settings` and Redis `abuse:config` (JSON). `POST /sim/telemetry` needs header `X-Sim-Key`
(`SIM_TELEMETRY_KEY`), exists only when `SIM_MODE=true`, and is stored under `sim:latest`/`sim:series`
for the dashboard; no decision code reads them (a test enforces it). `X-Sim-Client-IP` is honoured only in
`SIM_MODE`. Redis keys the backend uses: `otp:req:{id}`, `otp:phone:{hash}`, `stepup:{entry}`, `m:{epoch_s}`
(metrics hashes), `drop:{id}:offer_head`, `sim:*`, `abuse:config`; use your own prefix (`rl:`).

**Simulator facts.** Users can be created without OTP only through the database; over HTTP use the OTP
flow with `dev_otp`. Tokens are bound to the session that fetched them. Claim every winner within the claim
window (`claim_window_s`, default 120). `GET /admin/drops/{id}/integrity` after every run must show
`invariant_ok: true`, `oversold: 0`.

## What was measured (real HTTP, 4 uvicorn workers, `scripts/stress.py`, this laptop)

All eight scenarios passed and the integrity view was `ok=True, oversold=0, dup=0` after each: 1000 FIFO
claims on 500 seats (500 x 200, 500 x 409); 500 Fair winners claiming at once (500 distinct seats); 1000
Fair claim attempts on 500 seats (500 x 200, 500 x 403); 300 users x 5 tabs (one seat each); idempotent retry,
token replay and cross-session token (one allocation, 401); one session across three networks; 500 users x 5
entry attempts (500 entries); 200 genuine users x 5 attempts during a 20,000-request unauthenticated flood
(every user entered). Latencies from the last full run are in the final report. **Caveat:** the Python load
client was at about 100% of one CPU while the API used about a third of its capacity, so those latencies and
the requests-per-second are limited by the load generator and by a busy laptop; they are not the backend's
ceiling. The load generator needs the same care (cap in-flight requests at the connection-pool size) in the
real simulator.

## Known gaps

* The stale `api/tests/test_schema.py` and `api/schema.sql` (from the removed `init` migration) were deleted; the same behaviours are covered by `test_constraints.py`, `test_integrity_view.py` and `test_roles_and_reset.py`.
* No Redis-backed limiter or risk rules yet (Saanvi). No per-session OTP/IP throttling exists until `otp_request_guard` is implemented.
* `active_sessions` in metrics counts unrevoked sessions created in the last 24 h (not live connections).
* Latency percentiles in `/metrics` are server-side bucket estimates (bounds 1 ... 5000 ms).
