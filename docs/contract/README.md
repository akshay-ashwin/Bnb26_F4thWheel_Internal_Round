# Fair Drop API contract

> **FROZEN (Plan 03, 2026-10-04).** The canonical machine-readable contract is [`openapi.json`](openapi.json), generated from the code (`uv run fd openapi`; `uv run fd openapi --check` fails if the snapshot is stale). This file explains it in words. A change needs a note to all three people (Ameya, Akshay, Saanvi), a "CONTRACT CHANGE — teammates must know" banner at the top of the review log, and a regenerated snapshot in the same commit. The snapshot is always exported with `SIM_MODE=true` so `/sim/telemetry` is included.

Source of truth for shapes: `docs/design/Fair_Drop_Architecture.md` section 11 (shapes below are copied from it). Additions made by later plans are marked **(addition)** and name the plan.

The browser and the simulator call exactly the same public endpoints. Fifteen endpoints in total: 7 public, 7 admin, 1 simulator telemetry.

## Conventions

- Base path `/api`. JSON only (the export is NDJSON).
- Auth: the `fd_session` httpOnly cookie (browser) or `Authorization: Bearer <session_token>` (simulator). Both carry the same session.
- Every POST that changes state requires `Idempotency-Key: <uuid>` (see the endpoint table for which ones; a missing key is `400 IDEMPOTENCY_KEY_MISSING`).
- **Every response includes `server_time`** (ISO 8601 UTC string with milliseconds and a `Z` suffix, e.g. `2026-10-04T10:15:30.123Z`; top level of the JSON body; a required field in every response schema). The NDJSON export has no per-row `server_time`; it sends the same value in an `X-Server-Time` response header instead (decision made in Plan 01, to be confirmed in Plan 14).
- Every error uses the one envelope below. No endpoint returns any other error shape.
- Admin endpoints need the `X-Admin-Key` header (value from `.env`).

## The six interfaces agreed up front

### 1. Error envelope

```json
{
  "error": { "code": "RATE_LIMITED", "message": "Slow down", "retry_after_ms": 2000 },
  "server_time": "2026-10-04T10:15:30.123Z"
}
```

`retry_after_ms` is present only when it makes sense (429, 503, throttles); the `Retry-After` header (whole seconds, rounded up) is set at the same time. **(addition, Plan 03)** `error.details` is an optional object: for `VALIDATION_ERROR` it is `{"fields": [{"field": "body.phone", "message": "..."}]}` (never the submitted value); for a `503` from `/readyz` it is `{"postgres": "down", "redis": "up"}`. Unknown paths return `404 NOT_FOUND`, a wrong method returns `405` with code `VALIDATION_ERROR`, and a `500 INTERNAL` message carries the request id (also in the `X-Request-ID` response header, which every response has). `code` values are listed in [`error-codes.md`](error-codes.md). The design doc shows the envelope without `server_time`; the "every response includes `server_time`" convention is what adds it, and it sits next to `error`, so clients that only read `error` are unaffected.

### 2. The `/me` JSON (`GET /api/drops/{id}/me`) — the UI's single source of truth

```json
{
  "phase": "CLAIMING",
  "entry": {
    "entry_id": "0b6c5d34-8c40-4d6d-9d57-2a53f3c9a001",
    "status": "OFFERED",
    "rank": 187,
    "waitlist_pos": null,
    "offer_expires_at": "2026-10-04T10:17:30+00:00",
    "step_up_required": false,
    "admission_token": "<compact HMAC-SHA256 JWT>"
  },
  "allocation": null,
  "poll_after_ms": 2000,
  "server_time": "2026-10-04T10:15:30.123Z"
}
```

- **(changed in Plan 03)** Optional fields are always present and `null` when they do not apply (the OpenAPI schema marks them required and nullable): `entry` (null before registering), `rank`, `waitlist_pos`, `offer_expires_at`, `admission_token`, `dev_otp` (SIM_MODE only, otherwise null) and `allocation` (null until a seat is confirmed: `{ "allocation_id": "...", "seat_no": 42, "confirmed_at": "..." }`).
- `entry.status` is one of: `REGISTERED`, `OFFERED`, `STEP_UP_REQUIRED`, `WAITLISTED`, `NOT_SELECTED`, `OFFER_EXPIRED`, `ALLOCATED`, `DISQUALIFIED`. Every user screen maps to exactly one of these (plus "no entry yet" and the phase).
- Admission token claims: `{drop_id, entry_id, sid_hash, jti, iat, exp}`; `exp` is no later than the offer expiry; `sid_hash` binds the token to the session that fetched it.
- Errors: `401`, `429`. Read-only.

