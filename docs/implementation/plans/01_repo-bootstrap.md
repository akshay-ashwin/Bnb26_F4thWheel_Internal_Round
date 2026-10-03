# Plan 01 — Repository, Tooling, Attribution Guardrails & Local Stack

| Field | Value |
|---|---|
| Design-doc sections | §5 Architecture, §6 Technology stack, §16 Interfaces to agree at hour 2, §17 hours 0–2 |
| Original owner | Akshay (stack) + Saanvi (README contract) + Ameya (web skeleton) |
| Depends on | Nothing |
| Unlocks | Every other plan |
| Target time | 1.5–2 hours |

## 0. Mandatory operating protocol (read before writing anything)

1. Read `CLAUDE.md` (repo root) — it overrides this plan if they conflict.
2. Read `docs/implementation/PLAN_CHANGELOG.md`. If an earlier step edited this plan, the edited text is authoritative.
3. Read the review log of the previous plan in `docs/review-logs/` for handover notes.
4. Run the Pre-flight checks in section 3. Do not start building on a broken foundation.

Standing rules that apply to every line of work in this plan:

- **R1 — No AI attribution.** Never add Claude/Anthropic as author, co-author or contributor; no `Co-Authored-By` trailers, no "Generated with Claude Code" lines in commits or PRs; never touch git identity; never use `--no-verify`.
- **R2 — Better solution wins, and the future is rewritten.** If you find a better approach at any point, implement it (respecting the invariants in CLAUDE.md), write a Deviation Record in `docs/decisions/`, edit every affected later plan, and log it in `PLAN_CHANGELOG.md`.
- **R3 — Refine every step.** Do the Refinement Pass at the end of this plan before declaring it done, and refine the remaining plans if this step taught you something.
- **R4 — Review log.** Write `docs/review-logs/01-repo-bootstrap.md` in plain language from the template.

> Updated by D-001 (2026-10-04): stack rules apply to this plan. Everything runs through Docker Compose and `uv run fd <task>` (no `make`, no host Python or Node), files are LF, Node is 24 LTS (D-002), macOS and Windows are both supported, and fullstack-dev-skills may be used as advice but never its `project:*` workflow commands. CLAUDE.md always wins. See CLAUDE.md "Stack and platform rules" and `docs/decisions/D-001-cross-platform-docker-uv-node22-skills.md`. Read any `make X` below as `uv run fd X`.

## 1. Goal

Create a monorepo where one command (`uv run fd up`, which wraps `docker compose up`) brings up Postgres 16, Redis 7, an empty FastAPI service, an empty Vite web app (Node 24) and an idle simulator container, all healthy, identically on macOS and Windows; where AI attribution is technically impossible to commit; and where the shared interfaces are written down before anyone codes against them.

> Updated by D-001 (2026-10-04): the goal now includes the `fd` task CLI and macOS + Windows parity.

## 2. Scope

In scope: directory layout, git hygiene, attribution guardrails, Docker Compose, environment variables, dependency managers, lint/format/type tooling, CI skeleton, contract README skeleton, glossary, placing these plans in the repo.

Out of scope: any business logic, schema (Plan 02), real endpoints (Plan 03 onward).

## 3. Pre-flight checks

1. `git --version`, `docker --version`, `docker compose version` (Compose v2, 2.20 or newer because the root `compose.yaml` uses `include`), `uv --version`. On Windows, Docker Desktop must be in Linux-container mode (WSL2 backend recommended). Nothing else is needed on the host: Python 3.12 lives in the `api`/`sim` images (uv provisions its own interpreter for the `fd` CLI) and Node 24 LTS lives in the `web` image. Once `fd` exists (4.7), `uv run fd doctor` performs this whole list and the port check below.
> Updated by D-001 (2026-10-04): host prerequisites are git + Docker + uv only; Node 20 replaced by Node 22 LTS (in the container). (Node later moved to 24 LTS by D-002.)
2. `git config user.name` and `git config user.email` return the repository owner's identity. If empty: STOP and ask the human. Never set them yourself.
3. Confirm the target GitHub repo (if any) exists and the remote is the owner's. Do not create repos or change repo settings.
4. Confirm free ports: 5432 (Postgres), 6379 (Redis), 8000 (API), 5173 (web dev), 8080 (web prod container). If occupied, choose alternatives and record them in `.env.example`. A port can also be unusable without anything listening on it: on Windows, Hyper-V/WSL reserve ranges (check with `netsh int ipv4 show excludedportrange protocol=tcp` in PowerShell); `fd doctor` tests by binding, which catches both cases on both OSes.
5. If the repository folder is inside a cloud-synced location (OneDrive, iCloud Drive, Dropbox), keep heavy generated trees out of it: `node_modules`, Python virtualenvs and Postgres data go in Docker named volumes (see 4.4), never bind mounts. `fd doctor` warns when it detects such a location. Record the outcome in the review log.
> Updated by D-001 (2026-10-04): cross-OS port and cloud-sync checks added.
> Updated by Plan 01 execution (2026-10-04): the Postgres host port default is now **15432** (set in `.env.example` as the plan allows). A native PostgreSQL service owned 5432 on the first Windows machine and Docker failed to bind it. `fd doctor` reads the default ports from `.env.example` (one source of truth) and `.env` overrides them. The repo must also live outside cloud-synced folders: it was moved out of OneDrive before this plan ran.

