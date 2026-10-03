# D-002 — Node 24 LTS instead of Node 22 LTS

Date: 2026-10-04  ·  Raised during: Plan 01 (human-directed)  ·  Status: ADOPTED

## The original plan said

D-001 and CLAUDE.md "Stack and platform rules" item 5 fix the web toolchain at Node 22 LTS: image `node:22-bookworm-slim`, `engines.node` `>=22 <23`, CI on Node 22 (Plan 01 §3, §4.4, §4.6, §4.10, §6; Plan 15 §3). D-001 itself noted that Node 24 is the newer LTS line.

## What we do instead

- The `web` image is `node:24-bookworm-slim`; `web/package.json` sets `engines.node` to `>=24 <25`; CI uses Node 24.
- pnpm stays pinned in `packageManager` and enabled with corepack. Corepack is still bundled with Node 24. If `corepack enable` ever fails in the image, the fallback is `npm install -g pnpm@<pinned version>` in the Dockerfile; record it in the review log if used.
- Everything else from D-001 is unchanged (Python 3.12, PostgreSQL 16, Redis 7, Docker-only runtime, `uv run fd`).

## Why it is better (evidence)

- nodejs.org's release schedule (checked 2026-10-04) lists Node 24 (Krypton) as LTS and Node 20 as end-of-life. Node 22 is also still LTS, so this is not an emergency; the gain is a longer support runway for the same cost, because the project has no Node code yet and nothing to migrate.
- The human asked for Node 24 explicitly.
- Risk considered: some native optional dependencies (Playwright browsers, esbuild/rollup binaries) lag a new major. Mitigation: Plan 01's check `docker compose exec web node --version` plus Plan 15's first `pnpm install` and build prove the toolchain; if a dependency fails on 24, record it and revisit with a new D-record rather than silently pinning.

## Invariants check

1. 500 seats never 501 — unaffected.
2. One identity = one entry = one seat — unaffected.
3. Postgres is the only source of truth — unaffected.
4. Arrival time / request count never inputs in Fair mode — unaffected.
5. Bot detection never silently decides winners — unaffected.
6. Ground-truth labels never reach a decision path — unaffected.
7. Draw deterministic from a committed seed — unaffected.
8. Guarded entry state changes — unaffected.

## Contract impact

None. Tooling version only.

## Plans edited

| Plan | Section | Summary of edit |
|---|---|---|
| `CLAUDE.md` | Stack rules item 5, repository map | Node 24 LTS, `engines >=24 <25` |
| `00_HOW_TO_USE` | Setup | Node 24 |
| 01 | §1, §4.4, §4.6, §4.10, §6 | image `node:24-bookworm-slim`, engines, CI, `node --version` prints v24.x |
| 02–19 | §0 pointer | "Node is 24 LTS (D-002)" |
| 15 | §3 | Node 24 container |
| D-001 | historical | left as written; superseded for the Node version by this record |

## Rollback

Change the image tag, `engines` range and CI version back to 22 and revert this record's commit. No code depends on the Node major yet.