### 3. The `/metrics` JSON (`GET /api/admin/drops/{id}/metrics?window_s=60`)

```json
{
  "rps_series": [ { "t": 1759572930, "total": 812 } ],
  "outcomes_series": {
    "accepted": [700], "rate_limited": [90], "token_rejected": [2], "duplicate": [20]
  },
  "latency": { "p50": 12, "p95": 80, "p99": 240 },
  "error_rate": 0.001,
  "active_sessions": 4021,
  "entries": 50321,
  "offers": 500,
  "allocated": 311,
  "remaining": 189,
  "flagged_entries": 2043,
  "step_ups": { "issued": 40, "passed": 37, "failed": 3 },
  "server_time": "2026-10-04T10:15:30+00:00"
}
```

Series element shape and alignment come from Plan 14: `rps_series` is one `{t, total}` per second; each `outcomes_series` array is aligned with `rps_series`. **(addition, Plan 14)** `rate_limited_by_layer` (object of arrays) and `latency_by_group` may be added; they will be listed here when Plan 14 freezes them. Latencies are milliseconds.

### 4. The `/export` NDJSON row (`GET /api/admin/drops/{id}/export`)

One JSON object per line, `Content-Type: application/x-ndjson`:

```json
{"user_public_id":"u_8fK2xQ9d","entry_id":"0b6c5d34-8c40-4d6d-9d57-2a53f3c9a001","entered_at":"2026-10-04T10:00:01.250+00:00","risk_score":65,"risk_flags":["device_shared","subnet_burst"],"rank":187,"status":"ALLOCATED","seat_no":42}
```

`seat_no` is absent unless the entry holds a seat. This is what the evaluator joins against the simulator's private `ground_truth.ndjson`; the backend never reads that file (invariant 6).

### 5. Rate-limiter middleware decision interface

The abuse layers L1–L5 sit in one middleware that makes one call per request:

```
check(request) -> Decision { outcome: "allow" | "reject",
                             layer: "L1".."L8" | null,
                             code: <error code> | null,
                             retry_after_ms: int | null }
```

- `allow` carries `layer: null`. `reject` always names the layer that rejected and the error `code` (normally `RATE_LIMITED`; `TOKEN_INVALID` for L4).
- L1–L3 never touch Postgres. L7 never rejects (it only sets `risk_flags`). Only L4, L5 and L8 change outcomes, and all three are deterministic and explainable.
- A `reject` is turned into the error envelope with `retry_after_ms`, and also sets the `Retry-After` header (seconds).

### 6. Simulator telemetry (`POST /api/sim/telemetry`)

```json
{
  "run_id": "run_2026-10-04T10-00-00Z_fair",
  "attack_phase": "flood",
  "clients_by_label": { "human": 50000, "bot": 10000 },
  "identities_by_label": { "human": 50000, "bot": 2000 },
  "requests_by_label": { "human": 120000, "bot": 6400000 }
}
```

Only available when `SIM_MODE=true`, protected by `SIM_TELEMETRY_KEY` sent in the `X-Sim-Key` header. It writes to a separate Redis namespace `sim:*`. The dashboard shows it as "ground truth". No backend decision module may read `sim:*` keys (invariant 6; a CI check enforces it in Plan 14).

## Endpoint table

All paths are under `/api`. "Session" = cookie or bearer session. Statuses and codes are those in the design doc; codes added by later plans are marked.

### Public

