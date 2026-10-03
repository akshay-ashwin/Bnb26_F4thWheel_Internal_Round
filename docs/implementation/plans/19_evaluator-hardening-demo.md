# Plan 19 — Evaluator & Scorecard, Identity-Budget Sweep, Chaos + Load Hardening, Final Runs & Demo Readiness

| Field | Value |
|---|---|
| Design-doc sections | §1 core claim + "prove it", §9 all metrics + judge scorecard, §14 failure handling + MUST/SHOULD/STRETCH + cut list, §17 hours 10–18, §18 5-minute demo, §19 judge Q&A, §20 weaknesses + biggest weakness fixed |
| Original owner | Saanvi (evaluator, runs) + Akshay (perf, integrity Q&A) + Ameya (demo screens) |
| Depends on | Everything (Plans 01–18) |
| Unlocks | Code freeze and the demo |
| Target time | 5–6 hours (spread across the last third of the build) |

## 0. Mandatory operating protocol (read before writing anything)

1. Read `CLAUDE.md` (repo root) — it overrides this plan if they conflict.
2. Read `docs/implementation/PLAN_CHANGELOG.md`. If an earlier step edited this plan, the edited text is authoritative.
3. Read the review log of the previous plan in `docs/review-logs/` for handover notes.
4. Run the Pre-flight checks in section 3. Do not start building on a broken foundation.

Standing rules that apply to every line of work in this plan:

- **R1 — No AI attribution.** Never add Claude/Anthropic as author, co-author or contributor; no `Co-Authored-By` trailers, no "Generated with Claude Code" lines in commits or PRs; never touch git identity; never use `--no-verify`.
- **R2 — Better solution wins, and the future is rewritten.** If you find a better approach at any point, implement it (respecting the invariants in CLAUDE.md), write a Deviation Record in `docs/decisions/`, edit every affected later plan, and log it in `PLAN_CHANGELOG.md`.
- **R3 — Refine every step.** Do the Refinement Pass at the end of this plan before declaring it done, and refine the remaining plans if this step taught you something.
- **R4 — Review log.** Write `docs/review-logs/19-evaluator-hardening-demo.md` in plain language from the template.

## 1. Goal

Turn runs into evidence. Produce a scorecard that answers the problem statement with measured numbers (advantage ratio, share vs share inside a chance band, arrival-order independence, requests per win, 0 oversold, latency, FPR, recall), prove resilience with chaos runs, tune performance from measurements, record the final FIFO vs Fair runs with fixed seeds, and package everything the team needs to demo and answer judges without ever quoting a number a run didn't produce.

## 2. Scope

In scope: evaluator (offline + live), scorecard JSON + charts + markdown, scorecard upload to runs, identity-budget sweep chart, scenario expectation checks, chaos suite, load/perf tuning loop, L1 sizing, final recorded runs, demo runbook, Q&A evidence map, freeze checklist, cross-plan final review log.

## 3. Pre-flight checks

1. Export, integrity, metrics, runs endpoints (14); ground truth + manifests (18); public draw proof (09).
2. Full-scale combined scenario has run at least once end to end.

## 4. Evaluator (`sim/evaluator`)

### 4.1 Inputs and join
Export NDJSON (fetched via admin), ground truth NDJSON, manifest, metrics window for the run, integrity snapshot, public draw proof. Join export ↔ ground truth on `user_public_id`; report unmatched counts (should be 0 except humans who never verified).

