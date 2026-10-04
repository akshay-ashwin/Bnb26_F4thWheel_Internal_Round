# Plan 14 — Metrics, Export, Simulator Telemetry & Ground-Truth Isolation

> **[CUT] by D-004 (2026-10-04, LEAN MODE):** do not build: histograms (use client-observed latency posted by the simulator), OpenAPI drift CI, mechanical isolation check (keep a simple grep test). Also skip anything in "stretch", "optional" or "if time permits" text.

| Field | Value |
|---|---|
| Design-doc sections | §5 Metrics row, §6 Metrics, §9 metrics list, §10 judge view data needs, §11 `/admin/.../metrics`, `/export`, `/sim/telemetry`, §12 `m:*`, `sim:*`, §15 "labels never reach the backend" |
| Original owner | Saanvi (counters) + Akshay (endpoints) |
| Depends on | Plans 08–13 (they emit events through hooks) |
| Unlocks | Plans 17 (dashboard), 18 (telemetry), 19 (evaluator) |
| Target time | 2.5 hours |

## 0. Mandatory operating protocol (read before writing anything)

1. Read `CLAUDE.md` (repo root) — it overrides this plan if they conflict.
2. Read `docs/implementation/PLAN_CHANGELOG.md`. If an earlier step edited this plan, the edited text is authoritative.
3. Read the review log of the previous plan in `docs/review-logs/` for handover notes.
4. Run the Pre-flight checks in section 3. Do not start building on a broken foundation.

Standing rules that apply to every line of work in this plan:

- **R1 — No AI attribution.** Never add Claude/Anthropic as author, co-author or contributor; no `Co-Authored-By` trailers, no "Generated with Claude Code" lines in commits or PRs; never touch git identity; never use `--no-verify`.
- **R2 — Better solution wins, and the future is rewritten.** If you find a better approach at any point, implement it (respecting the invariants in CLAUDE.md), write a Deviation Record in `docs/decisions/`, edit every affected later plan, and log it in `PLAN_CHANGELOG.md`.
- **R3 — Refine every step.** Do the Refinement Pass at the end of this plan before declaring it done, and refine the remaining plans if this step taught you something.
- **R4 — Review log.** Write `docs/review-logs/14-metrics-export-telemetry.md` in plain language from the template.

> Updated by D-001 (2026-10-04): stack rules apply to this plan. Everything runs through Docker Compose and `uv run fd <task>` (no `make`, no host Python or Node), files are LF, Node is 24 LTS (D-002), macOS and Windows are both supported, and fullstack-dev-skills may be used as advice but never its `project:*` workflow commands. CLAUDE.md always wins. See CLAUDE.md "Stack and platform rules" and `docs/decisions/D-001-cross-platform-docker-uv-node22-skills.md`. Read any `make X` below as `uv run fd X`.

## 1. Goal

One JSON endpoint gives the dashboard everything it needs every second (traffic by outcome, latency percentiles, inventory, flags, step-ups), an NDJSON export gives the evaluator every entry's fate, the simulator can post ground truth for display only, and a CI check proves no decision code can read that ground truth.

## 2. Scope

In scope: in-process metric aggregation + Redis flush, latency histograms, active-session estimate, `/admin/metrics`, `/admin/export`, `/sim/telemetry`, run summaries + scorecard storage endpoints, isolation checks (CI), OpenAPI drift check.
Out of scope: dashboard rendering (17), fairness math (19).

## 3. Pre-flight checks

1. Metric hooks exist at: middleware timing (Plan 03), rate-limit outcomes (12), duplicate collapse (05/07), token rejections (10), claim outcomes (08), step-ups and sweeper (11), flags and OTP throttles (13).
2. `drop_runs` table exists (Plan 02).

## 4. Metric collection design

