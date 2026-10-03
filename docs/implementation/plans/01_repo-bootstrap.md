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

## 1. Goal

Create a monorepo where one command (`docker compose up`) brings up Postgres 16, Redis 7, an empty FastAPI service, an empty Vite web app and an idle simulator container, all healthy; where AI attribution is technically impossible to commit; and where the shared interfaces are written down before anyone codes against them.

## 2. Scope

In scope: directory layout, git hygiene, attribution guardrails, Docker Compose, environment variables, dependency managers, lint/format/type tooling, CI skeleton, contract README skeleton, glossary, placing these plans in the repo.

Out of scope: any business logic, schema (Plan 02), real endpoints (Plan 03 onward).

## 3. Pre-flight checks

1. `git --version`, `docker --version`, `docker compose version`, Python 3.12 available, Node 20 LTS available.
2. `git config user.name` and `git config user.email` return the repository owner's identity. If empty: STOP and ask the human. Never set them yourself.
3. Confirm the target GitHub repo (if any) exists and the remote is the owner's. Do not create repos or change repo settings.
4. Confirm free ports: 5432 (Postgres), 6379 (Redis), 8000 (API), 5173 (web dev), 8080 (web prod container). If occupied, choose alternatives and record them in `.env.example`.

## 4. Implementation steps

### 4.1 Directory layout

Create exactly this top level, each with a short README stating its purpose and owner:

- `api/` — backend package (`api/app/` for code, `api/tests/`, `api/migrations/`)
- `web/` — frontend (user app + dashboard in one Vite build)
- `sim/` — simulator (`sim/clients/`, `sim/scenarios/`, `sim/runner`, `sim/evaluator`, `sim/out/` git-ignored)
- `infra/` — compose file, Postgres config, Redis config, helper scripts
- `docs/design/` — copy the original architecture document here verbatim as `Fair_Drop_Architecture.md`
- `docs/contract/` — the frozen contract (see 4.8)
- `docs/implementation/` — copy this whole plans folder here: `plans/`, `templates/`, `PLAN_CHANGELOG.md`, `00_HOW_TO_USE.md`
- `docs/decisions/` — empty, with a README explaining Deviation Records
- `docs/review-logs/` — empty, with a README explaining the log purpose
- `CLAUDE.md` at repo root (copy from this folder)

### 4.2 Git hygiene

1. Initialise git if not already. Default branch `main`.
2. `.gitignore`: Python caches, virtualenvs, `node_modules`, `dist`, `.env`, `sim/out/`, coverage output, editor folders, OS junk. Do NOT ignore `.claude/settings.json` (it must be committed so attribution is off for everyone); DO ignore `.claude/settings.local.json`.
3. `.editorconfig` (LF endings, UTF-8, 2 spaces for web, 4 for Python).
4. `.gitattributes` forcing LF for text files.

### 4.3 Attribution guardrails (Rule R1 — do this before the first commit)

1. Create `.claude/settings.json` that turns off Claude Code's commit/PR attribution. Check the installed Claude Code version's settings documentation (or `/config`): older versions use the boolean `includeCoAuthoredBy` set to false; newer versions use an `attribution` object where commit and PR attribution text are set to empty. Use whichever your version supports; if both are accepted, set both. Record which one you used in the review log.
2. Create a versioned git hooks directory `infra/git-hooks/` and point git at it via `core.hooksPath` (this is a repo-local config, not identity — allowed). Add a `commit-msg` hook whose behaviour is: reject the commit if the message contains (case-insensitive) any of `co-authored-by: claude`, `noreply@anthropic.com`, `generated with claude`, `claude code`, `anthropic`, or the robot emoji; print a clear reason. Add a `pre-push` hook that scans every commit being pushed for the same patterns in message, author and committer fields and blocks the push if found.
3. Document in `docs/CONTRIBUTING.md`: how hooks are installed (`make hooks`), that `--no-verify` is forbidden, and the R1 rule in one paragraph.
4. Test the hook: attempt a commit with a forbidden trailer in a throwaway branch, confirm rejection, delete the branch. Record the result in the review log.

