# web

**Purpose:** the user app and the judge dashboard in one Vite build (React, TypeScript strict, Tailwind). Node 24 LTS lives in the container; the host needs no Node.
**Owner:** Ameya (frontend).

- Dev server: started by `uv run fd up` at http://127.0.0.1:5173 with `/api` proxied to the `api` service (same origin, so cookies work without CORS). File watching uses polling because change events do not cross a Windows/macOS bind mount.
- Production-style build: `docker compose --profile prod up -d web-prod` serves the static build with nginx at http://127.0.0.1:8080, with `/api` proxied to the API.
- pnpm is pinned in `package.json` (`packageManager`) and enabled through corepack. Run pnpm inside the container: `docker compose exec web pnpm <command>`.
- `node_modules` and the pnpm store live in the `web-node-modules` named volume, not on the host.
- Pins to know: `typescript` is `~6.0.3` (typescript-eslint does not support TS 7 yet) and `eslint` is `~10.11.0` (pnpm 12 refuses a release younger than a day). See `docs/PLATFORMS.md`.

Checks: `uv run fd lint` (eslint, tsc, prettier) and `uv run fd test-web`.

## App layout (Plan 15)

- `src/api/` typed client. `generated/schema.d.ts` is generated from `docs/contract/openapi.json` (`docker compose run --rm --no-deps web pnpm gen:api`; never edit it by hand). `http.ts` is the only place that calls the network; `retry.ts`, `idempotency.ts`, `poller.ts` and `clock.ts` are the rules every screen uses.
- `src/features/user/` the ticket-buyer app (home, drop page, tickets, draw verification). `src/features/admin/` the organiser console.
- `src/mocks/` demo mode: a mock of the API that runs in the browser, so the whole app works with no backend.

## Demo mode (no backend needed)

- Turn it on at start: set `VITE_USE_MOCKS=true` in `.env`, then `uv run fd up`. Or at any time: the "Switch to demo data" link in the page footer (it is remembered in this browser; the same link switches back).
- A "Demo data" badge shows in the header, and a "Demo controls" button lets you choose your draw result, move a drop through its phases, and break the network on purpose (connection lost, rate limit, expired claim pass, lost response).
- With the real API, the home page lists: drops from the admin list (if you unlocked the console in this tab), ids in `VITE_DROP_IDS` (comma-separated), and drops this browser has opened before.