### 4.1 In-process aggregation, periodic flush (performance decision — record it)
Writing to Redis on every request at 10k+ RPS doubles Redis load. Instead each worker aggregates counters and histograms in memory per (drop, epoch second) and flushes every 250 ms with one pipelined `HINCRBY` batch to `m:{drop}:{epoch_s}` (TTL 1 h). Lost on crash: at most 250 ms of counts (acceptable: counts are evidence, not decisions). If Redis is down, keep up to 60 s in memory, then drop oldest and count `metrics_dropped`.

### 4.2 Fields per second (hash fields)
`req_total`, `accepted`, `rate_limited` (+ `rate_limited:L1/L2/L3`), `token_rejected`, `duplicate`, `otp_throttled`, `errors_5xx`, `claims_ok`, `claims_sold_out`, `step_up_issued/passed/failed`, `offers_expired`, `promoted`, per endpoint group `req:{group}`.

### 4.3 Latency histograms (mergeable across workers)
Fixed bucket upper bounds in ms: 1, 2, 5, 10, 20, 50, 100, 200, 300, 500, 750, 1000, 2000, 5000, +inf. Each request increments `lat:{group}:{bucket}` in the per-second hash. Percentiles over a window = sum buckets across seconds, then interpolate within the bucket. This is accurate enough for P95/P99 targets (< 300 ms / < 1 s) because bucket edges sit on those targets — note this in the review log. Averages are not reported (design: misleading).

### 4.4 Active sessions
Per-second HyperLogLog `hll:{drop}:{epoch_s}` of session ids (PFADD in the flush, from an in-process set); active sessions over 60 s = PFCOUNT of the union.

### 4.5 Story-strip phase events
Phase transitions and the draw push a small event list `ev:{drop}` (capped list) with timestamp + phase; the dashboard uses it along with simulator `attack_phase`.

## 5. `GET /admin/drops/{id}/metrics?window_s=60`

Response (contract shape + additions marked):
- `rps_series[]`: per second `{t, total}`.
- `outcomes_series`: `{accepted[], rate_limited[], token_rejected[], duplicate[]}` aligned with rps_series (addition: `rate_limited_by_layer` object of arrays — record).
- `latency {p50, p95, p99}` across all groups + (addition) `latency_by_group`.
- `error_rate` = 5xx / total over window.
- `active_sessions`.
- `entries`, `offers`, `allocated`, `remaining`, `flagged_entries` — from Postgres via one aggregate query on entries by status (indexed), cached 1 s in-process; `remaining` from integrity.

> Updated by D-004 (2026-10-04) after Plan 02: `remaining` and the integrity fields come from `v_drop_integrity` (field names listed in Plan 02 §4.5 and Plan 08), which reads `entries` once through the `(drop_id, status, draw_rank)` index; counting entries by status for the other fields uses the same index.
- `step_ups {issued, passed, failed}`.
- (addition) `phase`, `mode`, `run_no`, `events[]`, `achieved_peak_rps` (max over the run).
Budget: < 50 ms per call; the dashboard polls every 1 s.

## 6. `GET /admin/drops/{id}/export` (NDJSON, streamed)

Row per entry: `{user_public_id, entry_id, entered_at, risk_score, risk_flags, rank, status, seat_no?}` + (additions, record) `run_no`, `offered_at`, `allocated_at`, `step_up_passed_at`. Stream with a server-side cursor in chunks (no full materialisation), ordered by entered_at. Also support `?format=csv` optionally. Content-Type `application/x-ndjson`. Must handle 52k rows in < 3 s.

## 7. `POST /sim/telemetry` and run storage

- Mounted only when SIM_MODE=true; authenticated by `X-Sim-Key` (SIM_TELEMETRY_KEY).
- Body: `{run_id, attack_phase, clients_by_label{human,bot}, identities_by_label, requests_by_label}` + (addition) optional `fairness_live` object computed by the live evaluator (Plan 19): bot entry share, bot seat share, advantage ratio, chance band.
- Writes to `sim:{drop}:latest` (JSON, TTL 1 h) and `sim:{drop}:series` (capped list). Nothing else.
- `GET /admin/drops/{id}/sim` (addition) returns the latest telemetry for the dashboard — the ONLY reader of `sim:*`, and it lives in the admin/sim read module, not in any decision module.
- Runs: `GET /admin/drops/{id}/runs` returns `drop_runs` rows (mode, run_no, summary, scorecard); `PUT /admin/drops/{id}/runs/{run_no}/scorecard` (addition) lets the evaluator upload the final scorecard so the dashboard can draw the ghosted previous run.