### 4.4 Docker Compose (`infra/docker-compose.yml`, symlinked or referenced from repo root `compose.yaml`)

Services:

| Service | Image / build | Key settings | Healthcheck |
|---|---|---|---|
| `postgres` | `postgres:16` | Named volume; mounted custom `postgresql.conf` overrides: `max_connections` ≥ 200, `shared_buffers` ~25% of container RAM, `synchronous_commit=on` (never off — durability matters for integrity claims), `log_min_duration_statement` 200 ms for slow-query visibility, `lock_timeout` left default (set per-transaction in code) | `pg_isready` |
| `redis` | `redis:7` | No persistence needed (`save ""`, `appendonly no`); `maxmemory` set (e.g. 512 MB) with `maxmemory-policy volatile-ttl` (decision: never `allkeys-lru`, which could evict rate-limit buckets or `jti` markers silently; every key we write has a TTL except `drop:{id}:remaining`, which is recomputable) | `redis-cli ping` |
| `api` | build `api/` | Depends on healthy postgres + redis; env from `.env`; command runs uvicorn with `UVICORN_WORKERS` (default 4); exposes 8000; `ulimit nofile` raised (e.g. 65535) | HTTP GET `/api/healthz` |
| `web` | build `web/` | Dev: Vite dev server with proxy `/api` → `api:8000`. Prod profile: static build served by nginx with `/api` reverse-proxied to `api` (same origin, so cookies work without CORS) | HTTP GET `/` |
| `sim` | build `sim/` | Idle by default (`profiles: ["sim"]`), runs scenarios on demand; `ulimit nofile` raised; network access to `api` | none |

Add a `migrate` one-shot service (or a make target) that runs migrations against postgres (Plan 02 fills it in).

### 4.5 Environment variables (`.env.example`, every variable documented with purpose, default, and "secret? yes/no")

`APP_ENV` (dev/demo/prod), `SIM_MODE` (true/false — enables `dev_otp`, `X-Sim-Client-IP`, `/sim/telemetry`), `DATABASE_URL`, `DB_POOL_MIN`, `DB_POOL_MAX`, `REDIS_URL`, `REDIS_TIMEOUT_MS`, `UVICORN_WORKERS`, `PHONE_PEPPER` (secret), `SESSION_SECRET` (secret), `TOKEN_SIGNING_KEY` (secret) and `TOKEN_KEY_ID`, `ADMIN_KEY` (secret), `SIM_TELEMETRY_KEY` (secret), `COOKIE_SECURE`, `COOKIE_DOMAIN`, `TRUSTED_PROXY_CIDRS`, `DEFAULT_CLAIM_WINDOW_S`, `DEFAULT_WINDOW_S`, `LOG_LEVEL`. Provide a `make secrets` target description that generates random secrets into `.env` (32+ random bytes, hex) — never commit `.env`.

Startup safety rule (implemented in Plan 03): if `APP_ENV=prod` and `SIM_MODE=true`, the API refuses to start.

### 4.6 Language tooling

- Python: choose `uv` (preferred: fast, lockfile) or Poetry; record the decision. Separate projects for `api/` and `sim/` (they must not share decision code — this physical separation is part of invariant 6). A tiny shared package is allowed ONLY for wire types (Pydantic request/response models) if needed; never for decision logic.
- Lint/format: ruff (lint + format), mypy strict.
- Tests: pytest, pytest-asyncio, httpx.
- Web: Node 20, pnpm or npm (record choice), TypeScript strict, ESLint, Prettier.
- Pre-commit configuration running ruff, mypy (on changed files), prettier, eslint, and the attribution check.

### 4.7 Make targets (document each in the root README)

`up`, `down`, `logs`, `migrate`, `reset-db`, `test-api`, `test-web`, `lint`, `fmt`, `hooks`, `secrets`, `sim SCENARIO=<name> MODE=<fifo|fair>`, `eval RUN=<id>`, `demo-reset`. Targets that need later plans print "not implemented yet (Plan NN)".

