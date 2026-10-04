# Contract additions (everything the backend does beyond `README.md`)

> Regenerated into `openapi.json` (`uv run fd openapi`) when the backend was integrated with the Plan 03 foundation; the snapshot and this file agree. The shapes of the original 15 endpoints follow `openapi.json` exactly (for example optional `/me` fields and `dev_otp` are present as `null`, `drop_id` is a UUID path parameter so a malformed id is `400 VALIDATION_ERROR`, `POST /entries` takes the body `{}`).

`README.md` stays the frozen contract. Everything here is **additive**: no endpoint, field or code in
the frozen contract was renamed or changed. Clients that ignore unknown fields need no changes.

## Added endpoints

| Endpoint | Auth | Why |
| --- | --- | --- |
| `GET /api/readyz` | none | Postgres and Redis reachability (`ok` / `degraded` when only Redis is down / `unready`) |
| `GET /api/drops/{id}/draw-proof` | none, after the draw | Public proof with `eligible_public_ids` and `ranked_public_ids` (see `draw.md`) |
| `GET /api/admin/drops` | admin | List drops (id, name, mode, phase, run_no, capacity) so the dashboard needs no hard-coded id |
| `GET /api/admin/drops/{id}/sim` | admin | Latest simulator telemetry for the dashboard (display only) |
| `GET /api/admin/abuse/config` | admin | Read back the abuse configuration |

## Added fields

* `GET /api/admin/drops/{id}/integrity`: an `extra` object with `capacity, allocations_count, sold_without_allocation, allocation_without_sold_seat, entries_allocated_count, entries_allocated_mismatch, sold_seat_entry_not_allocated, free_seat_with_sold_at` (all must be 0, except the two counts, which must equal `sold`).
* `GET /api/admin/drops/{id}/metrics`: after `step_ups`: `phase, mode, run_no, capacity, oversold, invariant_ok, claims_ok, claims_sold_out, blocked_requests, throttled_requests, duplicate_requests, rate_limited_by_layer, window_s, metrics_dropped`.
* `GET /api/admin/drops/{id}/export`: rows also carry `run_no, offered_at, allocated_at, step_up_passed_at`.
* `POST /api/admin/drops/{id}/phase` accepts an optional `mode` only with `action: "reset"` (the demo flips the same drop FIFO to Fair).
* `GET /api/admin/drops/{id}/draw-proof` also returns `drop_id` and `run_no`.
* `POST /api/drops/{id}/entries`: a repeat returns `200` with the same body as the `201` (`status` is always `REGISTERED`; `/me` carries later states). `POST /step-up` always answers `{status: "OFFERED"}` on success, including an idempotent repeat.
* `/me`: `entry.dev_otp` is `null` except for `STEP_UP_REQUIRED` entries when `SIM_MODE=true`.
* `POST /api/admin/drops/{id}/phase` body: optional `mode` (only with `reset`); `POST /api/sim/telemetry` body: optional `fairness_live`.

## Behaviour worth knowing

* `Idempotency-Key`: required on `claim` and `step-up`; optional on `entries`. A replay of a stored success returns the original status (for `entries` that is `201` again; without a key, a repeat is `200`). Replays carry a fresh `server_time`.
* Claim idempotency is by **meaning**: `(drop, entry)`. A fresh token from `/me` with the same key is a legitimate retry.
* A token replayed after a successful claim returns the same allocation (`200`), never a second seat. A token presented by a different session is `401 TOKEN_INVALID`, even for the same user on another device.
* Telemetry auth header is `X-Sim-Key` (value `SIM_TELEMETRY_KEY`); the route does not exist (`404 NOT_FOUND`) when `SIM_MODE=false`.
* Admission token size is under 400 bytes (asserted in the tests); `sid_hash` is the first 128 bits of SHA-256 of the session id (32 hex characters).
