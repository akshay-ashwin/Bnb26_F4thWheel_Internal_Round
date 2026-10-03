# api

**Purpose:** the Fair Drop backend (FastAPI, Python 3.12, asyncpg, redis-py). It runs only in a Linux container. Postgres is the only source of truth; Redis holds only things that are safe to lose.
**Owner:** Akshay (backend).

- `app/` application code (Plan 01 has only the health check; Plan 03 sets the real structure)
- `tests/` pytest suite
- `migrations/` SQL migrations run by the `migrate` service (dbmate, Plan 02)

Run it: `uv run fd up`. Tests and lint run in the container: `uv run fd lint` (Plan 03 adds `uv run fd test-api`). The virtualenv lives in the `api-venv` named volume at `/opt/venv`, never inside this folder. This project must never import from `sim/`, and `sim/` never imports from here.