## 4. Implementation steps

### 4.1 Directory layout

Create exactly this top level, each with a short README stating its purpose and owner:

- `api/` — backend package (`api/app/` for code, `api/tests/`, `api/migrations/`)
- `web/` — frontend (user app + dashboard in one Vite build)
- `sim/` — simulator (`sim/clients/`, `sim/scenarios/`, `sim/runner`, `sim/evaluator`, `sim/out/` git-ignored)
> Updated by Plan 01 execution (2026-10-04): the simulator uses a `src` layout, so those folders are subpackages at `sim/src/sim/clients/`, `sim/src/sim/scenarios/`, `sim/src/sim/runner/`, `sim/src/sim/evaluator/`; `sim/out/` stays at the project root. Reason: an importable package named `sim` next to the `sim/` project folder would be `sim/sim/`, and a `src` layout avoids importing from the working directory by accident.
- `infra/` — compose file, Postgres config, Redis config, git hook shims (no other shell scripts)
- `tools/fd/` + root `pyproject.toml` and `uv.lock` — the `uv run fd <task>` task CLI (4.7). Standard library only; never imports `api/` or `sim/`.
- `compose.yaml` (repo root) — a real file that `include`s `infra/docker-compose.yml` (no symlinks anywhere in git; they need extra privileges on Windows)
- `docs/PLATFORMS.md` — per-OS notes for macOS and Windows: prerequisites, Docker Desktop settings (WSL2 / Resources), port issues, line endings, OneDrive caveat, simulator tuning, troubleshooting
- `docs/design/` — copy the original architecture document here verbatim as `Fair_Drop_Architecture.md`
- `docs/contract/` — the frozen contract (see 4.8)
- `docs/implementation/` — copy this whole plans folder here: `plans/`, `templates/`, `PLAN_CHANGELOG.md`, `00_HOW_TO_USE.md`
- `docs/decisions/` — empty, with a README explaining Deviation Records
- `docs/review-logs/` — empty, with a README explaining the log purpose
- `CLAUDE.md` at repo root — the ONLY authoritative copy; do not create another under `docs/`
> Updated by human directive (2026-10-04): the duplicate `docs/implementation/CLAUDE.md` was removed so the two copies cannot drift.

### 4.2 Git hygiene

1. Initialise git if not already. Default branch `main`.
2. `.gitignore`: Python caches, virtualenvs, `node_modules`, `dist`, `.env`, `sim/out/`, coverage output, editor folders, OS junk (`.DS_Store`, `Thumbs.db`, `desktop.ini`). Do NOT ignore `.claude/settings.json` (it must be committed so attribution is off for everyone); DO ignore `.claude/settings.local.json`.
3. `.editorconfig` (`end_of_line = lf`, UTF-8, 2 spaces for web, 4 for Python).
4. `.gitattributes`: `* text=auto eol=lf`, explicit `binary` entries for images/fonts/archives. The attribute overrides any developer's `core.autocrlf`, so Windows checkouts get LF too. Add a `.dockerignore` per image.
5. `uv run fd lint` fails if any tracked text file contains a CR byte, and `fd secrets` / `fd doctor` check `.env` for the same. Reason: a CR inside `.env`, an entrypoint or SQL breaks Linux containers in confusing ways.
> Updated by D-001 (2026-10-04): LF enforced by `.gitattributes`, `.editorconfig` and a lint check.

