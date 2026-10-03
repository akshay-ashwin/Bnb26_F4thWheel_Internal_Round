# Fair Drop — Implementation Plans: How to Use This Pack with Claude Code

This pack turns the Fair Drop architecture document into 19 sequential, self-contained implementation plans. Each plan is one Claude Code session (or a few). No plan contains code; each describes exactly what to build, in what order, how to verify it, and what to write down for the reviewer.

## What's in the pack

| Path | Purpose |
|---|---|
| (repo root) `CLAUDE.md` | Standing rules for Claude Code, loaded automatically every session. Contains the 8 invariants and rules R1–R4. It lives ONLY at the repo root; there is no copy in this folder, so the two cannot drift (human directive, 2026-10-04; see PLAN_CHANGELOG). |
| `00_HOW_TO_USE.md` | This file. |
| `PLAN_CHANGELOG.md` | Running log of every edit made to the plans after they were written (Rule R2). D-001 (stack decision) is the first entry. |
| `templates/REVIEW_LOG_TEMPLATE.md` | The plain-language log Claude writes after every plan (Rule R4). |
| `templates/DEVIATION_RECORD_TEMPLATE.md` | Used whenever Claude adopts a better solution than the plan (Rule R2). |
| `plans/01_…` to `plans/19_…` | The implementation plans. |

## Setup (once)

> Updated by D-001 (2026-10-04): prerequisites and the task runner changed. The project runs on macOS and Windows; see `docs/decisions/D-001-cross-platform-docker-uv-node22-skills.md`.

0. Install the only three host prerequisites: **git**, **Docker** (Compose v2; Docker Desktop on macOS and Windows, with Linux containers / the WSL2 backend on Windows), and **uv**. You do not need Python, Node or Make on the host: Python 3.12 and Node 24 LTS live inside the containers. Every task is `uv run fd <task>` (for example `uv run fd up`); where older text says `make X`, read `uv run fd X`.
1. Create the empty repository (or use your existing one) and make sure `git config user.name` / `user.email` are YOUR identity.
2. Make sure `CLAUDE.md` is at the repo root (it is the only authoritative copy).
3. Copy this whole folder to `docs/implementation/` in the repo (Plan 01 also tells Claude to do this; doing it yourself first is fine).
4. Copy the original architecture document to `docs/design/Fair_Drop_Architecture.md`.
5. Start Claude Code in the repo root.

Skills: the `fullstack-dev-skills` plugin may be used as advice (see the table in D-001), but never its `project:*` / `/project:*` workflow commands. `CLAUDE.md` wins over any skill, especially for deviations (Rule R2) and attribution (Rule R1).

## Running a plan

Paste a prompt like this for each step (change the number):

> Read CLAUDE.md, docs/implementation/PLAN_CHANGELOG.md, and docs/implementation/plans/NN_<name>.md. Follow the plan's operating protocol, implement it fully, run its verification, do the refinement pass, update any later plans that need to change, write the review log, and finish with the attribution check. Do not start plan NN+1.

Review the review log (`docs/review-logs/NN-*.md`) before starting the next plan. If Claude edited future plans (check `PLAN_CHANGELOG.md` and `docs/decisions/`), skim those edits — that's Rule R2 working as intended.

## The four rules (full text in CLAUDE.md)

- **R1 — No AI attribution on GitHub.** Enforced three ways: Claude Code's attribution setting turned off in `.claude/settings.json`, a commit-msg hook, and a pre-push hook (Plan 01), plus a manual check at the end of every plan.
- **R2 — Better solution wins and the future is rewritten.** Claude implements the better approach, writes a Deviation Record, edits every affected later plan, and logs it. Guardrail: it may never weaken the 8 invariants in CLAUDE.md; if it thinks an invariant is wrong it must stop and ask you.
- **R3 — Refine at every step.** Every plan ends with a refinement pass over both the code and the remaining plans.
- **R4 — Review log per plan.** Simple-language explanation for the code reviewer: what, why, how to verify, deviations, risks, handover.

## Plan index

