# infra

**Purpose:** everything that runs the stack and guards the repository: Docker Compose, Postgres and Redis configuration, and the git hook shims. No other shell scripts live here.
**Owner:** Akshay (stack).

- `docker-compose.yml` the services (`postgres`, `redis`, `api`, `web`, `web-prod`, `sim`, `migrate`). The root `compose.yaml` includes it (a real file, no symlink). The project name `fairdrop` is set in the root file.
- `postgres/postgresql.conf` Postgres settings. `synchronous_commit = on` is deliberate and must stay on: the integrity claims rest on committed transactions surviving a crash.
- `redis/redis.conf` and `redis/README.md` Redis settings and why `volatile-ttl` (never `allkeys-lru`).
- `git-hooks/` two-or-three-line `sh` shims (`commit-msg`, `pre-push`, `pre-commit`) that call `uv run fd ...`. All logic is Python in `tools/fd/`. They fail closed if `uv` is missing. Installed with `uv run fd hooks`.