### 4.3 Attribution guardrails (Rule R1 — do this before the first commit)

1. Create `.claude/settings.json` that turns off Claude Code's commit/PR attribution. Check the installed Claude Code version's settings documentation (or `/config`): older versions use the boolean `includeCoAuthoredBy` set to false; newer versions use an `attribution` object where commit and PR attribution text are set to empty. Use whichever your version supports; if both are accepted, set both. Record which one you used in the review log.
2. Create a versioned git hooks directory `infra/git-hooks/` and point git at it via `core.hooksPath` (this is a repo-local config, not identity — allowed; `uv run fd hooks` sets it). The hook files are two-line `sh` shims (Git for Windows ships `sh`; LF endings; executable bit set with `git update-index --chmod=+x` so macOS works) that call `uv run fd attribution-check <commit-msg|pre-push> ...`. All logic lives in Python in `tools/fd/`, so it behaves identically on macOS and Windows. Behaviour: the `commit-msg` check rejects the commit if the message contains (case-insensitive) any of `co-authored-by: claude`, `noreply@anthropic.com`, `generated with claude`, `claude code`, `anthropic`, or the robot emoji, printing a clear reason. The `pre-push` check scans every commit being pushed (read from stdin as git provides it) for the same patterns in message, author and committer fields and blocks the push if found. Add a `pre-commit` shim that calls `uv run fd lint --staged`.
3. Document in `docs/CONTRIBUTING.md`: how hooks are installed (`uv run fd hooks`), that `--no-verify` is forbidden, and the R1 rule in one paragraph.
4. Test the hook: attempt a commit with a forbidden trailer in a throwaway branch, confirm rejection, delete the branch. Run this on every OS you have available (at least the one you are on) and record which OS in the review log. Unit-test the pattern matching in `tools/fd/` tests so it also runs on the Windows and macOS CI runners (4.10).
> Updated by D-001 (2026-10-04): hooks are sh shims over a Python implementation; `make hooks` is `uv run fd hooks`; the pre-commit framework is dropped.
> Updated by D-003 (2026-10-04): the literal pattern list in step 2 is replaced by the narrower, trailer-aware list in `docs/decisions/D-003-attribution-patterns.md` (trailers Co-Authored-By / Co-developed-by / Assisted-by / Reviewed-by naming claude or anthropic, `noreply@anthropic.com`, "generated with/by Claude", Claude Code URLs, robot emoji; broad claude/anthropic match kept for author and committer name/email), because the bare words `claude code` and `anthropic` reject legitimate commits about the tools. Hook shims fail closed when `uv` is missing (message points to `uv run fd doctor`). `fd attribution-check` also has a `range <revspec>` mode for CI.

### 4.4 Docker Compose (`infra/docker-compose.yml`, included by the real file `compose.yaml` at the repo root — no symlink)

> Updated by D-001 (2026-10-04): no symlink (Windows); `ulimits` are compose settings; named volumes and polling file-watch added for Docker Desktop on macOS and Windows.

Cross-OS rules for every service in this file:
- Source code is bind-mounted for development; `node_modules`, the Python virtualenv / uv cache and Postgres data are **named volumes** (bind-mounted dependency trees are very slow on Docker Desktop and fight with OneDrive).
- File-change events do not cross a Windows or macOS bind mount reliably, so dev mode sets Vite `server.watch.usePolling = true` and uvicorn `--reload` with `WATCHFILES_FORCE_POLLING=true`. Performance and demo runs start the API without reload.
- All files copied into images (entrypoints, SQL, config) are LF by 4.2. Do not rely on `chmod` bits from the host: set them in the Dockerfile.
- Images are multi-arch so Apple Silicon and x86 Windows both work: `python:3.12-slim`, `node:24-bookworm-slim` (Debian rather than Alpine so Playwright and native optional dependencies work), `postgres:16`, `redis:7`, dbmate's official image.

Services:

| Service | Image / build | Key settings | Healthcheck |
|---|---|---|---|
| `postgres` | `postgres:16` | Named volume; mounted custom `postgresql.conf` overrides: `max_connections` ≥ 200, `shared_buffers` ~25% of container RAM, `synchronous_commit=on` (never off — durability matters for integrity claims), `log_min_duration_statement` 200 ms for slow-query visibility, `lock_timeout` left default (set per-transaction in code) | `pg_isready` |
| `redis` | `redis:7` | No persistence needed (`save ""`, `appendonly no`); `maxmemory` set (e.g. 512 MB) with `maxmemory-policy volatile-ttl` (decision: never `allkeys-lru`, which could evict rate-limit buckets or `jti` markers silently; every key we write has a TTL except `drop:{id}:remaining`, which is recomputable) | `redis-cli ping` |
| `api` | build `api/` | Depends on healthy postgres + redis; env from `.env`; command runs uvicorn with `UVICORN_WORKERS` (default 4); exposes 8000; compose `ulimits: nofile` raised (e.g. 65535) | HTTP GET `/api/healthz` |
| `web` | build `web/` | Node 24 LTS image. Dev: Vite dev server (polling file-watch) with proxy `/api` → `api:8000`. Prod profile: static build served by nginx with `/api` reverse-proxied to `api` (same origin, so cookies work without CORS) | HTTP GET `/` |
| `sim` | build `sim/` | Idle by default (`profiles: ["sim"]`), runs scenarios on demand; compose `ulimits: nofile` raised; network access to `api`. The simulator can ALSO run natively on the host (Windows/macOS, Plan 18) against the published API port | none |

Add a `migrate` one-shot service that runs migrations against postgres (Plan 02 fills it in); `uv run fd migrate` is its wrapper.

### 4.5 Environment variables (`.env.example`, every variable documented with purpose, default, and "secret? yes/no")

