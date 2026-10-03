# web

**Purpose:** the user app and the judge dashboard in one Vite build (React, TypeScript strict, Tailwind). Node 24 LTS lives in the container; the host needs no Node.
**Owner:** Ameya (frontend).

- Dev server: started by `uv run fd up` at http://127.0.0.1:5173 with `/api` proxied to the `api` service (same origin, so cookies work without CORS). File watching uses polling because change events do not cross a Windows/macOS bind mount.
- Production-style build: `docker compose --profile prod up -d web-prod` serves the static build with nginx at http://127.0.0.1:8080, with `/api` proxied to the API.
- pnpm is pinned in `package.json` (`packageManager`) and enabled through corepack. Run pnpm inside the container: `docker compose exec web pnpm <command>`.
- `node_modules` and the pnpm store live in the `web-node-modules` named volume, not on the host.
- Pins to know: `typescript` is `~6.0.3` (typescript-eslint does not support TS 7 yet) and `eslint` is `~10.11.0` (pnpm 12 refuses a release younger than a day). See `docs/PLATFORMS.md`.

Checks: `uv run fd lint` (eslint, tsc, prettier) and `uv run fd test-web`.
