# Plan 17 — Judge Dashboard: Story Strip, Traffic, Ground Truth vs Detected, Inventory, Fairness (Ghost Overlay), Performance, Controls, Draw-Verify

| Field | Value |
|---|---|
| Design-doc sections | §10 Judge/admin view (1–7), §9 metrics + chance band + scorecard, §18 demo script, §20 "features to make especially impressive" |
| Original owner | Ameya |
| Depends on | Plans 14 (endpoints), 15 (engine); 09 (proof), 12 (abuse config), 19 (scorecard/live fairness; mock until then) |
| Unlocks | The demo |
| Target time | 4 hours |

## 0. Mandatory operating protocol (read before writing anything)

1. Read `CLAUDE.md` (repo root) — it overrides this plan if they conflict.
2. Read `docs/implementation/PLAN_CHANGELOG.md`. If an earlier step edited this plan, the edited text is authoritative.
3. Read the review log of the previous plan in `docs/review-logs/` for handover notes.
4. Run the Pre-flight checks in section 3. Do not start building on a broken foundation.

Standing rules that apply to every line of work in this plan:

- **R1 — No AI attribution.** Never add Claude/Anthropic as author, co-author or contributor; no `Co-Authored-By` trailers, no "Generated with Claude Code" lines in commits or PRs; never touch git identity; never use `--no-verify`.
- **R2 — Better solution wins, and the future is rewritten.** If you find a better approach at any point, implement it (respecting the invariants in CLAUDE.md), write a Deviation Record in `docs/decisions/`, edit every affected later plan, and log it in `PLAN_CHANGELOG.md`.
- **R3 — Refine every step.** Do the Refinement Pass at the end of this plan before declaring it done, and refine the remaining plans if this step taught you something.
- **R4 — Review log.** Write `docs/review-logs/17-judge-dashboard.md` in plain language from the template.

> Updated by D-001 (2026-10-04): stack rules apply to this plan. Everything runs through Docker Compose and `uv run fd <task>` (no `make`, no host Python or Node), files are LF, Node is 22 LTS, macOS and Windows are both supported, and fullstack-dev-skills may be used as advice but never its `project:*` workflow commands. CLAUDE.md always wins. See CLAUDE.md "Stack and platform rules" and `docs/decisions/D-001-cross-platform-docker-uv-node22-skills.md`. Read any `make X` below as `uv run fd X`.

## 1. Goal

A projector-ready single screen that tells the whole story top to bottom in under five minutes: what phase we're in, the attack spike and the red wall of rejections, what the simulator knows vs what the backend detected, 500 seats filling with 0 oversold, and the fairness proof — FIFO ghosted beside Fair with bot wins inside the chance band — plus controls and a draw-verify button that turns green.

## 2. Scope

In scope: admin auth gate, data layer for metrics/integrity/sim/runs/proof, the seven panels, ghost overlay, chance band chart, draw verification in a Web Worker, controls with confirmations, projector layout, fallback images.
Out of scope: computing fairness from ground truth (Plan 19 computes; the dashboard displays).

## 3. Pre-flight checks

1. Example payloads for metrics, integrity, sim, runs, draw-proof in `docs/contract/examples/`.
2. `docs/contract/draw.md` exact algorithm (Plan 09).

## 4. Layout (1920×1080, no scrolling for panels 1–5; dark theme; min font 18 px for numbers 48 px+)

Row 1: Story strip (full width).
Row 2: Live traffic chart (2/3) | Ground truth vs detected (1/3).
Row 3: Inventory panel (1/2) | Fairness panel (1/2).
Row 4 (below fold or collapsible): Performance panel | Controls drawer (slide-in from right, hidden during the pitch).
Build order (design: ghost overlay first): Fairness panel with ghost overlay → Inventory counters → Traffic chart → Story strip → Ground truth → Performance → polish.

## 5. Data layer

- Admin key prompt → sessionStorage; all admin calls include it; 401 → re-prompt.
- 1 s polling: metrics, integrity, sim (latest telemetry incl. `fairness_live`). On phase change or reset: refetch runs and drop info. After draw: fetch public proof once.
- Keep a rolling client-side buffer of the last 120 s of series for smooth charts; reset on run change.
- Handle partial failure per panel (each panel shows its own "stale since 3 s" badge rather than blanking the screen).

## 6. Panels

