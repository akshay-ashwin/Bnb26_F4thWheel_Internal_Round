# Review Log — Plan 01: Repository, Tooling, Attribution Guardrails & Local Stack

Date: 2026-10-04  ·  Commits: 34e2cab … e537345 (plus the commit that adds this log)  ·  Status: DONE WITH CAVEATS

> CONTRACT CHANGE — teammates must know: **none to the API.** The contract skeleton in `docs/contract/` is new in this plan and not frozen yet (it freezes at Plan 03). Three small choices in it need a teammate's eyes, because they go slightly beyond the design doc: (1) `server_time` sits at the top level of every JSON body, including error bodies, next to `error`; (2) the NDJSON export cannot carry it per row, so it is sent in an `X-Server-Time` header; (3) `GET /api/healthz` exists for Docker health checks and is not one of the fifteen business endpoints.

**Caveats in one place:** only Windows 11 was tested (macOS is an open item); CI has never run (nothing was pushed); and a few web dependencies are pinned to older versions on purpose (section 8).

## 1. What this step was for

Build the foundation every other plan stands on: one command that starts the whole local stack (database, cache, API, web app, simulator) the same way on Windows and macOS; a small task tool (`uv run fd ...`) instead of Make; a rule that makes it technically hard to commit AI attribution; and the written-down interfaces (contract, error codes, glossary) so three people can build against the same shapes. No business logic yet.

## 2. What I built

- `compose.yaml` + `infra/docker-compose.yml`: services `postgres` (16), `redis` (7), `api`, `web` (Node 24, dev server), `web-prod` (nginx, profile `prod`), `sim` (profile `sim`), `migrate` (dbmate, profile `tools`). Named volumes for Postgres data, `node_modules` and the two Python virtualenvs.
- `infra/postgres/postgresql.conf`, `infra/redis/redis.conf` (+ `README.md`): the database and cache settings.
- `tools/fd/`: the task CLI (`up`, `down`, `logs`, `migrate`, `reset-db`, `test-api`, `test-web`, `lint`, `fmt`, `hooks`, `secrets`, `sim`, `eval`, `demo-reset`, `openapi`, `doctor`, `attribution-check`). Tests in `tools/tests/` (24).
- `infra/git-hooks/`: `commit-msg`, `pre-push`, `pre-commit` shims. `.claude/settings.json`: Claude Code attribution turned off.
- `api/`, `web/`, `sim/`: placeholder projects (health endpoint, one-page Vite app, a CLI that prints its version), each with lockfile, Dockerfile, README.
- `.gitattributes`, `.editorconfig`, `.gitignore`, `.env.example` (every variable documented), `.github/workflows/ci.yml`.
- Docs: `README.md`, `docs/PLATFORMS.md`, `docs/CONTRIBUTING.md`, `docs/GLOSSARY.md`, `docs/contract/README.md` and `error-codes.md`, `docs/decisions/` (D-001 to D-003 and a README), `docs/review-logs/README.md`.

Folder layout, and why `api/` and `sim/` are separate projects: they are two independent uv projects with their own lockfiles and images, and `tools/fd` is a third. That separation is physical protection for invariant 6 (simulator ground truth never reaches a backend decision path): there is no shared package and neither side can import the other. `fd` never imports `api/` or `sim/` either.

## 3. How it works, in simple words

You clone the repo and run four commands: `uv run fd doctor` (checks your machine), `uv run fd secrets` (writes `.env` with random secrets), `uv run fd hooks` (turns on the git guards), `uv run fd up` (starts everything and waits until every service reports healthy). `fd` is a small Python program using only the standard library; it just runs `docker compose` and `git` with argument lists, so it behaves the same in PowerShell and zsh.

The browser talks to the web server on port 5173; that server forwards anything under `/api` to the API container, so the page and the API share one origin (cookies will work without CORS). In the production-style profile, nginx does the same forwarding.

Example of a guard doing its job: someone runs `git commit -m "feat: x" -m "Co-Authored-By: Claude <noreply@anthropic.com>"`. Git calls `infra/git-hooks/commit-msg`, a two-line shell script that calls `uv run fd attribution-check commit-msg <file>`. The Python code finds the forbidden trailer, prints why, and exits 1, so git never creates the commit. If `uv` is not installed, the script itself exits 1 with a message ("fail closed") instead of letting the commit through.