| # | Plan | Design-doc owner | Builds |
|---|---|---|---|
| 01 | Repository, tooling, attribution guardrails, local stack | All | Monorepo, Compose, hooks, contract skeleton, CI |
| 02 | Database schema, constraints, migrations, integrity views | Akshay | 8 + 3 tables, integrity view, constraint tests |
| 03 | Backend core | Akshay | FastAPI skeleton, frozen contract models, error envelope, pools |
| 04 | Identity: OTP, users, sessions | Akshay | Phone hash, OTP, cookie + bearer sessions |
| 05 | Idempotency framework | Akshay | Keys, semantic hashing, stored responses |
| 06 | Drop lifecycle & admin control | Akshay | Seed commitment, phase machine, reset with run history |
| 07 | Entries + `/me` | Akshay | One entry per identity, status endpoint, polling policy |
| 08 | Allocation engine + FIFO end-to-end | Akshay | SKIP LOCKED claim, integrity endpoint, concurrency gate |
| 09 | Fair mode draw | Akshay | Window freeze, commit-reveal ranking, offers, proof |
| 10 | Admission tokens (L4) + Fair claim | Akshay | Session-bound single-use tokens |
| 11 | Sweeper, waitlist, step-up (L8), resilience | Akshay | Leader jobs, promotion, outage handling |
| 12 | Abuse L1–L5 | Saanvi | Redis Lua buckets, shedding middleware, toggles |
| 13 | Abuse L6–L7 risk scoring | Saanvi | OTP throttles, cluster rules, re-score at close |
| 14 | Metrics, export, telemetry, isolation | Saanvi + Akshay | `/metrics`, `/export`, `sim:*`, CI isolation check |
| 15 | Frontend foundation | Ameya | Typed client, retry/idempotency engine, poller, mocks |
| 16 | User journey screens | Ameya | Every user state, e2e reliability tests |
| 17 | Judge dashboard | Ameya | 7 panels, ghost overlay, draw-verify button |
| 18 | Attack simulator | Saanvi | Labelled clients, 9 scenarios + sweep, runner |
| 19 | Evaluator, hardening, demo readiness | All | Scorecard, budget sweep, chaos, final runs, runbook |

## Dependency order and parallelism

Strict order for a single Claude Code session: 01 → 19 as numbered.

If you run several sessions/teammates in parallel, these are safe after the listed prerequisites:

- 15 (frontend foundation) can start right after 03 (it works on mocks).
- 18 (simulator skeleton, safety guard, identity factory) can start after 04; scenarios come online as 07–11 land.
- 12 can start after 04 (it only needs session parsing and the middleware slot).
- 16 after 15; 17 after 15 (on mocks) and finishes after 14.
- 19 runs last, but its evaluator can be started once 14 and 18 exist.

Critical path (design doc §17 "build first"): 02 → 08 (integrity) → 09/10 (fairness mechanism) → 18/19 (evidence) → 17 (display).

## Things the plans resolve that the design doc left open (so you're not surprised)

- **FIFO admission tokens** (Plan 08): the contract requires a token on every claim, but tokens were only defined for Fair offers. FIFO now issues tokens to REGISTERED entries while OPEN so both modes share one code path.
- **Idempotency hash for claim** (Plan 05): `/me` mints fresh tokens, so hashing the raw claim body would wrongly trigger `IDEMPOTENCY_KEY_REUSED` on legitimate retries. The hash covers the request's meaning (drop + entry), not the token string.
- **Byte-exact draw definition** (Plan 09): explicit `|` separator and encodings so the browser verify button and the server can never disagree.
- **Public draw proof** (Plan 09): a public endpoint with the eligible id list, because "anyone can verify" needs the list.
- **Window-close race** (Plans 07, 09): window enforced in the insert on the DB clock, plus a grace period before freezing the set.
- **Multi-worker background jobs** (Plan 11): Postgres advisory-lock leader election so the sweeper runs once, not four times.
- **Step-up OTP delivery** (Plan 11): the server stores only the phone hash, so SIM_MODE exposes the step-up code; production would need encrypted phone storage.
- **Re-scoring at close** (Plan 13): early accounts of a farm aren't flagged at entry time; re-scoring before the draw fixes that without touching ranks.
- **Run history for the ghost overlay** (Plans 02, 06, 14): reset archives the FIFO run so the dashboard can show it beside the Fair run.

Each of these is marked as a decision to record, so the review logs make them visible.