## 8. Ground-truth isolation (invariant 6) — make it mechanically enforced

1. Module boundaries: decision modules = auth, entries, draw, claim, tokens, sweeper, step-up, abuse (L1–L8), risk. Read-only presentation modules = admin metrics/export/sim/runs.
2. Use an import-linter style contract (or a simple CI script written in Python, never shell, so it runs on any OS and inside `uv run fd lint`; > Updated by D-001, 2026-10-04) asserting: no decision module imports the sim router, sim schemas, or the sim repository; and the string prefix `sim:` appears only in the sim repository module. Fail CI otherwise.
3. Also assert the backend codebase never reads headers or fields named label/actor/ground_truth.
4. Document in `docs/contract/isolation.md` and reference it in Q&A prep.

## 9. OpenAPI drift check

CI job regenerates OpenAPI and diffs with `docs/contract/openapi.json`; a diff fails CI unless the PR also changes `docs/contract/CHANGELOG.md` (contract change discipline, R2).

## 10. Tests

1. Counters from 4 workers aggregate correctly (fire known request mix, compare totals).
2. Percentiles from histograms match exact percentiles on a synthetic distribution within one bucket.
3. Metrics endpoint < 50 ms with 1 h of data.
4. Export streams 52k rows < 3 s with constant memory.
5. Telemetry rejected without key or when SIM_MODE=false.
6. Isolation check fails on a deliberately bad import in a test branch (then revert).
7. Redis down → metrics endpoint still returns PG-derived fields and marks series as unavailable.

## 11. Verification / Definition of Done

Tests pass; a FIFO and a Fair run each produce a populated metrics response, an export file, and a runs entry; CI isolation + drift checks green.

## 12. Plan-update obligations

- Plan 15/17: final JSON shapes (copy example payloads into `docs/contract/examples/`).
- Plan 18: telemetry posting cadence and key.
- Plan 19: export fields, runs scorecard upload, `fairness_live` shape.

## 13. Review log must explain

- How numbers get from a request to the dashboard in under a second, in simple steps.
- Why we use histograms instead of averages and what P95/P99 mean.
- How we guarantee the backend never peeks at the simulator's answer key, and how CI proves it.
- Every contract addition (with banner).

## Close-out sequence (do all of these, in order)

1. **Definition of Done.** Run every check in the Verification section. Record the actual numbers (tests passed, latencies, oversold counts) — never write a number you did not observe.
2. **Refinement Pass (R3).** Re-read the full diff as a strict reviewer; remove dead code and debug output; check names against `docs/GLOSSARY.md`; walk each failure path (Postgres slow, Redis gone, request retried, two tabs at once); run the whole test suite, not just the new tests; ask "is there a simpler way?" and apply R2 if yes.
3. **Downstream sync (R2).** Open each plan listed in "Plan-update obligations" (and any other later plan you affected). Edit stale instructions, mark them `> Updated by D-00X`, and append to `PLAN_CHANGELOG.md`. If nothing changed, write "no downstream changes" in the review log.
4. **Review log (R4).** Write `docs/review-logs/14-metrics-export-telemetry.md` from the template, covering every item in this plan's "Review log must explain" list.
5. **Attribution check (R1).** List unpushed commits with their author and full message; confirm the author is the repo owner and no message contains AI attribution. Fix by rewording before pushing if needed.
6. **Commit** the plan's final state with a conventional message (e.g. `feat(alloc): atomic claim with skip-locked seats`) and the repo owner's identity.
7. **Chat summary.** 5–10 lines: what is done, key numbers, deviations, link to the review log, what Plan 15 starts with.