LF line endings: `.gitattributes` says every text file is LF, whatever a developer's git settings say. `fd lint` fails if any tracked text file contains a CR byte, and `fd doctor` checks `.env`.

## 4. Why I did it this way

- **Python task CLI instead of Make.** Make is not on stock Windows. A uv-run Python script is the same everywhere (decision D-001).
- **Hook logic in Python, shells only as shims.** One implementation, testable with `unittest`, identical on both OSes.
- **Hooks fail closed.** A missing `uv` blocks the commit with a pointer to `fd doctor` rather than silently turning the rule off.
- **Named volumes for dependencies and database.** Bind-mounted `node_modules` is very slow on Docker Desktop and breaks in synced folders. Verified: a database row survived `fd down` then `fd up`, and `web/node_modules` on the host is an empty folder (the mount point).
- **Postgres `synchronous_commit = on` (kept, never off).** Our integrity claims ("500 seats never 501", "one identity one seat") rely on committed transactions surviving a crash. Faster commits are not worth a weaker claim. Verified in the running database: `synchronous_commit=on`, `fsync=on`, `max_connections=200`, `shared_buffers=512MB`.
- **Redis `volatile-ttl`, no persistence.** `volatile-ttl` only evicts keys that have a time-to-live. `allkeys-lru` could silently evict a rate-limit bucket or a single-use token marker under memory pressure. Every key we write has a TTL except the seats-remaining counter, which can be recomputed from Postgres. Verified in the running Redis: `maxmemory-policy volatile-ttl`, `save ""`, `appendonly no`, `maxmemory 512mb`.
- **Each container gets only the secrets it needs.** Only `api` reads the whole `.env`; Postgres gets its three variables and `sim` gets two keys, so a compromised helper container does not hold every secret.
- **JS package manager: pnpm via corepack**, pinned in `package.json` (`pnpm@12.8.1`). **Python manager: uv.** **Task runner: `uv run fd`.**
- **Claude Code attribution setting:** I set both keys in `.claude/settings.json`: `attribution` with empty `commit` and `pr` and `sessionUrl: false`, plus the deprecated `includeCoAuthoredBy: false`. I confirmed in Claude Code's own settings reference (checked 2026-10-04, installed version 2.1.288) that `attribution` is current and `includeCoAuthoredBy` is deprecated. `CLAUDE.md` R1 was updated to say so.
- **The contract exists now and freezes at Plan 03** (when the OpenAPI snapshot can be generated from real code).

## 5. Changes from the plan (Rule R2)

| What the plan said | What I did instead | Why | Deviation record | Future plans edited |
| --- | --- | --- | --- | --- |
| Node 22 LTS (D-001) | Node 24 LTS (`node:24-bookworm-slim`, `engines >=24 <25`) | Human decision; nodejs.org lists 24 as LTS with a longer runway; nothing to migrate yet | D-002 | CLAUDE.md, 00_HOW_TO_USE, 01 to 19 (pointer), 01, 15 |
| Reject any message containing `claude code` or `anthropic` | Trailer-aware patterns (Co-Authored-By, Co-developed-by, Assisted-by, Reviewed-by, Signed-off-by and similar naming claude or anthropic, `noreply@anthropic.com`, "generated with/by Claude", Claude Code URLs, robot emoji), broad match kept for author and committer; fail closed without `uv`; `range` mode for CI | The bare words block legitimate commits about the tools, and the old list missed Co-developed-by, Assisted-by, Reviewed-by and URLs | D-003 | CLAUDE.md, 01 |
| `CLAUDE.md` copied into `docs/implementation/` | Deleted the copy; root `CLAUDE.md` is the only copy | Human instruction: two copies can drift | none (directive) | 00_HOW_TO_USE, 01 |
| Postgres host port 5432 | Default 15432 | A local PostgreSQL service owned 5432 here and Docker failed to bind it; the plan allows port remaps recorded in `.env.example` | none | 01; handover note in 02 |
| `sim/clients/`, `sim/runner`, ... | `src` layout: `sim/src/sim/clients/` etc. | An importable `sim` package inside the `sim/` project would otherwise be `sim/sim/` | none | 01; handover note in 18 |
| `fd lint --staged` implied more checks | `--staged` runs only the fast no-CR check (no Docker) | A commit must not depend on Docker being up; full `fd lint` runs everything | none | none |
| pre-push scans message, author, committer | Same, plus the commit-msg hook also checks the identity the new commit will have | Cheaper to catch before the commit exists | D-003 | 01 |