### 4.2 Definitions (write them into `docs/evaluation.md` exactly; they're what you'll say to judges)
- E_bot, E_human = valid entries (verified identities that entered and were eligible) by label. W_bot, W_human = seats allocated by label.
- **Advantage ratio** A = (W_bot / E_bot) / (W_human / E_human). Edge cases: if W_human = 0 or E_bot = 0, report "undefined" with the reason, never a fake number. Add a 95% confidence interval for A via bootstrap over identities (2,000 resamples) or via the exact hypergeometric band transformed — choose and record.
- **Per-request ratio**: (W_bot / R_bot) / (W_human / R_human) where R = requests sent (from ground truth) — shows volume buys nothing.
- **Bot seat share vs bot entry share.**
- **Chance band:** exact hypergeometric 95% interval for bot wins given capacity (number of seats actually allocated in the run), E, E_bot — use exact quantiles (scipy hypergeom) and also print the design's normal-approximation numbers for comparison. Pass = measured W_bot inside band. In Fair mode with step-up, compute the band over the population that actually received offers (document precisely — waitlist promotions and step-up failures change who wins; the honest test is "bots won no more than chance allows"; consider a one-sided test too and record the choice).
- **Speed independence:** Spearman correlation between arrival order (entered_at rank) and won (0/1) over all valid entries; report with p-value. FIFO expected strongly negative; Fair ≈ 0.
- **Requests per successful allocation** by label.
- **Integrity:** oversold, duplicates, invariant_ok from `/integrity` (SQL) at the end of the run AND the minimum invariant_ok over the run from polled samples.
- **Latency:** P50/P95/P99 at peak (the 10 s window with highest RPS) and overall; **error rate** 5xx at peak.
- **Human FPR:** humans flagged ≥ threshold / humans with entries; also share of humans who actually had to step up.
- **Detection precision/recall** on farmed identities (flag ≥ threshold vs label bot), shown, not required.
- Optional: Gini of wins across actors (attacker as one actor vs humans).