| Method + URL | Auth | Request body | Success | Error codes (HTTP) | Idempotency |
| --- | --- | --- | --- | --- | --- |
| `GET /drops/{id}` | none | — | `200 {id, name, capacity, mode, phase, reg_opens_at, reg_closes_at, claim_window_s, seats_remaining, seed_commit, seed?, entry_set_hash?}` | `NOT_FOUND` (404) | read-only; cacheable 1 s |
| `POST /auth/otp/request` | none | `{phone, device_id}` | `200 {request_id, expires_in_s, dev_otp?}` (`dev_otp` only when `SIM_MODE`) | `OTP_THROTTLED` (429), `INVALID_PHONE` (400), `RATE_LIMITED` (429) | same phone within 30 s returns the same `request_id` |
| `POST /auth/otp/verify` | none | `{request_id, otp, device_id}` | `200 {session_token, user_public_id}` and sets the cookie | `OTP_INVALID` (401), `OTP_EXPIRED` (410), `RATE_LIMITED` (429) | re-verify returns the existing session for the same device |
| `POST /drops/{id}/entries` | session | `{}` | `201 {entry_id, status:"REGISTERED"}`; a repeat returns `200` with the same body | `WINDOW_CLOSED` (403), `WINDOW_NOT_OPEN` (403), `UNAUTHENTICATED` (401), `RATE_LIMITED` (429) | `UNIQUE(drop,user)`; key optional |
| `GET /drops/{id}/me` | session | — | `200` see interface 2 | `UNAUTHENTICATED` (401), `RATE_LIMITED` (429) | read-only |
| `POST /drops/{id}/claim` | session + admission token | `{admission_token}` | `200 {allocation_id, seat_no, confirmed_at}` | `TOKEN_INVALID` (401), `NOT_OFFERED` (403), `OFFER_EXPIRED` (409), `SOLD_OUT` (409), `STEP_UP_REQUIRED` (423), `IDEMPOTENCY_KEY_REUSED` (422), `IDEMPOTENCY_KEY_MISSING` (400, Plan 05), `RATE_LIMITED` (429) | key required; same key or same entry returns the same `200` |
| `POST /drops/{id}/step-up` | session | `{otp}` | `200 {status:"OFFERED"}` | `OTP_INVALID` (401), `OFFER_EXPIRED` (409), `IDEMPOTENCY_KEY_MISSING` (400, Plan 05) | key required; repeat after success returns `200` |

In FIFO mode `entries` and `claim` are both open at once and `claim` skips the offer check; the request and response shapes are identical, so neither the UI nor the simulator needs a second code path.

### Admin (`X-Admin-Key`)

| Method + URL | Request | Success | Error codes (HTTP) |
| --- | --- | --- | --- |
| `POST /admin/drops` | `{name, capacity:500, mode:"fair"\|"fifo", window_s, claim_window_s}` | `201 {drop_id, seed_commit}` | `VALIDATION_ERROR` (400), `UNAUTHENTICATED` (401) |
| `POST /admin/drops/{id}/phase` | `{action:"open"\|"close"\|"draw"\|"reset"}` | `200 {phase}` | `INVALID_TRANSITION` (409), `NOT_FOUND` (404) |
| `GET /admin/drops/{id}/metrics` | `?window_s=60` | `200` see interface 3 | `NOT_FOUND` (404) |
| `GET /admin/drops/{id}/integrity` | — | `200 {seats_total:500, sold, free, oversold, duplicate_entries_with_seats, invariant_ok}` (computed by SQL, not counters) | `NOT_FOUND` (404) |
| `GET /admin/drops/{id}/export` | — | `200` NDJSON, see interface 4 | `NOT_FOUND` (404) |
| `GET /admin/drops/{id}/draw-proof` | — | `200 {seed_commit, seed, entry_set_hash, algorithm:"HMAC_SHA256(seed, drop_id‖user_public_id) asc"}` | `NOT_FOUND` (404), `INVALID_TRANSITION` (409) before the draw |
| `PUT /admin/abuse/config` | `{layers:{L1:true,…,L8:true}, thresholds:{…}}` | `200` echo of the config | `VALIDATION_ERROR` (400) |

Admin endpoints are idempotent by nature. A bad or missing admin key is `401 UNAUTHENTICATED`.

### Simulator

| Method + URL | Auth | Request body | Success | Error codes |
| --- | --- | --- | --- | --- |
| `POST /sim/telemetry` | `SIM_TELEMETRY_KEY`, only when `SIM_MODE=true` | see interface 6 | `200 {}` | `UNAUTHENTICATED` (401), `NOT_FOUND` (404) when `SIM_MODE=false` |

### Health

`GET /api/healthz` returns `200 {"status":"ok","server_time":"..."}` and touches nothing. **(addition, Plan 03)** `GET /api/readyz` returns `200 {"status":"ready"|"degraded","postgres":"up","redis":"up"|"down","server_time":"..."}`; Redis down is `degraded` (still `200`), Postgres down is the error envelope `503 SERVICE_UNAVAILABLE` with `details`. Neither is one of the fifteen business endpoints.

## Where things are decided later

- Exact HTTP status for `VALIDATION_ERROR`: decided in Plan 03 as 400, consistent with `INVALID_PHONE`. FastAPI's default 422 is removed from the spec; the only 422 is `IDEMPOTENCY_KEY_REUSED`.
- Run summary and scorecard storage endpoints are mentioned in Plan 14 scope; if they become public endpoints they get added here under a Deviation Record.