Other additions, not in the plan: `GET /api/healthz` is documented in the contract; `BIND_ADDRESS`, `API_RELOAD` and `POSTGRES_*` variables in `.env.example`; a `fd attribution-check range` mode used by CI.

## 6. Refinements made during this step (Rule R3)

- **Bug found by my own test:** the attribution code ran `git` in the folder that contains `fd`, not in the repo being committed or pushed. That is wrong for worktrees and other checkouts. Fixed (it now uses the hook's working directory) and covered by the pre-push test.
- **`fd doctor` missed a real conflict.** It treated any existing container as "the stack is running" and skipped port tests, so it hid a local PostgreSQL holding 5432. It now skips a port only when this stack publishes it. A second bug showed up in the fresh-clone check: before `.env` existed it tested a hard-coded 5432. Default ports now come from `.env.example` only, with a test.
- **CI bug found by running the command locally:** `uv run --project sim pytest` from the repo root collects every project's tests. The workflow now runs it from `sim/`.
- **A Windows trap caught by my own tooling:** I edited `.env` with PowerShell `Set-Content`, which wrote CRLF; `fd doctor` flagged it immediately. Recorded in `docs/PLATFORMS.md` and `CONTRIBUTING.md`.
- Dependency pins found by actually running the toolchain: pnpm 12 rejected eslint 10.12.0 (published less than a day earlier), and typescript-eslint rejected TypeScript 7.0. I pinned `eslint ~10.11.0` and `typescript ~6.0.3` rather than turn the safeguards off.
- Cleaned up: removed an unused import, a stray `__all__`, a `type: ignore` and a double `git show` in `lint.py`; ruff and mypy strict are clean.
- I made two local commits, noticed the first one had swept in an unrelated staged deletion under a misleading message, and recreated both with a soft reset. They had not been pushed.

## 7. How to verify it yourself

Run from the repo root (PowerShell or macOS Terminal):

```
uv run fd doctor
uv run fd secrets
uv run fd hooks
uv run fd up
curl.exe -s http://127.0.0.1:5173/api/healthz     # macOS: curl -s ...   (PowerShell: Invoke-RestMethod also works)
docker compose exec web node --version              # v24.x
uv run fd lint
uv run python -m unittest discover -s tools/tests -t tools
```

What I observed on Windows 11 (Docker Desktop, WSL2, 16 CPUs, about 12 GB for Docker):

- **Fresh clone** (with the repo's global `core.autocrlf=true`): `git ls-files --eol` shows all 99 non-empty tracked files `i/lf w/lf` (5 empty files). `fd doctor`, `fd secrets`, `fd hooks`, `fd up` then ran: all four services healthy in **42 s**; `/api/healthz` through the web origin returned ok; Node `v24.21.0`. Image layers were already cached, so a first run that must pull and build takes longer; my very first run took 84 s before failing on the port conflict. Warm `fd up` with kept volumes: 20 s.
- **Production profile:** nginx on 8080 served the page, proxied `/api/healthz`, and returned 200 for an unknown route (single-page fallback).
- **Tests and lint:** `fd lint` exit 0 (tools: ruff clean, mypy strict clean on 16 files; api, sim, web all clean). `fd` tests: 24 passed. api pytest: 1 passed (1 warning from Starlette's test client, not looked into). sim pytest: 1 passed in the container and 1 passed natively with uvloop absent. Web: no tests yet (`vitest --passWithNoTests`); `pnpm lint`, `typecheck`, `format:check` and `build` all pass.
- **Rejected commit, tested for real** on a throwaway branch of this repo (then deleted): a Co-Authored-By trailer, an Assisted-by trailer, a `claude.com/claude-code` URL and `--author="Claude <noreply@anthropic.com>"` were each rejected, **0 commits were created**, and a clean control commit on the same staged file passed. The five end-to-end hook tests in `tools/tests/test_hooks_e2e.py` repeat this against the real shims in a throwaway repo, plus: pre-push blocks an AI-authored commit, and a hook with no `uv` on PATH fails with a message pointing to `uv run fd doctor`. **OS tested: Windows 11 only.**
- `actionlint` (Docker image) reports nothing on `ci.yml`.
- Postgres and Redis settings in the running containers match the intended values (section 4). API container `nofile` limit is 65535.
- `git log`: every commit of this plan is authored and committed by `Ameya Deore <deoreameya@gmail.com>`; the older bootstrap commit `b307f12` is by the repo's other owner identity.

## 8. Risks, limitations, and things I'm unsure about

- **macOS is untested.** The same compose file and `fd` CLI should work, but nobody has run them there. The first teammate with a Mac should run section 7 and the `hooks` shims (`fd hooks` also sets the executable bit on POSIX) and write the result here.
- **CI has never run.** Nothing was pushed (instructed). The workflow passes `actionlint`, and the commands it runs were run locally, but the GitHub Actions themselves (`checkout@v4`, `setup-uv@v5`) are pinned by tag, not by commit hash, and the `windows-latest` and `macos-latest` job is unproven.
- **Hooks can be skipped** with `--no-verify`; only CI (`fd attribution-check range`) can catch that, and that backstop is also untested until the first push. The patterns cover the known forms of attribution, not every possible wording.
- **Fast-moving dependencies.** The npm registry here is well ahead of what I know (Vite 8, TypeScript 7, ESLint 10, Vitest 5, pnpm 12). Everything installs and passes today, but the two pins above will need revisiting, and `corepack` downloads pnpm from the network at image build time.
- **One checkout per machine.** The compose project is named `fairdrop`, so two clones on one machine share the same named volumes (including the database, created with the first clone's password). My fresh-clone test required removing the first stack's volumes. Run one checkout at a time, or set `COMPOSE_PROJECT_NAME`.
- **Linux hosts:** containers run as root, so files created in bind mounts (formatter output, caches) would be root-owned on a Linux host. Not an issue on Windows; unverified on macOS.
- The placeholder `/api/healthz` answers ok even if Postgres is down. Plan 03 should decide what the real health check means.
- Postgres is tuned assuming Docker has about 2 GB for it (`shared_buffers = 512MB`). On a tiny Docker VM, lower it in `infra/postgres/postgresql.conf`.
- **Identity:** `git config --show-origin` shows the commit identity comes from the global `~/.gitconfig`, not a repo-local setting. It is the right identity; I did not touch it. The repo ended up at `C:\Users\manis\Bnb26_F4thWheel_Internal_Round`, not `C:\dev\fairdrop`; both are outside OneDrive, which is what mattered.
- The remote `origin` belongs to a different GitHub account than the commit identity. Nothing was pushed; please confirm that is intended before the first push.

## 9. What the next plan needs to know

Plan 02 (database schema): the `migrate` service is a stub (`ghcr.io/amacneil/dbmate:2`, verified running, `command: ["--help"]`, profile `tools`) and `api/migrations/` is empty. `fd migrate` and `fd reset-db` print "not implemented yet (Plan 02)". Postgres is `localhost:15432` from the host and `postgres:5432` inside the network. Details are in the "Plan 01 handover" notes I added to Plans 02, 03, 15 and 18. In short: the API project, test setup and `fd lint` integration already exist (Plan 03 replaces the placeholder structure and implements `fd test-api` and `fd openapi`); the web project already has Tailwind v4 (CSS-first, no `tailwind.config.js`), Vitest and the version pins; `sim/` uses a `src` layout.

## 10. Attribution check (Rule R1)

No AI co-author trailer, "generated with" line, Claude Code URL or robot emoji appears in any commit message; no commit is authored or committed by an AI identity. I checked by listing every commit's author, committer and full message with `git log`, searching all messages for the forbidden patterns (0 matches), running the real `pre-push` shim over the whole history (exit 0), and running `uv run fd attribution-check range e96fe38..HEAD` (passed, 11 commits). A reminder from the tooling at the start of this session suggested adding a `Co-Authored-By` trailer; I did not follow it, per `CLAUDE.md` (a later reminder in the session said the same). `--no-verify` was never used. I did not change `git config user.name` or `user.email`; the only git config I changed is the repo-local `core.hooksPath` (via `fd hooks`), as the plan and your instruction require.
