# Plan 06 — Drop Lifecycle: Admin Control, Seed Commitment, Phase State Machine, Reset & Runs

> **[CUT] by D-004 (2026-10-04, LEAN MODE):** do not build: dual auto-close (keep the ticker only), public drop cache. Also skip anything in "stretch", "optional" or "if time permits" text.

| Field | Value |
|---|---|
| Design-doc sections | §4 steps 2–4, §11 `GET /drops/{id}`, `POST /admin/drops`, `POST /admin/drops/{id}/phase`, §12 drops, §13 state machine, §18 demo flow (switch FIFO ↔ Fair, reset) |
| Original owner | Akshay |
| Depends on | Plans 02, 03 |
| Unlocks | Plans 07–11, 17 (controls), 18–19 (run orchestration) |
| Target time | 2 hours |

## 0. Mandatory operating protocol (read before writing anything)

1. Read `CLAUDE.md` (repo root) — it overrides this plan if they conflict.
2. Read `docs/implementation/PLAN_CHANGELOG.md`. If an earlier step edited this plan, the edited text is authoritative.
3. Read the review log of the previous plan in `docs/review-logs/` for handover notes.
4. Run the Pre-flight checks in section 3. Do not start building on a broken foundation.

Standing rules that apply to every line of work in this plan:

- **R1 — No AI attribution.** Never add Claude/Anthropic as author, co-author or contributor; no `Co-Authored-By` trailers, no "Generated with Claude Code" lines in commits or PRs; never touch git identity; never use `--no-verify`.
- **R2 — Better solution wins, and the future is rewritten.** If you find a better approach at any point, implement it (respecting the invariants in CLAUDE.md), write a Deviation Record in `docs/decisions/`, edit every affected later plan, and log it in `PLAN_CHANGELOG.md`.
- **R3 — Refine every step.** Do the Refinement Pass at the end of this plan before declaring it done, and refine the remaining plans if this step taught you something.
- **R4 — Review log.** Write `docs/review-logs/06-drop-lifecycle.md` in plain language from the template.

> Updated by D-001 (2026-10-04): stack rules apply to this plan. Everything runs through Docker Compose and `uv run fd <task>` (no `make`, no host Python or Node), files are LF, Node is 24 LTS (D-002), macOS and Windows are both supported, and fullstack-dev-skills may be used as advice but never its `project:*` workflow commands. CLAUDE.md always wins. See CLAUDE.md "Stack and platform rules" and `docs/decisions/D-001-cross-platform-docker-uv-node22-skills.md`. Read any `make X` below as `uv run fd X`.

## 1. Goal

An admin can create a drop, see its published seed commitment before anything opens, move it through phases with guarded transitions (FIFO and Fair have different paths), reset it for a re-run (optionally switching mode), and every reset preserves the previous run's history for the ghost overlay.

## 2. Scope

In scope: admin auth, drop creation with seats, seed generation + commitment, phase transitions, automatic close at window end, public drop read with caching, reset with run archival, mode switch.
Out of scope: the draw itself (Plan 09; this plan wires the `draw` action to call it), the sweeper (Plan 11).

## 3. Pre-flight checks

1. Seat generation routine and `admin_reset_drop` exist (Plan 02).
2. Idempotency delete-by-drop exists (Plan 05).

## 4. Phase state machine (implement exactly; put the table in the review log)

### Fair mode

| From | Action / trigger | To | Side effects |
|---|---|---|---|
| SCHEDULED | `open` | OPEN | set reg_opens_at = now, reg_closes_at = now + window_s |
| OPEN | `close` OR clock passes reg_closes_at | CLOSED | set reg_closes_at = min(existing, now); closed_at; entry_set_hash computed after the grace period (Plan 09 owns the freeze) |
| CLOSED | `draw` | DRAWN → immediately CLAIMING | Plan 09 runs the draw in one transaction; seed revealed; offers issued |
| CLAIMING | sweeper finds no active offers and no waitlist, or all seats sold, or admin `close` | DONE | remaining non-final entries → NOT_SELECTED (Plan 11) |
| any | `reset` | SCHEDULED | archive run, clear run data, new seed + commit, run_no + 1, optional mode switch |

### FIFO mode

