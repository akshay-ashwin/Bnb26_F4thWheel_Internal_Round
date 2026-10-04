# api

**Purpose:** the Fair Drop backend (FastAPI, Python 3.12, asyncpg, redis-py). It runs only in a Linux container. Postgres is the only source of truth; Redis holds only things that are safe to lose.
**Owner:** Akshay (backend).

- `app/` the backend: `main.py` (factory), `config.py`, `db.py`, `cache.py`, `errors.py`, `schemas.py`, `middleware.py`, `metrics.py`, `jobs.py`; `routers/` (public, admin, sim, ops); `services/` (auth, entries, claim, draw, sweeper, stepup, lifecycle, idempotency, tokens, admin); `abuse/` (the limiter and risk hooks Saanvi replaces); `draw_math.py` and `security.py` (pure functions)
- `tests/` pytest suite on real Postgres and Redis (`uv run fd test-api`); `scripts/stress.py` real-HTTP stress run (see `docs/BACKEND_HANDOFF.md`)
- `migrations/` plain-SQL migrations run by the `migrate` service (dbmate), plus the reviewable `schema.sql` dump (never edit it by hand: `uv run fd migrate` rewrites it)
- `seeds/` dev-only SQL (`dev_demo_drop.sql`: one demo drop with 500 seats; not a migration)

Run it: `uv run fd up`. Database: `uv run fd migrate` (idempotent; also sets the `fairdrop_app` password from `.env`), `uv run fd reset-db` (fresh volume). Tests and lint run in the container: `uv run fd test-api` (migrates `fairdrop_test`, then pytest; `uv run fd test-api -- -s -k perf` prints the integrity-view latency numbers) and `uv run fd lint`. The API connects as the restricted role `fairdrop_app` (`DATABASE_URL`); the schema owner is `POSTGRES_USER` (D-005). The virtualenv lives in the `api-venv` named volume at `/opt/venv`, never inside this folder. This project must never import from `sim/`, and `sim/` never imports from here.