### 4.8 Contract skeleton (`docs/contract/README.md`)

Write the six interfaces the design doc requires to be agreed at hour 2, as prose + JSON examples (no code): the error envelope, the `/me` JSON, the `/metrics` JSON, the `/export` NDJSON row, the rate-limiter middleware decision interface (`check(request) → Decision{allow|reject, layer, code, retry_after_ms}`), and the `sim:*` telemetry shape. Copy shapes from design doc §11 exactly. Add a full endpoint table (all public, admin and sim endpoints) with status codes and error codes. Mark the file "FROZEN at Plan 03 completion; changes only via Deviation Record (R2) with a CONTRACT CHANGE banner."

Also create `docs/contract/error-codes.md` listing every code: `RATE_LIMITED`, `OTP_THROTTLED`, `INVALID_PHONE`, `OTP_INVALID`, `OTP_EXPIRED`, `UNAUTHENTICATED`, `WINDOW_CLOSED`, `WINDOW_NOT_OPEN`, `TOKEN_INVALID`, `NOT_OFFERED`, `OFFER_EXPIRED`, `SOLD_OUT`, `STEP_UP_REQUIRED`, `IDEMPOTENCY_KEY_REUSED`, `IDEMPOTENCY_KEY_MISSING`, `INVALID_TRANSITION`, `NOT_FOUND`, `VALIDATION_ERROR`, `SERVICE_UNAVAILABLE`, `INTERNAL`, each with HTTP status, meaning, whether the client should retry, and which UI state it maps to.

### 4.9 Glossary (`docs/GLOSSARY.md`)

Define once and use consistently: drop, phase, mode, identity, user, user_public_id, session, sid_hash, entry, offer, waitlist, allocation, seat, admission token, jti, idempotency key, seed, seed_commit, entry_set_hash, draw rank, step-up, risk score, risk flag, cluster, layer L1–L8, advantage ratio, chance band, ground truth, actor, run.

### 4.10 CI skeleton (`.github/workflows/ci.yml`)

Jobs: lint (ruff, mypy, eslint, tsc), api tests (with Postgres + Redis service containers), web tests, attribution scan of the PR's commits, and a placeholder "ground-truth isolation" job (Plan 14 makes it real). CI must not post comments, add labels, or use any bot identity.

### 4.11 Placeholder apps

- `api/`: a minimal app exposing `/api/healthz` returning ok + `server_time` (just enough for compose health; Plan 03 replaces structure).
- `web/`: Vite React TS template with Tailwind installed and one page reading "Fair Drop".
- `sim/`: a CLI entry point that prints its version and exits.

## 5. Decisions you must make and record

| Decision | Recommended default | Record in |
|---|---|---|
| Python dependency manager | uv | review log |
| JS package manager | pnpm | review log |
| Claude Code attribution setting key used | per installed version | review log |
| Redis eviction policy | volatile-ttl | `infra/redis/README.md` |
| Port remaps (if any) | none | `.env.example` |

## 6. Verification / Definition of Done

1. Fresh clone → `make secrets && make up` → all services healthy within 60 s.
2. `curl` to `/api/healthz` via the web origin (through the proxy) returns ok — proves same-origin path works.
3. Commit-msg hook rejects a message with a Claude co-author trailer; pre-push hook rejects a commit authored with an anthropic email (test on a throwaway local branch, then delete it).
4. CI workflow file passes a local lint (actionlint if available).
5. `docs/contract/README.md`, `error-codes.md`, `GLOSSARY.md`, `CONTRIBUTING.md` exist and are complete.
6. `git log` shows only the owner's identity.

## 7. Plan-update obligations

- If you remapped ports, changed package managers or directory names → update Plans 02, 03, 15, 18 wherever paths/commands are mentioned.
- If the Claude Code attribution setting name differs → update `CLAUDE.md` R1 text.

## 8. Review log must explain

- The folder layout and why `api/` and `sim/` are separate projects (ground-truth isolation).
- How the attribution guardrails work (settings + two hooks) and the proof they were tested.
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