| From | Action | To | Side effects |
|---|---|---|---|
| SCHEDULED | `open` | OPEN | entries AND claims accepted at once (design §11) |
| OPEN | all seats sold (detected in claim path) or admin `close` | DONE | registered-but-unallocated → NOT_SELECTED |
| OPEN/DONE | `draw` | — | `409 INVALID_TRANSITION` (no draw in FIFO) |
| any | `reset` | SCHEDULED | as above |

Every transition is one guarded update (`… WHERE id = $1 AND phase = $expected`) — zero rows updated → `409 INVALID_TRANSITION` (unless the drop is already in the target phase, in which case return `200` with the current phase: admin actions are idempotent).

## 5. Implementation steps

### 5.1 Admin authentication
`X-Admin-Key` header compared in constant time to `ADMIN_KEY`; failure → 401. Admin endpoints are exempt from L1–L3 rate limits (Plan 12) but logged.

### 5.2 `POST /admin/drops`
1. Validate `{name, capacity (default 500, 1..10,000), mode, window_s (10..3600), claim_window_s (10..3600)}`.
2. Generate `seed` = 32 bytes from the OS CSPRNG; store as lowercase hex.
3. `seed_commit = SHA-256(raw 32 seed bytes)` lowercase hex. Write the exact definition into the GLOSSARY and `docs/contract/draw.md` (Plan 09 and the browser verifier depend on byte-exact definitions).
4. One transaction: insert drop (phase SCHEDULED, run_no 1) + generate seats.

> Updated by D-004 and D-005 (2026-10-04) after Plan 02: call the SQL function `create_drop(name, capacity, mode, window_s, claim_window_s, seed_commit, seed, ...)`; the application role cannot INSERT into `drops` or `seats`. The function stores `seed` from creation (this plan's honesty note stands) and returns the id. `capacity` can never change afterwards (the seats' foreign key forbids it), so a different size means a new drop.
5. Initialise Redis `drop:{id}:remaining = capacity`.
6. Respond `201 {drop_id, seed_commit}`.

Honesty note for the review log: the server stores the seed from creation, so the operator could peek. Commit-before-open prevents choosing the seed after seeing entries, but not an operator who pre-grinds seeds. Mitigation is the drand stretch (Plan 09 §stretch). Say this plainly.

### 5.3 `POST /admin/drops/{id}/phase {action}`
Dispatch to the transition table. `draw` calls the draw service from Plan 09 (stub returns 501 until then). After any transition: invalidate `drop:{id}:public` cache and publish the phase change to metrics (Plan 14 hook) so the story strip lights up.

### 5.4 Automatic close (Fair)
Two mechanisms, both required:
- Lazy: every entries request computes the effective phase from `reg_closes_at` using the DB clock (Plan 07 enforces it in the insert itself).
- Active: the leader ticker (built in Plan 11; for now a simple per-process task guarded by a Postgres advisory lock) transitions OPEN → CLOSED when `now() >= reg_closes_at`, so the dashboard shows CLOSED without admin action.

### 5.5 `GET /drops/{id}` (public)
Returns `{id, name, capacity, mode, phase, reg_opens_at, reg_closes_at, claim_window_s, seats_remaining, seed_commit, seed?, entry_set_hash?}`.
- `seed` only when phase ∈ {DRAWN, CLAIMING, DONE}. `entry_set_hash` only when phase ≥ CLOSED and computed.
- `seats_remaining` from Redis `drop:{id}:remaining`; if missing/Redis down, from `v_drop_integrity.free`.
- Cache the whole body in Redis `drop:{id}:public` for 1 s (contract: cacheable 1 s); also set `Cache-Control: max-age=1`. This endpoint will be hammered at launch.
- Unknown id → 404 NOT_FOUND.

### 5.6 Reset (`action: "reset"`, optional `mode` field)
Contract extension (record as D-record + CONTRACT CHANGE banner): the phase body accepts an optional `mode` only with `reset`. Reason: the demo switches the same drop FIFO → reset → Fair; without this the dashboard would have to create a new drop and every client would need the new id. If you prefer creating a new drop instead, that is acceptable — but then Plans 16–19 must handle drop id changes; choose and record.

