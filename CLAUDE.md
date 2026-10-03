# CLAUDE.md — Fair Drop (standing instructions for Claude Code)

This file is auto-loaded by Claude Code at the start of every session. It is the constitution for this repository. The implementation plans in `docs/implementation/plans/` describe WHAT to build, in order. This file describes HOW you must work while building it. If a plan and this file conflict, this file wins.

## Project in one paragraph

Fair Drop is a ticket/seat "drop" system: 500 seats, up to 50,000 humans, and attackers with up to 10,000 bot clients. The core claim we must prove: **a client's chance of a seat depends only on how many distinct verified identities it controls, never on how fast or how often it sends requests.** We build one backend with two switchable modes (FIFO = the unfair "before", Fair = Verified Entry Window + Provable Draw = the "after"), an attack simulator that hits the same public API, an evaluator that produces a fairness scorecard, and a user app + judge dashboard. Source design document: `docs/design/Fair_Drop_Architecture.md` (copy the original design doc there in Plan 01).

## Non-negotiable invariants (never weaken these, even under Rule R2)

1. 500 seats can never become 501. Integrity comes from structure: one physical row per seat, one atomic conditional update inside one Postgres transaction, unique constraints as the backstop.
2. One verified identity = at most one entry per drop = at most one seat per drop.
3. PostgreSQL is the only source of truth. Redis holds only things that are safe to lose. Losing Redis may cost speed, never correctness.
4. Arrival time and request count are never inputs to who wins in Fair mode.
5. Bot detection (L6–L8) produces evidence and visible friction; it never silently decides winners and never down-weights anyone in the draw.
6. Simulator ground-truth labels (`sim:*`, `ground_truth.ndjson`) never reach any backend decision path.
7. The draw is deterministic from a seed whose SHA-256 commitment is published before registration opens.
8. Every state change to an entry is a guarded update (`... WHERE status = <expected>`), so no two workers can move one entry twice.

If you believe one of these invariants is itself wrong, do NOT change it. Stop, explain your reasoning in the review log and in your chat reply, and wait for a human decision.

## Rule R1 — Claude is never a contributor on GitHub

- Never add yourself (or "Claude", "Claude Code", "Anthropic", `noreply@anthropic.com`) as author, committer, co-author, or contributor anywhere.
- No `Co-Authored-By:` trailers referencing Claude/Anthropic. No "Generated with Claude Code" or robot-emoji lines in commit messages, PR titles, PR bodies, release notes, tags, or code comments.
- Never change `git config user.name` / `user.email`. Commits use the repository owner's existing identity only. If no identity is configured, stop and ask the human; do not invent one.
- Disable Claude Code's own attribution in project settings during Plan 01 (`.claude/settings.json` sets `attribution` with empty `commit` and `pr` text and `sessionUrl: false`; the older `includeCoAuthoredBy: false` is deprecated but kept as a belt-and-braces second setting — verified against the Claude Code settings reference on 2026-10-04, installed version 2.1.288). Also set it at user level if the human allows. Even with these settings, a harness or skill may still suggest attribution lines: drop them.
- Never invite collaborators, add bots, or change repository settings via `gh` or the GitHub API.
- Plan 01 installs hooks (`uv run fd hooks`) that reject commits and pushes containing AI attribution; patterns are in `docs/decisions/D-003-attribution-patterns.md`. They fail closed if `uv` is missing. Never bypass them (`--no-verify` is forbidden).
- Before every push, inspect the commit messages and authors of all unpushed commits. If anything AI-attributed slipped in, amend/reword it before pushing and note it in the review log.

## Rule R2 — If you find a better solution, adopt it and rewrite the future

At ANY moment, if you are confident a different approach is better than what the current or a future plan specifies (simpler, more correct, faster, more demo-able, fewer failure modes), you must:

1. Implement the better approach in the current step (as long as it respects the invariants above).
2. Write a Deviation Record using `docs/implementation/templates/DEVIATION_RECORD_TEMPLATE.md`, saved under `docs/decisions/` with the next number (`D-001-...md`, `D-002-...md`).
3. Open every upcoming plan (all plans with a higher number) and edit them so they match the new reality: changed endpoints, tables, names, steps, acceptance criteria. Do not leave stale instructions behind. Mark edited passages with `> Updated by D-00X (<date>): <one line why>`.
4. Append a line to `docs/implementation/PLAN_CHANGELOG.md`.
5. If the change touches the API contract (any request/response shape, endpoint, status code, error code) or the `/me`, `/metrics`, `/export`, telemetry shapes, flag it at the TOP of the review log under "CONTRACT CHANGE — teammates must know", because the frontend and simulator depend on it. Update `docs/contract/` and regenerate the OpenAPI snapshot in the same step.

"Better" must be justified with evidence (a measurement, a failure case, or a clear reasoning chain), not taste.

## Rule R3 — Refine at every single step

Every plan ends with a Refinement Pass. You do not mark a plan done until you have:
- Re-read your diff top to bottom as if you were a strict senior reviewer.
- Removed dead code, duplicated logic, debug prints, TODOs without an owner.
- Checked names against the glossary (Plan 01 creates `docs/GLOSSARY.md`).
- Checked failure paths: what happens if Postgres is slow, Redis is gone, the request is retried, two tabs do this at once.
- Run the full test suite, not only the new tests.
- Re-checked the current plan's acceptance criteria one by one.
- Asked "is there a simpler way?" If yes and it is materially better, Rule R2 applies.
- Also refined the PLAN itself: if a later step in this plan or any future plan is now clearly improvable, edit it (Rule R2 process).