`APP_ENV` (dev/demo/prod), `SIM_MODE` (true/false — enables `dev_otp`, `X-Sim-Client-IP`, `/sim/telemetry`), `DATABASE_URL`, `DB_POOL_MIN`, `DB_POOL_MAX`, `REDIS_URL`, `REDIS_TIMEOUT_MS`, `UVICORN_WORKERS`, `PHONE_PEPPER` (secret), `SESSION_SECRET` (secret), `TOKEN_SIGNING_KEY` (secret) and `TOKEN_KEY_ID`, `ADMIN_KEY` (secret), `SIM_TELEMETRY_KEY` (secret), `COOKIE_SECURE`, `COOKIE_DOMAIN`, `TRUSTED_PROXY_CIDRS`, `DEFAULT_CLAIM_WINDOW_S`, `DEFAULT_WINDOW_S`, `LOG_LEVEL`. Provide `uv run fd secrets`, which generates random secrets into `.env` from `.env.example` (32+ random bytes, hex via Python's `secrets` module), writes the file with LF endings, and refuses to overwrite an existing `.env` without `--force` — never commit `.env`. Compose reads `.env` through `env_file`, so a stray CR would end up inside every secret.
> Updated by D-001 (2026-10-04): `make secrets` replaced by `uv run fd secrets`.

Startup safety rule (implemented in Plan 03): if `APP_ENV=prod` and `SIM_MODE=true`, the API refuses to start.

### 4.6 Language tooling

> Updated by D-001 (2026-10-04): uv is decided (not "uv or Poetry"); three uv projects; Node 22; tools run in containers; pre-commit framework replaced by hook shims. (Node later moved to 24 LTS by D-002.)

- Python: **uv** (decided in D-001; lockfiles). Three separate uv projects: the root `fd` task CLI (`tools/fd/`), `api/`, and `sim/`. They must not share decision code — this physical separation is part of invariant 6. A tiny shared package is allowed ONLY for wire types (Pydantic request/response models) if needed; never for decision logic. Python 3.12 inside the `api` and `sim` images (`uv sync --frozen` at build).
- `sim/pyproject.toml`: `uvloop` is an optional extra (`fast`, with the environment marker `sys_platform != 'win32'`), never a hard dependency, so the simulator installs and runs natively on Windows. The container image installs the extra. Details in Plan 18.
- Lint/format: ruff (lint + format), mypy strict — run inside the project's container via `uv run fd lint` / `fmt`.
- Tests: pytest, pytest-asyncio, httpx — run inside the `api` container via `uv run fd test-api` (Plan 03 §4.10).
- Web: **Node 24 LTS** (`node:24-bookworm-slim`), `engines.node` `>=24 <25`, pnpm enabled with corepack and pinned in `packageManager` (decided; record in the review log), TypeScript strict, ESLint, Prettier — all executed in the `web` container. `pnpm` commands run via `docker compose exec web pnpm ...`; the host has no Node.
- No pre-commit framework (it would need host tools). The `pre-commit` git hook shim calls `uv run fd lint --staged` (4.3).

### 4.7 Task CLI: `uv run fd <task>` (replaces the Makefile; document each task in the root README)

Build `tools/fd/` with argparse and the standard library only. Rules: subprocess calls use argument lists (never `shell=True`); output is forced to UTF-8 and avoids emoji/ANSI-only meaning, so Windows consoles do not crash on a dash; it never imports `api/` or `sim/`; every task works the same in PowerShell, cmd, zsh and bash; it finds the repo root itself, so it can be run from any subfolder.

Tasks (same names as the old Make targets): `up`, `down`, `logs`, `migrate`, `reset-db`, `test-api`, `test-web` (`--e2e` adds Playwright, Plan 15), `lint` (`--staged`), `fmt`, `hooks`, `secrets`, `sim --scenario <name> --mode <fifo|fair> [--native]`, `eval --run <id> [--native]`, `demo-reset`. Two additions: `openapi` (export the OpenAPI snapshot, Plan 03) and `doctor` (pre-flight, 3). Tasks that need later plans print "not implemented yet (Plan NN)". `--native` means "run on the host with `uv run --project sim`" (Windows/macOS/second laptop); the default is the compose `sim` service.

Mapping table (task → what it runs) goes in the root README; the one in `docs/decisions/D-001-...md` is the starting point.

### 4.8 Contract skeleton (`docs/contract/README.md`)

Write the six interfaces the design doc requires to be agreed at hour 2, as prose + JSON examples (no code): the error envelope, the `/me` JSON, the `/metrics` JSON, the `/export` NDJSON row, the rate-limiter middleware decision interface (`check(request) → Decision{allow|reject, layer, code, retry_after_ms}`), and the `sim:*` telemetry shape. Copy shapes from design doc §11 exactly. Add a full endpoint table (all public, admin and sim endpoints) with status codes and error codes. Mark the file "FROZEN at Plan 03 completion; changes only via Deviation Record (R2) with a CONTRACT CHANGE banner."

Also create `docs/contract/error-codes.md` listing every code: `RATE_LIMITED`, `OTP_THROTTLED`, `INVALID_PHONE`, `OTP_INVALID`, `OTP_EXPIRED`, `UNAUTHENTICATED`, `WINDOW_CLOSED`, `WINDOW_NOT_OPEN`, `TOKEN_INVALID`, `NOT_OFFERED`, `OFFER_EXPIRED`, `SOLD_OUT`, `STEP_UP_REQUIRED`, `IDEMPOTENCY_KEY_REUSED`, `IDEMPOTENCY_KEY_MISSING`, `INVALID_TRANSITION`, `NOT_FOUND`, `VALIDATION_ERROR`, `SERVICE_UNAVAILABLE`, `INTERNAL`, each with HTTP status, meaning, whether the client should retry, and which UI state it maps to.

### 4.9 Glossary (`docs/GLOSSARY.md`)

Define once and use consistently: drop, phase, mode, identity, user, user_public_id, session, sid_hash, entry, offer, waitlist, allocation, seat, admission token, jti, idempotency key, seed, seed_commit, entry_set_hash, draw rank, step-up, risk score, risk flag, cluster, layer L1–L8, advantage ratio, chance band, ground truth, actor, run.

### 4.10 CI skeleton (`.github/workflows/ci.yml`)

Jobs: lint (ruff, mypy, eslint, tsc), api tests (with Postgres + Redis service containers), web tests (Node 24), attribution scan of the PR's commits (`uv run fd attribution-check range <base>..HEAD`, D-003), and a placeholder "ground-truth isolation" job (Plan 14 makes it real). CI must not post comments, add labels, or use any bot identity.

> Updated by D-001 (2026-10-04): OS matrix. The Docker-based jobs above run on `ubuntu-latest` only (GitHub's Windows and macOS runners cannot run Linux containers). Add a second job on `windows-latest` and `macos-latest` that needs no Docker: `fd` CLI unit tests, attribution-check pattern tests, a CR-byte lint test, and (from Plan 18) the simulator's unit tests and native smoke run with uvloop absent.

### 4.11 Placeholder apps

- `api/`: a minimal app exposing `/api/healthz` returning ok + `server_time` (just enough for compose health; Plan 03 replaces structure).
- `web/`: Vite React TS template with Tailwind installed and one page reading "Fair Drop".
- `sim/`: a CLI entry point that prints its version and exits; it must do so both in the container and natively on Windows/macOS without uvloop installed.

## 5. Decisions you must make and record

| Decision | Recommended default | Record in |
|---|---|---|
| Python dependency manager | uv (decided by D-001) | review log |
| JS package manager | pnpm via corepack, pinned in `packageManager` | review log |
| Task runner | `uv run fd` (decided by D-001) | review log |
| Claude Code attribution setting key used | per installed version | review log |
| Redis eviction policy | volatile-ttl | `infra/redis/README.md` |
| Port remaps (if any) | none | `.env.example` |

## 6. Verification / Definition of Done

1. Fresh clone → `uv run fd doctor && uv run fd secrets && uv run fd up` → all services healthy within 60 s. Run it on every OS available to the team and record each (macOS and Windows are both required to be supported; if only one is available now, say so and leave the other as an open item for the first teammate who has it).
2. An HTTP GET to `/api/healthz` via the web origin (through the proxy) returns ok — proves the same-origin path works. In Windows PowerShell 5.1, `curl` is an alias for `Invoke-WebRequest`; use `curl.exe` (ships with Windows 10/11) or `Invoke-RestMethod`. Document both forms in `docs/PLATFORMS.md`.
3. Commit-msg hook rejects a message with a Claude co-author trailer; pre-push hook rejects a commit authored with an anthropic email (test on a throwaway local branch, then delete it). Also confirm a missing `uv` blocks the hook with a message pointing to `uv run fd doctor`. `tools/tests` automates all of this against the real shims in a throwaway repository.
4. CI workflow file passes a local lint (actionlint if available); the Windows/macOS job passes.
5. `docs/contract/README.md`, `error-codes.md`, `GLOSSARY.md`, `CONTRIBUTING.md`, `PLATFORMS.md` exist and are complete.
6. `git log` shows only the owner's identity.
7. `git ls-files --eol` shows `i/lf w/lf` for every tracked text file, and `fd lint` reports no CR bytes. `docker compose exec web node --version` prints v24.x.

## 7. Plan-update obligations

- If you remapped ports, changed package managers or directory names → update Plans 02, 03, 15, 18 wherever paths/commands are mentioned.
- If you renamed or added an `fd` task, or changed its flags → update every plan that quotes it (02, 03, 15, 16, 18, 19) and the D-001 mapping table.
- If the Claude Code attribution setting name differs → update `CLAUDE.md` R1 text.

## 8. Review log must explain

- The folder layout and why `api/` and `sim/` are separate projects (ground-truth isolation).
- How the attribution guardrails work (settings + hook shims over `fd attribution-check`) and the proof they were tested, including on which OS.
- How the same commands work on macOS and Windows: what `fd` does, what is in `docs/PLATFORMS.md`, how LF is enforced, and anything that only was or was not verified on one OS.
- What each env var does and which are secrets.
- Why Redis uses `volatile-ttl` and Postgres keeps `synchronous_commit=on`.
- That the contract exists and when it freezes.

## Close-out sequence (do all of these, in order)

1. **Definition of Done.** Run every check in the Verification section. Record the actual numbers (tests passed, latencies, oversold counts) — never write a number you did not observe.
2. **Refinement Pass (R3).** Re-read the full diff as a strict reviewer; remove dead code and debug output; check names against `docs/GLOSSARY.md`; walk each failure path (Postgres slow, Redis gone, request retried, two tabs at once); run the whole test suite, not just the new tests; ask "is there a simpler way?" and apply R2 if yes.
3. **Downstream sync (R2).** Open each plan listed in "Plan-update obligations" (and any other later plan you affected). Edit stale instructions, mark them `> Updated by D-00X`, and append to `PLAN_CHANGELOG.md`. If nothing changed, write "no downstream changes" in the review log.
4. **Review log (R4).** Write `docs/review-logs/01-repo-bootstrap.md` from the template, covering every item in this plan's "Review log must explain" list.
5. **Attribution check (R1).** List unpushed commits with their author and full message; confirm the author is the repo owner and no message contains AI attribution. Fix by rewording before pushing if needed.
6. **Commit** the plan's final state with a conventional message (e.g. `feat(alloc): atomic claim with skip-locked seats`) and the repo owner's identity.
7. **Chat summary.** 5–10 lines: what is done, key numbers, deviations, link to the review log, what Plan 02 starts with.
