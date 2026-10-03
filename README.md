# Fair Drop — Backend

Fair Drop sells a fixed pool of physical seats (demo: **500**) and must never oversell. This repo holds the **backend foundation, a FIFO baseline (the BEFORE), and the Fair Draw backend (the AFTER)**; abuse/rate limiting (Redis), simulator and frontend are built separately.

## Architecture (high level)
FastAPI (async) → PostgreSQL 16 (source of truth, SQLAlchemy async + asyncpg, Alembic) ; Redis 7 connected + health-checked only.
Seat safety is enforced by the database: `UNIQUE(allocations.seat_id)`, `UNIQUE(drop_id, user_id)` on entries and allocations, a composite FK tying a seat to its drop, and a fixed seat table (500 rows). Claims run in one transaction with `SELECT … FOR UPDATE SKIP LOCKED`.

```
backend/app/{api,services,models,schemas,db,utils,middleware}   backend/migrations   backend/tests
```

## Run with Docker
```bash
cp .env.example .env
docker compose up --build        # api on :8000, runs migrations (creates demo drop id=1, 500 seats)
curl localhost:8000/health
```

## Environment variables (`.env.example`)
`DATABASE_URL`, `REDIS_URL`, `SIM_MODE` (OTP returned in API response; no SMS), `SECRET_KEY` (HMAC for phones/OTPs/sessions), `ADMIN_API_KEY` (`X-Admin-Key` for admin routes), `SESSION_TTL_MINUTES`, `OTP_TTL_SECONDS`, `ADMISSION_TOKEN_TTL_SECONDS` (default 300).

## Migrations
```bash
cd backend && alembic upgrade head      # also seeds the demo drop
```

## Tests
Needs a Postgres database whose name ends in `_test` (tests truncate tables; other names are refused). Redis is not required.
```bash
docker compose up -d postgres
docker compose exec postgres createdb -U fairdrop fairdrop_test
cd backend && pip install -r requirements.txt
TEST_DATABASE_URL=postgresql+asyncpg://fairdrop:fairdrop@localhost:5432/fairdrop_test pytest
```
Tests migrate the test DB themselves (`downgrade base` → `upgrade head`).

## Fair Draw flow
A client's chance depends on how many distinct verified identities it controls, never on request speed, count, retries, tabs or connections.
```
verified identity (OTP session) → entry window (open) → [commit seed: publish SHA256(seed)]
→ freeze (entries closed, entry_set_hash stored) → reveal seed → deterministic HMAC draw (drawn)
→ open-claims (claimable) → winner gets signed single-use admission token → atomic seat claim → close
```
- `score = HMAC-SHA256(seed, "fairdrop:draw:v1|<drop_id>|<public_id>")`; lowest `total_seats` scores win. Exact encodings: [API contract](docs/API_CONTRACT.md#fair-draw-fair-mode-drops), code: `backend/app/utils/draw.py`.
- Public, auth-free verification: `GET /api/drops/{id}/fairness`, `/fairness/entries`, `/fairness/results`.
- Claim = one PostgreSQL transaction: lock drop (shared) → lock token row → verify winner/token/session/expiry/not-used → `SKIP LOCKED` seat → insert allocation → consume token. Unique constraints + write-once triggers on seed/commitment/entry hash are the last line of defence.
- `mode='fifo'` drops keep the old first-come-first-served `/claim` for the before/after comparison.

### Demo
```bash
docker compose up --build -d
docker compose exec api python scripts/demo_fair_flow.py --users 2000   # create drop → users → commit → freeze → reveal → draw → claims → verify
docker compose exec api python scripts/verify_draw.py <drop_id>          # judge-side check using only public endpoints
```
Manual: `POST /api/admin/drops` (mode fair) → `POST /api/admin/demo/users` → `/commit` → `/freeze` → `/reveal` → `/draw` → `/open-claims` → winner: `POST /admission-token`, `POST /claim` with `X-Admission-Token`. (All admin calls need `X-Admin-Key`.) The built-in 500-seat demo drop (id 1) is a `fifo` drop; create fair drops via the admin API.

## Endpoints
See [docs/API_CONTRACT.md](docs/API_CONTRACT.md): `/health`, OTP request/verify, `GET /api/drops/{id}`, `POST /api/drops/{id}/entries`, `GET /api/drops/{id}/me`, `POST /api/drops/{id}/claim`, `GET /api/admin/drops/{id}/integrity`, plus the Fair Draw admin/verification/admission-token endpoints.

## Assumptions / limitations
- **FIFO is only the baseline.** Fair mode is implemented (see above); fifo-mode drops are unchanged.
- Fair Draw limits: no waitlist promotion (non-winners are ranked but unclaimed seats stay available after `close`); the seed is generated and stored by the server, so users trust the commit-before-freeze order plus DB write-once triggers (no external randomness beacon); admin endpoints are protected only by `X-Admin-Key`; Sybil resistance is limited to one OTP-verified phone = one identity (rate limiting/abuse detection is separate work).
- FIFO semantics: entry creation time (server clock) defines `queue_position`; `/claim` hands out the lowest free seat to claims in arrival order. Claims are not gated on queue position.
- `SKIP LOCKED` can report `SOLD_OUT` to a request while the last seats are held by in-flight transactions that later roll back; the client may retry.
- Auth is demo-grade (SIM_MODE). No rate limiting here — Redis abuse layer is owned elsewhere.