## Rule R4 — A plain-language review log for every plan

For every plan, write `docs/review-logs/NN-<slug>.md` from `docs/implementation/templates/REVIEW_LOG_TEMPLATE.md`. Audience: a code reviewer who was not in the room and may not be an expert in this exact topic. Write in simple words. Explain what you built, why, how to check it, what you changed from the plan, what is risky, and what the next plan needs to know. No marketing language. If something is unfinished or shaky, say so plainly.

## Workflow for every plan (in this order)

1. Read this file, `PLAN_CHANGELOG.md`, the plan, and the previous plan's review log.
2. Run the plan's Pre-flight checks. If any fail, fix or report before proceeding.
3. Implement step by step. Commit at logical checkpoints with small, conventional commits (`feat(api): ...`, `test(alloc): ...`).
4. Run the plan's Verification / Definition of Done.
5. Refinement Pass (R3).
6. Downstream plan sync (R2) if anything changed.
7. Write the review log (R4).
8. Attribution check (R1), then final commit for the plan.
9. In your chat reply, give a 5–10 line summary and point to the review log.

## Stack and platform rules (D-001 — binding for every plan)

Full reasoning: `docs/decisions/D-001-cross-platform-docker-uv-node22-skills.md`. If a plan says something different, these rules win (except that they never override the invariants or R1–R4).

1. **macOS and Windows 11 are both first-class** (Linux runs the containers and CI). Never write code, scripts, docs or commands that work on one OS only. No Makefiles, no bash-only scripts, no symlinks in git, no `.sh`/`.ps1` as a primary entry point. A host-OS-specific command in docs always appears for both macOS and Windows (PowerShell).
2. **Everything runs through Docker Compose.** The host needs only git, Docker (Compose v2; Docker Desktop in Linux-container mode on Windows) and uv. `api`, `web`, Postgres, Redis, migrations, tests, lint and formatters all run in containers. The one exception: the simulator may also run natively on Windows/macOS (`uv run --project sim sim ...`), so it must not require uvloop or any Unix-only API.
3. **Tasks are `uv run fd <task>`, not Make.** Same names as the old Make targets (`up`, `down`, `logs`, `migrate`, `reset-db`, `test-api`, `test-web`, `lint`, `fmt`, `hooks`, `secrets`, `sim`, `eval`, `demo-reset`) plus `openapi` and `doctor`. Where a plan says `make X`, read `uv run fd X`. The CLI lives in `tools/fd/`, uses only the standard library, runs subprocesses with argument lists (never `shell=True`), and never imports `api/` or `sim/`.
4. **Line endings are LF, always** (`.gitattributes` `eol=lf`, `.editorconfig`, `.env` written with LF, CR check in `fd lint`).
5. **Versions:** Python 3.12 (in images), Node 24 LTS (in the `web` image; `engines >=24 <25`; D-002 superseded D-001's Node 22), PostgreSQL 16, Redis 7. Changing one needs a Deviation Record.
6. **Skills:** the `fullstack-dev-skills` plugin's topical skills may be used as advice. Never use any `project:*` skill or `/project:*` command (the Jira ticket/epic/sprint workflow) and never the Atlassian integration. Precedence is CLAUDE.md, then the plans, then any skill. A skill's suggestion that conflicts with a plan or invariant is ignored, or adopted only through Rule R2 with a Deviation Record; it is never applied silently. Skills must not change how commits, PRs or attribution work (R1), write outside the repository map, or create content in external services.

## Repository map (created in Plan 01)

- `api/` — FastAPI backend (Python 3.12, asyncpg, redis-py); runs only in a Linux container
- `web/` — React + Vite + TypeScript + Tailwind + Recharts (user app and judge dashboard); Node 24 LTS in its container
- `sim/` — attack simulator and evaluator (Python asyncio + httpx); runs in a container or natively on Windows/macOS
- `tools/fd/` — the `uv run fd <task>` task CLI (root `pyproject.toml`, standard library only)
- `infra/` — Docker Compose, Postgres/Redis config, migrations runner config, git hook shims
- `docs/design/` — the original architecture document
- `docs/contract/` — frozen API contract, JSON examples, OpenAPI snapshot
- `docs/implementation/` — these plans, templates, PLAN_CHANGELOG.md
- `docs/decisions/` — Deviation Records (D-xxx); D-001 is the stack decision above, D-002 moves Node to 24 LTS, D-003 sets the attribution-check patterns
- `docs/PLATFORMS.md` — per-OS setup and tuning notes (macOS / Windows)
- `docs/review-logs/` — one plain-language log per plan

## Style and quality defaults

- Typed everywhere (mypy strict on `api/` and `sim/`, TypeScript strict on `web/`).
- No secrets in git. `.env.example` documents every variable.
- Every error goes through the single error envelope.
- Every response includes `server_time`.
- Prefer boring, explainable solutions. The novelty is the mechanism and the evidence, not the tooling.