1. **Story strip:** NORMAL → BOT ATTACK → REQUEST SPIKE → MITIGATION → FAIR DRAW. Lit state from simulator `attack_phase` (NORMAL/BOT_ATTACK/SPIKE) combined with backend signals (MITIGATION lit when rate_limited share > X% or flagged entries rising; FAIR DRAW lit when phase ≥ DRAWN in Fair mode). Define the mapping table in the review log. Stretch: animated transitions; cut-list fallback: static labels lit by phase.
2. **Live traffic:** stacked area per second — accepted / rate-limited / token-rejected / duplicate (colours consistent everywhere: accepted green, rate-limited red, token-rejected orange, duplicate grey). Headline number: current RPS and peak RPS (achieved, honest). Annotate the open/close/draw moments from `events[]`.
3. **Ground truth vs detected:** left "Simulator ground truth" (bot clients, bot identities, human clients) with a lock icon and caption "The backend never sees this"; right "Backend detected" (flagged clusters, flagged entries, step-ups issued/passed/failed). Show detection recall/precision when the evaluator provides it.
4. **Inventory:** 500-cell seat grid (CSS grid or canvas; update only changed cells; colour sold cells, optionally by winner label from the live evaluator — only on this admin screen); big counters: allocated, remaining, **oversold: 0**, **duplicates: 0**, `invariant_ok` badge. These come from `/integrity` (SQL), not from metrics counters — say so in a caption ("recomputed by SQL every second").
5. **Fairness:**
   - Bars: bot seat share vs bot entry (identity) share, for the current run and the ghosted previous run (FIFO) drawn translucent beside it.
   - Chance band chart: x = bot seats won, showing the hypergeometric 95% band (from the evaluator or computed client-side using the design formula: mean = capacity × E_bot/E; variance = capacity × p(1−p)(E−capacity)/(E−1); use exact quantiles if the evaluator supplies them) with the measured value as a dot; FIFO's dot ghosted far outside.
   - Advantage ratio gauge: 0–(max) with a target zone 0.7–1.3; FIFO ghost needle.
   - Arrival-order vs win correlation (Spearman) value per run.
   - "Requests per seat: bots N vs humans M."
   Every number labelled with the run (FIFO run #k / Fair run #k+1). Never display a number that the run didn't produce — show "—" until data exists.
6. **Performance:** P50/P95/P99 lines (from histograms), error rate, claims/s; target lines at 300 ms and 1 s.
7. **Controls (drawer):** create drop, mode FIFO/Fair (via reset-with-mode), open / close / draw / reset buttons with confirm dialogs and disabled states per phase; abuse layer toggles L1–L8 with exact labels (L5 = "idempotency cache (DB constraints always on)"); threshold inputs; "Verify draw" button.

## 7. Draw-verify button (in-browser, Web Worker)

1. Fetch the public proof: seed, seed_commit, entry_set_hash, eligible_public_ids, ranked_public_ids.
2. In a Web Worker using WebCrypto: check SHA-256(seed bytes) = seed_commit; check SHA-256(join("\n", sorted ids)) = entry_set_hash; compute HMAC-SHA256(seed, drop_id + "|" + public_id) for every id, sort ascending (tie by id), compare with ranked_public_ids.
3. Show progress (52k HMACs, expect ~1–3 s), then a big green "Draw verified: 3/3 checks passed" or red with the first mismatch.
4. Unit-test the worker against the independent Python verifier's output on the same fixture (from Plan 09 test 4) — byte-exactness is the whole point.

## 8. Fallbacks and demo safety

- "Presentation mode" toggle hides the controls and dev badges.
- Export buttons for each chart as PNG (for backup slides) and a "freeze frame" toggle that stops polling to keep a screen still while talking.
- If the backend is unreachable, panels show the last data with a stale badge (never empty boxes during the pitch).

## 9. Tests

1. Each panel renders with mock payloads for: idle, FIFO under attack, Fair under attack, post-draw, Redis-down (metrics series unavailable).
2. Ghost overlay renders when runs contain a previous FIFO scorecard.
3. Draw-verify worker matches the Python reference on the fixture; flips red on a tampered id.
4. Controls disable invalid transitions per phase.
5. Rendering performance: 1 s polling for 10 min with no memory growth (Chrome performance profile; record).

## 10. Verification / Definition of Done

Full FIFO → reset → Fair run with the simulator shows every panel live; the draw-verify button turns green; screenshots saved to `docs/screens/dashboard/`.

## 11. Plan-update obligations

- Plan 19: evaluator must deliver `fairness_live` and final scorecards in the shapes this dashboard reads (copy final shapes into the contract examples).
- Plan 18: telemetry `attack_phase` values must match the story strip mapping.

## 12. Review log must explain

- What a judge sees in each panel and which endpoint feeds it, in plain words.
- How the ghost overlay is assembled from stored runs.
- How the draw-verify button works and why it's byte-exact with the server.
- What happens on screen if something fails mid-demo.

## Close-out sequence (do all of these, in order)

1. **Definition of Done.** Run every check in the Verification section. Record the actual numbers (tests passed, latencies, oversold counts) — never write a number you did not observe.
2. **Refinement Pass (R3).** Re-read the full diff as a strict reviewer; remove dead code and debug output; check names against `docs/GLOSSARY.md`; walk each failure path (Postgres slow, Redis gone, request retried, two tabs at once); run the whole test suite, not just the new tests; ask "is there a simpler way?" and apply R2 if yes.
3. **Downstream sync (R2).** Open each plan listed in "Plan-update obligations" (and any other later plan you affected). Edit stale instructions, mark them `> Updated by D-00X`, and append to `PLAN_CHANGELOG.md`. If nothing changed, write "no downstream changes" in the review log.
4. **Review log (R4).** Write `docs/review-logs/17-judge-dashboard.md` from the template, covering every item in this plan's "Review log must explain" list.
5. **Attribution check (R1).** List unpushed commits with their author and full message; confirm the author is the repo owner and no message contains AI attribution. Fix by rewording before pushing if needed.
6. **Commit** the plan's final state with a conventional message (e.g. `feat(alloc): atomic claim with skip-locked seats`) and the repo owner's identity.
7. **Chat summary.** 5–10 lines: what is done, key numbers, deviations, link to the review log, what Plan 18 starts with.