Reset steps (one transaction where possible):
1. Snapshot the finished run into `drop_runs` (mode, started/ended, a metrics summary pulled from Plan 14's summarizer once it exists — until then store counts by entry status and integrity view row).
2. Call `admin_reset_drop` (clears allocations, frees seats, deletes entries and idempotency records, increments run_no).

> Updated by D-005 (2026-10-04) after Plan 02: `SELECT admin_reset_drop($1)` returns the new `run_no` and also clears `drawn_at`, `closed_at`, `done_at` and `entry_set_hash`. It does NOT touch `phase`, `mode`, `seed_commit` or `seed`: set those afterwards with a normal UPDATE (the application role may update `mode`, `phase`, `seed_commit`, `seed` and the timestamps, but never `capacity` or `run_no`). Archive the finished run into `drop_runs` BEFORE calling it (the application may INSERT there and later UPDATE `ended_at`, `summary`, `scorecard`).
3. New seed + seed_commit; clear seed reveal, entry_set_hash, timestamps; phase SCHEDULED; apply mode if supplied.
4. After commit: bump the per-drop `/me` cache version `drop:{id}:me_ver` (Plan 07; O(1) invalidation of every cached status), delete `drop:{id}:*` keys other than `me_ver`, `idem:reg:*` is user-scoped and harmless to leave; cluster sets `cl:*` scoped to the drop; `jti:*` harmless); reset `remaining`. Do NOT delete `sim:*` or metrics history (metrics are keyed by drop and timestamp; include run_no in the metrics summary so charts can split runs).
5. Sessions and users survive reset (humans don't re-verify between demo runs). The simulator decides whether to reuse identities.

### 5.7 `GET /admin/drops` list (small addition, record it)
Returns drops with id, name, mode, phase, run_no — lets the dashboard pick the drop without hardcoding ids.

## 6. Tests

1. Create → seed_commit equals SHA-256 of the stored seed bytes; seed not exposed publicly before draw.
2. Every allowed transition succeeds; every disallowed one returns 409; repeating an action in its target phase returns 200.
3. FIFO: `draw` → 409.
4. Concurrent `open` ×20 → one transition, all responses 200 with phase OPEN.
5. Auto-close moves phase at reg_closes_at (use a 3 s window in test).
6. Reset: previous run archived in drop_runs; seats all free; entries gone; new seed_commit differs; run_no incremented; mode switched when requested.
7. Public GET cached ≤ 1 s and reflects phase changes after cache expiry or explicit invalidation.

## 7. Verification / Definition of Done

Tests pass; a scripted admin sequence (create fifo → open → close → reset to fair → open → close) works via curl (`curl.exe` in Windows PowerShell; D-001) and is pasted (summarised) into the review log.

## 8. Plan-update obligations

- Plan 09 must implement the draw service signature this plan calls, and the grace-period freeze.
- Plan 11's leader ticker replaces the temporary auto-close task.
- Plans 17 and 19 depend on `drop_runs` and the reset-with-mode decision.
- Plan 18's scenario runner orchestrates create/open/close/draw/reset via these endpoints.

## 9. Review log must explain

- The phase diagram for both modes, in words a non-engineer can follow.
- What the seed commitment proves and what it does not (the operator-trust caveat).
- What reset keeps (users, sessions, run history) and what it wipes, and why.
- Any contract additions (reset mode, list drops) with the CONTRACT CHANGE banner.

## Close-out sequence (do all of these, in order)

1. **Definition of Done.** Run every check in the Verification section. Record the actual numbers (tests passed, latencies, oversold counts) — never write a number you did not observe.
2. **Refinement Pass (R3).** Re-read the full diff as a strict reviewer; remove dead code and debug output; check names against `docs/GLOSSARY.md`; walk each failure path (Postgres slow, Redis gone, request retried, two tabs at once); run the whole test suite, not just the new tests; ask "is there a simpler way?" and apply R2 if yes.
3. **Downstream sync (R2).** Open each plan listed in "Plan-update obligations" (and any other later plan you affected). Edit stale instructions, mark them `> Updated by D-00X`, and append to `PLAN_CHANGELOG.md`. If nothing changed, write "no downstream changes" in the review log.
4. **Review log (R4).** Write `docs/review-logs/06-drop-lifecycle.md` from the template, covering every item in this plan's "Review log must explain" list.
5. **Attribution check (R1).** List unpushed commits with their author and full message; confirm the author is the repo owner and no message contains AI attribution. Fix by rewording before pushing if needed.
6. **Commit** the plan's final state with a conventional message (e.g. `feat(alloc): atomic claim with skip-locked seats`) and the repo owner's identity.
7. **Chat summary.** 5–10 lines: what is done, key numbers, deviations, link to the review log, what Plan 07 starts with.