### 4.3 Outputs (`sim/out/<run_id>/`)
`scorecard.json` (every metric + target + pass/fail + the run identifiers), `scorecard.md` (the design's judge scorecard table filled in), charts as SVG + PNG: share bars, chance band, advantage gauge, arrival-vs-win scatter/binned plot, latency lines, requests-per-win bars. Upload `scorecard.json` to `PUT /admin/drops/{id}/runs/{run_no}/scorecard` so the dashboard's ghost overlay can use it.

### 4.4 Live mode
During a run, every 2 s fetch export (or an incremental export since last fetch — add `?since=` if needed, record) + in-memory ground truth from the coordinator; compute shares, A, band; post as `fairness_live` via telemetry. Must be cheap; skip if the previous computation hasn't finished.

### 4.5 Expectation checker
For each scenario file, compare its stated expectations to measured results and print PASS/FAIL lines (e.g., bot_flood: "attacker holds exactly 1 entry" PASS; ">99% of attacker requests rejected" PASS 99.6%).

## 5. Identity-budget sweep (the chart that pre-empts "you just moved the problem to identity")
Run combined_demo with bot identity budgets 0, 500, 2,000, 5,000, 10,000 in FIFO and Fair (fixed seeds). Plot bot seat share (y) vs bot identity share (x) with the diagonal y = x. Expected: FIFO far above the diagonal at every budget; Fair hugging it. Save as `docs/evidence/identity_budget_sweep.svg/png` and include the raw table. If time is short, run budgets 0, 2,000, 10,000 only and say so.

## 6. Performance tuning loop (measure → change one thing → re-measure; log each iteration in the review log)
1. Baseline the flash crowd and combined scenarios: record P95/P99, error rate, achieved RPS per endpoint group, Postgres CPU, Redis ops/s, pool wait times, `/me` cache hit ratio.
2. Check slow queries (log_min_duration_statement, EXPLAIN ANALYZE on entries insert, `/me` query, claim, integrity view, sweeper).
3. Levers in order: `/me` cache hit ratio and poll pacing → pool sizes per worker → uvicorn workers (CPU-bound?) → indexes → Redis pipelining → metrics flush interval. Never: `synchronous_commit=off`, disabling constraints, or skipping the entry row lock.
4. Size L1 global limits at ~80% of the measured sustainable RPS per group (Plan 12) and re-run to confirm humans see 0 429s.
5. Targets: P95 < 300 ms, P99 < 1 s, 5xx < 1% at peak (design). If not met on the hardware, report the real numbers and the reason (e.g., simulator-bound) — never round toward the target.

## 7. Chaos suite (each run on the combined scenario at moderate scale; integrity must stay ok)
1. Kill Redis during registration; restart after 20 s.
2. Kill Redis during the claim storm.
3. Restart the API container during the claim storm.
4. Stop Postgres for 10 s during CLAIMING → offers extended by the gap (Plan 11).
5. Kill the leader worker during CLAIMING → sweeper resumes on another worker.
6. Simulated lost responses at the proxy (inject drops) → no duplicate allocations.
Record results in `docs/evidence/chaos.md`: what happened, what users saw, integrity before/after. Optional stretch: dashboard chaos toggles that run these live (design STRETCH) — only if everything else is done.

## 8. Final recorded runs
- FIFO and Fair combined_demo × 3 each with fixed seeds (published in the manifest), on the demo hardware/network.
- Screen-record both full runs (dashboard + one user screen) as the fallback video (design: pre-record, run live only if venue rehearsal passed).
- Export chart PNGs as backup slides.
- Fill the demo script's X values ONLY from these recorded runs (design rule: never quote a number the run did not produce). Put them in `docs/demo/numbers.md` with run ids.

## 9. Demo runbook (`docs/demo/RUNBOOK.md`)
- Hardware/network setup (simulator on second laptop if available), pre-flight checklist (compose healthy, secrets, SIM_MODE on, drop created, admin key in dashboard, presentation mode on, fallback video ready, browser zoom, phone for the live user segment).
- Exact sequence matching design §18 timings with who clicks what: verify on phone → FIFO run → integrity callout → reset to Fair → same attack → draw + verify button → fairness panel → architecture slide.
- Reset procedure between rehearsals (one command), and what to do if: the simulator stalls, the API is slow, the projector resolution differs, the network dies (switch to video).
- Timer targets; rehearse until under 5:00 twice in a row (design gate).

## 10. Judge Q&A evidence map (`docs/demo/QA.md`)
For each question in design §19 and each criticism in §20: the one-line answer, the artifact that proves it (chart path, scorecard field, test name, endpoint), and who answers. Add honest limits: simulated OTP has zero real cost (shown via the budget sweep), operator could pre-grind seeds without drand (state whether drand was implemented), load numbers are achieved-not-claimed, ASN signal status, step-up phone-storage decision.

## 11. Freeze checklist (code freeze gate)
All MUST items from design §14 done or explicitly cut with the cut-list rationale; CI green (lint, tests, isolation, OpenAPI drift, attribution scan); no TODOs without owners; `.env.example` complete; README quickstart verified on a fresh clone; final attribution audit of the entire git history (authors, committers, trailers) — R1.

## 12. Verification / Definition of Done
- Scorecards for FIFO and Fair exist from the final runs, with every target marked pass/fail honestly.
- Identity-budget sweep chart exists.
- Chaos report exists with integrity ok in every case (or a documented, fixed bug).
- Runbook and Q&A docs exist; two consecutive rehearsals under 5:00.

## 13. Plan-update obligations
This is the last plan; instead, write `docs/review-logs/19-final-summary.md` (in addition to this plan's own log) summarising all 19 plans: what was built, all deviations (D-records) and why, final numbers, known limitations, and a "how to run everything from scratch" section.

## 14. Review log must explain
- Every scorecard metric in plain words and its measured value in both modes.
- What the identity-budget sweep shows and why it's the answer to the hardest judge question.
- Performance changes made and their measured effect.
- Chaos outcomes.
- Anything that did not meet target, stated plainly with the reason.

## Close-out sequence (do all of these, in order)

1. **Definition of Done.** Run every check in the Verification section. Record the actual numbers (tests passed, latencies, oversold counts) — never write a number you did not observe.
2. **Refinement Pass (R3).** Re-read the full diff as a strict reviewer; remove dead code and debug output; check names against `docs/GLOSSARY.md`; walk each failure path (Postgres slow, Redis gone, request retried, two tabs at once); run the whole test suite, not just the new tests; ask "is there a simpler way?" and apply R2 if yes.
3. **Downstream sync (R2).** Open each plan listed in "Plan-update obligations" (and any other later plan you affected). Edit stale instructions, mark them `> Updated by D-00X`, and append to `PLAN_CHANGELOG.md`. If nothing changed, write "no downstream changes" in the review log.
4. **Review log (R4).** Write `docs/review-logs/19-evaluator-hardening-demo.md` from the template, covering every item in this plan's "Review log must explain" list.
5. **Attribution check (R1).** List unpushed commits with their author and full message; confirm the author is the repo owner and no message contains AI attribution. Fix by rewording before pushing if needed.
6. **Commit** the plan's final state with a conventional message (e.g. `feat(alloc): atomic claim with skip-locked seats`) and the repo owner's identity.
7. **Chat summary.** 5–10 lines: what is done, key numbers, deviations, link to the review log, and a pointer to `docs/review-logs/19-final-summary.md` (this is the final plan).
