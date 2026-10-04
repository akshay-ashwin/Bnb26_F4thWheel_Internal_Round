# Glossary

One meaning per word. Use these names in code, docs, UI copy and review logs. If you need a new term, add it here first.

| Term | Meaning |
| --- | --- |
| **drop** | One sale event with a fixed number of seats (500). Has a `mode`, a `phase`, a registration window and a claim window. |
| **phase** | Where a drop is in its life: `SCHEDULED`, `OPEN`, `CLOSED`, `DRAWN`, `CLAIMING`, `DONE`. Changed only by the server (admin phase actions). |
| **mode** | How seats are given out. `fifo` = first claim wins (the unfair "before"). `fair` = Verified Entry Window then Provable Draw (the "after"). Same API, same shapes in both. |
| **identity** | One verified phone number. The unit of fairness: chance of a seat depends only on how many distinct identities a client controls. |
| **user** | The database row for an identity (`users`). One phone hash = one user. |
| **user_public_id** | Random public id of a user (`users.public_id`). Used in the draw and in exports instead of anything personal. Format (fixed in Plan 04, the draw hashes this exact string): 16 random bytes, base32, lowercase, no padding, 26 characters from `a-z2-7`. |
| **session** | A signed-in browser tab or simulator client, created after OTP verification. Carried by the `fd_session` cookie or a bearer token. One user can have several. |
| **phone_hash** | `HMAC-SHA256(PHONE_PEPPER, E.164 number)` as hex. The only form of a phone number the system keeps (`users.phone_hash`, UNIQUE: one phone = one identity). The raw number is never stored or logged. |
| **pepper** | `PHONE_PEPPER`: a secret from the environment that keys the phone hash, so a stolen database alone cannot be reversed by trying every possible number. Changing it orphans every identity. |
| **OTP request** | A one-time code held in Redis only: hash of the code, bound to a request id, the phone hash and the device id. Lives 300 s, five guesses, single use. Same phone within 30 s returns the same request id. |
| **device_id** | Opaque id (8-128 characters) chosen by the client at login. Binds an OTP to the device that asked for it and a session to one device. |
| **session token** | `<session uuid>.<base64url HMAC>`, signed with a key derived from `SESSION_SECRET`. Expiry (24 h) and revocation are checked server-side for cookie and bearer alike. |
| **sid_hash** | Hash of the session id. Put inside an admission token so the token only works for the session that fetched it. |
| **entry** | One identity's registration in one drop (`entries`). At most one per user per drop. Its `status` is the user's state. |
| **entry status** | `REGISTERED`, `OFFERED`, `STEP_UP_REQUIRED`, `WAITLISTED`, `NOT_SELECTED`, `OFFER_EXPIRED`, `ALLOCATED`, `DISQUALIFIED`. Every change is a guarded update (`WHERE status = <expected>`). |
| **offer** | The right to claim a seat inside the claim window. Created by the draw (winners) or by waitlist promotion. Has an expiry. |
| **waitlist** | Entries drawn beyond the seat count, in draw-rank order. Promoted to an offer when an earlier offer expires or fails step-up. |
| **allocation** | The permanent record that an entry holds a seat (`allocations`, append-only ledger). At most one per entry. |
| **seat** | One physical row in `seats` (one row per seat, so 500 seats can never become 501). `free` or `sold`. |
| **app role** | `fairdrop_app`, the restricted Postgres login the API uses (no DDL, append-only ledger, no direct seat or drop inserts). The superuser `POSTGRES_USER` owns the schema and runs migrations and test fixtures only. |
| **integrity view** | `v_drop_integrity`: per drop, seat rows, sold, free, oversold and cross-table agreement between seats, ledger rows and ALLOCATED entries, computed by SQL on every read (never counters). `invariant_ok` is true only when everything agrees. |
| **run (`run_no`)** | The attempt counter of a drop. Reset (`admin_reset_drop`) starts the next run; the previous one is kept in `drop_runs`. |
| **admission token** | Short-lived signed token (HMAC-SHA256 JWT) shown to an entry with an offer; required to claim. Bound to the drop, the entry and the session. |
| **jti** | The token's unique id. Marked used in Redis (fast path) and enforced by Postgres uniqueness (backstop), so a token works once. |
| **idempotency key** | A UUID the client sends with state-changing calls (`Idempotency-Key`). Retrying with the same key returns the stored result instead of acting twice. |
| **seed** | The secret random value that decides the draw order. Revealed after the draw. |
| **seed_commit** | SHA-256 of the seed, published before registration opens, so the draw cannot be rigged after the fact. |
| **entry_set_hash** | SHA-256 of the sorted `user_public_id`s of all entries at close. Proves the set of entrants did not change after the commitment. |
| **draw rank** | Each entry's position after the draw: sorted ascending by `HMAC_SHA256(seed, drop_id ‖ user_public_id)`. Deterministic from the seed; arrival time and request count are never inputs. |
| **step-up** | A fresh OTP to the same phone, required before a flagged winner may claim (L8). A pass keeps the seat; a fail moves to the waitlist. |
| **risk score** | 0–100 per entry from additive rules (L7). 60 or more means "step-up required if it wins". Evidence only; never changes draw odds. |
| **risk flag** | A named reason recorded on an entry (for example a shared device). Shown to judges; never excludes anyone. |
| **cluster** | A group of identities sharing a device id, /24 network, or user agent in a short window; the unit L7 scores. |
| **layer (L1–L8)** | One abuse control, in request order: L1 global buckets, L2 per-IP and /24, L3 per-session and per-user, L4 token validation, L5 duplicate/idempotency collapse, L6 OTP abuse controls, L7 cluster scoring, L8 step-up at claim. Each can be switched on or off in the admin abuse config. |
| **advantage ratio** | Bot win rate per identity divided by human win rate per identity. About 1.0 in Fair mode means no per-identity advantage. |
| **chance band** | The 95% range of bot wins that pure luck predicts (hypergeometric). Measured wins inside the band means "bots did exactly as well as luck predicts". |
| **ground truth** | The simulator's private labels saying which client is a human or a bot (`sim:*` keys, `ground_truth.ndjson`). Never read by any backend decision path. |
| **actor** | A group in the simulator with one goal and one owner (for example "the attacker" with 10,000 clients, or "humans"). Fairness is measured per actor. |
| **run** | One execution of a scenario against one mode, with a `run_id`, a manifest and outputs under `sim/out/`. |
| **fd** | The task CLI: `uv run fd <task>`. Replaces Make. |
| **Deviation Record** | A numbered `D-xxx` document in `docs/decisions/` explaining a change from a plan (Rule R2). |
