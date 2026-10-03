# Fair Drop API Contract (implemented endpoints only)

Errors always use `{"error": {"code": "<CODE>", "message": "<text>"}}` (FastAPI's own validation errors use its default `422 {"detail": [...]}` shape).
Authenticated endpoints need `Authorization: Bearer <session_token>`. Timestamps are ISO-8601 UTC.

## GET /health
`200 {"status":"ok","postgres":"ok","redis":"ok|down"}` — `503` with `status:"unhealthy"` if Postgres is down. Redis is reported but not required yet.

## POST /api/auth/otp/request
Body: `{"phone": "+919876543210"}`
- `200 {"message", "expires_in_seconds", "demo_otp"}` — `demo_otp` is returned only when `SIM_MODE=true` (no SMS is sent).
- `422 INVALID_PHONE`, `501 SMS_NOT_CONFIGURED` (SIM_MODE off).

## POST /api/auth/otp/verify
Body: `{"phone": "...", "otp": "123456"}`
- `200 {"session_token", "expires_at", "user_public_id"}` — OTP is single-use; 5 wrong attempts invalidate it.
- `401 INVALID_OTP`, `422 INVALID_PHONE`.

## GET /api/drops/{drop_id}
Public.
- `200 {"id","name","total_seats","mode":"fifo|fair","status":"scheduled|open|frozen|drawn|claimable|closed","entry_start","entry_end","entry_count","allocated_seats","remaining_seats"}`
- `404 DROP_NOT_FOUND`

## POST /api/drops/{drop_id}/entries
Auth required. No body. Idempotent: one entry per user per drop.
- `201` new entry, `200` entry already existed. Body: `{"drop_id","status":"pending|claimed","created_at","queue_position"}`
- `401 UNAUTHENTICATED|INVALID_SESSION`, `404 DROP_NOT_FOUND`, `409 ENTRY_CLOSED`

## GET /api/drops/{drop_id}/me
Auth required.
- `200 {"user_public_id","drop_id","entry": EntryOut|null,"allocation": {"seat_number","allocated_at"}|null,"draw": {"rank","is_winner"}|null}` — `draw` is set for fair drops once drawn. `queue_position` is informational arrival order and plays **no role** in the Fair Draw.
- `401`, `404 DROP_NOT_FOUND`

## POST /api/drops/{drop_id}/claim
Auth required. No body. Optional header `Idempotency-Key: <string ≤200>`; a repeated key (same user + drop) replays the original status and body without a second allocation. Behaviour depends on the drop's `mode`:

**fifo drops (BEFORE baseline)** — first come first served.
- `200 {"drop_id","seat_number","allocated_at"}`
- `401`; `403 NO_ENTRY`; `404 DROP_NOT_FOUND`
- `409`: `ALREADY_CLAIMED`, `SOLD_OUT`, `DROP_NOT_OPEN`, `CLAIM_CONFLICT` (constraint race, safe to retry)

**fair drops** — requires header `X-Admission-Token: <token>` from `POST /api/drops/{id}/admission-token`. Same `200` body. In one transaction it verifies winner + token, consumes the token, allocates one seat. Errors (all `{"error":{code,message}}`; nothing is consumed on any error, and failed responses are *not* cached under the Idempotency-Key, so the key can be retried with a corrected token):
- `401 UNAUTHENTICATED|INVALID_SESSION`
- `401 ADMISSION_TOKEN_INVALID` (malformed / bad signature / tampered), `401 ADMISSION_TOKEN_EXPIRED`
- `403 NOT_A_WINNER`, `403 ADMISSION_TOKEN_REQUIRED`, `403 ADMISSION_TOKEN_WRONG_USER`, `403 ADMISSION_TOKEN_WRONG_SESSION`, `403 ADMISSION_TOKEN_WRONG_DROP`
- `409 DROP_NOT_CLAIMABLE` (not yet claimable, or closed), `409 ADMISSION_TOKEN_USED` (replay), `409 ADMISSION_TOKEN_SUPERSEDED` (a newer token was issued), `409 SOLD_OUT`, `409 CLAIM_CONFLICT`
- `404 DROP_NOT_FOUND`

## GET /api/admin/drops/{drop_id}/integrity
Header `X-Admin-Key: <ADMIN_API_KEY>`.
- `200 {"drop_id","total_seats","physical_seats","allocated_seats","remaining_seats","duplicate_allocation_count","unique_allocated_users","overselling_occurred","invariant_allocated_lte_total"}`
- `403 FORBIDDEN`, `404 DROP_NOT_FOUND`

---

# Fair Draw (fair-mode drops)

## Lifecycle
`open` → `frozen` → `drawn` → `claimable` → `closed`. Each admin transition is atomic (drop row locked) and valid only from the stated state, otherwise `409 INVALID_STATE`. Admin endpoints need `X-Admin-Key`; on a non-fair drop they return `409 NOT_A_FAIR_DROP`; unknown drop `404 DROP_NOT_FOUND`.

| state | meaning | entries | token / claim |
|---|---|---|---|
| open | entry window | accepted (`409 ENTRY_CLOSED` otherwise) | no |
| frozen | entry set fixed + hashed | rejected (API and DB trigger) | no |
| drawn | ranking stored | rejected | no |
| claimable | winners may claim | rejected | yes |
| closed | allocation finished | rejected | no (`DROP_NOT_CLAIMABLE`) |

Recommended order: **commit** (while open) → freeze → reveal → draw → open-claims → close. `commit` is also accepted while frozen (before reveal).

## Algorithm (canonical encodings)
All text UTF-8, no trailing newline.
- **seed**: 32 random bytes as a 64-char lowercase hex string; always used as the UTF-8 bytes of that hex string.
- **commitment** = `SHA256(seed)` hex. Verify: `printf %s "$SEED" | shasum -a 256`.
- **entry_set_hash** = `SHA256("fairdrop:entryset:v1\ndrop_id=<id>\ncount=<n>\n" + "\n".join(sorted unique user_public_ids))` hex. Only random public ids (`u_…`), never phones.
- **score** = `HMAC-SHA256(key=seed, msg="fairdrop:draw:v1|<drop_id>|<user_public_id>")` hex.
- **ranking**: ascending score (rank 1 = lowest), ties by public id. Ranks `1..total_seats` are winners, the rest are waitlist (rank retained, no promotion implemented).
Request time, order, count, retries, tabs and connections never enter the computation. Reference implementation: `backend/app/utils/draw.py`; independent checker: `backend/scripts/verify_draw.py`.

## Admin endpoints (`X-Admin-Key`)
- `POST /api/admin/drops` body `{"name"?, "total_seats"? (default 500), "mode"? ("fair" default | "fifo")}` → `201` DropOut (creates seats `1..N`).
- `POST /api/admin/drops/{id}/commit` → `200 {"drop_id","status","seed_commitment","seed_committed_at"}`. Generates the secret seed; the seed is never returned here. `409 SEED_ALREADY_COMMITTED` — the seed can't be replaced (also enforced by a DB trigger, and a DB CHECK that the stored seed hashes to the commitment).
- `POST /api/admin/drops/{id}/freeze` (from `open`) → `200 {"drop_id","status":"frozen","entry_set_hash","frozen_entry_count","frozen_at"}`. Entry creation holds a shared lock on the drop and freeze an exclusive one, so no entry can race past the freeze.
- `POST /api/admin/drops/{id}/reveal` (frozen or later) → `200 {"drop_id","status","seed","seed_commitment"}`. Idempotent. `409 NO_COMMITMENT`.
- `POST /api/admin/drops/{id}/draw` (from `frozen`) → `200 {"drop_id","status":"drawn","entry_set_hash","entry_count","winner_count","drawn_at"}`. `409 SEED_NOT_REVEALED`; `409 ENTRY_SET_MISMATCH` if current entries don't hash to the frozen hash.
- `POST /api/admin/drops/{id}/open-claims` (from `drawn`) → `200 {"drop_id","status":"claimable"}`.
- `POST /api/admin/drops/{id}/close` (from `drawn`/`claimable`) → `200 {"drop_id","status":"closed"}`. Unclaimed seats simply stay `available`.
- `POST /api/admin/demo/users` body `{"count": 1..60000, "drop_id"?, "enter"?: true}` → `201 {"users":[{"user_public_id","session_token"}],"entered"}`. **SIM_MODE only** (`403 SIM_MODE_REQUIRED`); creates synthetic verified users + sessions and (optionally) enters them. For demos/simulators.

## Public verification endpoints (no auth, fair drops)
- `GET /api/drops/{id}/fairness` → `200 {"drop_id","mode","status","total_seats","seed_commitment","seed_revealed","seed" (null until revealed),"entry_set_hash","frozen_entry_count","frozen_at","draw_status":"pending|complete","winner_count","drawn_at","algorithm":{…}}`
- `GET /api/drops/{id}/fairness/entries?offset=0&limit=1000` (limit ≤ 10000) → `{"drop_id","entry_set_hash","total","offset","user_public_ids":[sorted]}`; `409 NOT_FROZEN` before freeze.
- `GET /api/drops/{id}/fairness/results?offset=0&limit=100` (limit ≤ 10000) → `{"drop_id","total","winner_count","offset","results":[{"rank","user_public_id","score","is_winner"}]}` in rank order; `409 DRAW_NOT_COMPLETE` before the draw.
A judge verifies: `sha256(seed)==seed_commitment`; recompute `entry_set_hash` from `/entries`; recompute every score/rank from the seed; confirm the top `total_seats` are the winners. `python backend/scripts/verify_draw.py http://localhost:8000 <drop_id>` does all of it.

## POST /api/drops/{drop_id}/admission-token
Auth required, no body. Winners only, only while `claimable`.
- `200 {"drop_id","admission_token","expires_at"}`. TTL `ADMISSION_TOKEN_TTL_SECONDS` (default 300).
- `401`; `403 NOT_A_WINNER`; `409 DROP_NOT_CLAIMABLE`; `409 ALREADY_CLAIMED`.
Token format: `base64url(payload).base64url(HMAC-SHA256(key, base64url(payload)))`, key derived from `SECRET_KEY`; payload `{v, jti, d: drop_id, u: user_public_id, s: session_id, exp}`. It is signed, bound to drop + user + the session that requested it, expiring, and single-use (`admission_tokens.consumed_at`, consumed in the same transaction as the seat allocation). Re-requesting replaces the previous unconsumed token (old one → `ADMISSION_TOKEN_SUPERSEDED`), so an expired/lost offer can be refreshed; there is never more than one live token per winner.
