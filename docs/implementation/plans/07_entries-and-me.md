# Plan 07 — Registration (Entries) and the `/me` Status Endpoint

| Field | Value |
|---|---|
| Design-doc sections | §4 step 2 Register, §4 "If one attacker controls 10,000 clients" (volume collapses), §7 Session reliability, §10 user view, §11 `POST /entries`, `GET /me`, §12 Redis `me:{entry_id}` |
| Original owner | Akshay |
| Depends on | Plans 04, 05, 06 |
| Unlocks | Plans 08–11 (they change entry status), 13 (risk hook at entry), 16 (UI), 18 (simulator flow) |
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
- **R4 — Review log.** Write `docs/review-logs/07-entries-and-me.md` in plain language from the template.

> Updated by D-001 (2026-10-04): stack rules apply to this plan. Everything runs through Docker Compose and `uv run fd <task>` (no `make`, no host Python or Node), files are LF, Node is 22 LTS, macOS and Windows are both supported, and fullstack-dev-skills may be used as advice but never its `project:*` workflow commands. CLAUDE.md always wins. See CLAUDE.md "Stack and platform rules" and `docs/decisions/D-001-cross-platform-docker-uv-node22-skills.md`. Read any `make X` below as `uv run fd X`.

## 1. Goal

One verified identity gets exactly one entry per drop no matter how many requests, tabs or retries it fires, and arrival time inside the window is irrelevant. `/me` becomes the UI's single source of truth: it can rebuild any screen after refresh or reconnect and it tells clients how fast to poll.

## 2. Scope

In scope: entries endpoint (both modes), window enforcement on the DB clock, risk hook call site, duplicate counting, `/me` assembly, caching, invalidation, server-paced polling, waitlist position.
Out of scope: admission token minting (Plan 10 plugs into `/me`), offers/draw (Plan 09), claim (Plan 08).

## 3. Pre-flight checks

1. Phase transitions work (Plan 06). Session dependency works (Plan 04). Registration idempotency backend exists (Plan 05).

## 4. `POST /drops/{id}/entries`

### 4.1 Flow
1. Session required (401 otherwise).
2. Optional idempotency key → Redis lookup (Plan 05); hit → replay.
3. Load drop (cached public data is fine for phase pre-check; the authoritative check is in the insert).
4. Phase pre-check: Fair needs OPEN and DB-clock `now() < reg_closes_at`; FIFO needs OPEN. SCHEDULED → `403 WINDOW_NOT_OPEN`; past window → `403 WINDOW_CLOSED`.
5. Risk scoring hook (Plan 13): `score_entry(drop_id, user_id, device_id, ip24, ua_hash, session_timing)` returns `(risk_score, risk_flags)`. Now: returns (0, []). It must run BEFORE the insert so the score is stored atomically with the entry, and it must be Redis-only and fast (< 2 ms budget). If Redis is down it returns (0, ["risk_unavailable"]) — never blocks entry.
6. Insert with the window enforced IN the statement: insert … select from drops where id = $drop and phase = 'OPEN' and (mode = 'fifo' or now() < reg_closes_at) … on conflict (drop_id, user_id) do nothing returning the row. This closes the race between the pre-check and the close (no row returned + drop no longer open → WINDOW_CLOSED). Record the reasoning.
7. If a row was inserted → `201 {entry_id, status: "REGISTERED"}`; record metrics `accepted`, entry count `+1` (Redis `drop:{id}:entries` counter, display only).
8. If conflict → fetch existing entry → `200` with the same body shape (status may already be later than REGISTERED — return the current status; record decision); increment `duplicate` metric (this is L5 "duplicate collapse" in action).
9. Store Redis idempotency record if a key was provided.
10. Set `client_ip`, `device_id`, `run_no` on the entry.

Arrival timestamp `entered_at` is recorded for evidence (the Spearman metric) but is NEVER read by Fair-mode decision code. Add a code comment + a test (Plan 09) proving the draw doesn't use it.

### 4.2 Performance target
At 50k registrations in ~30 s the insert path must stay well under 50 ms p95. Use a single statement, no extra round trips, the unique index does the dedupe. Measure with a quick concurrent test (1,000 users) and record.

## 5. `GET /drops/{id}/me`

### 5.1 Response assembly
- `phase` (effective phase from drop, including the lazy auto-close logic).
- `entry`: null if none; else entry_id, status, `rank` (draw_rank, only after draw), `waitlist_pos` (only when WAITLISTED), `offer_expires_at` (only when OFFERED/STEP_UP_REQUIRED), `step_up_required` (status == STEP_UP_REQUIRED), `admission_token` (Plan 10 — leave a hook that returns null now), `dev_otp` (SIM_MODE only, Plan 11).
- `allocation`: null or `{allocation_id, seat_no, confirmed_at}` (confirmed_at = allocation created_at).
- `poll_after_ms` (5.3).

### 5.2 Caching (design: `me:{entry_id}` 2 s TTL)
- Cache only the session-independent part (entry + allocation) under `me:{entry_id}`. Never cache the admission token (it is session-bound and minted per request — Plan 10). Record this refinement over the design wording.
- Cache key includes a per-drop cache version: `me:{drop}:v{n}:{entry_id}` where `n` is read from `drop:{id}:me_ver` (cached in-process for 250 ms). Bulk events (draw, reset) bump the version — one O(1) operation invalidates every entry of the drop instead of deleting 52,000 keys. Single-entry changes still delete their own key.
- Cache invalidation: every code path that changes an entry's status or creates its allocation must delete `me:{entry_id}` after commit. Create one helper `invalidate_entry_status(entry_ids)` now and require all later plans to use it (list them in Plan-update obligations).
- Lookup path: session → user_id → entry_id via Redis `entry_of:{drop}:{user}` (set at entry creation, TTL drop lifetime) → `me:{entry_id}` → else Postgres single indexed query joining allocation. If Redis is down: straight to Postgres, and raise `poll_after_ms` floor (design §14).

### 5.3 Server-paced polling (`poll_after_ms`)
A small policy function, documented as a table in `docs/contract/polling.md`:

| Situation | Base poll_after_ms |
|---|---|
| SCHEDULED | 5000 |
| OPEN, no entry | 3000 |
| OPEN, REGISTERED | 4000 (nothing will change until close) |
| CLOSED (draw pending) | 1500 |
| OFFERED / STEP_UP_REQUIRED | 1000 |
| WAITLISTED | 2000 |
| ALLOCATED / NOT_SELECTED / OFFER_EXPIRED / DONE | 15000 (terminal; UI can stop polling) |
| FIFO OPEN | 1000 |

Multipliers: × load factor from metrics (Plan 14 provides current RPS vs capacity; until then 1.0), × 2 if Redis is down, × 3 if the session was rate-limited at L3 recently (Plan 12 sets a flag). Add ±10% jitter so 50k clients don't synchronize. Clamp 500–30000.

### 5.4 Waitlist position
Decision (recommended): `waitlist_pos = my draw_rank − (highest draw_rank that has ever been offered)`, maintained as Redis `drop:{id}:offer_head` updated by the draw and the sweeper (Plans 09, 11), with a Postgres fallback query (count of WAITLISTED entries with lower rank, which is fine at low frequency). This is O(1) per poll. Record it.

## 6. Tests

1. Sequential double entry → 201 then 200, same entry_id.
2. 10,000 concurrent POSTs from ONE session (scaled down to 500 in CI) → exactly one entry; duplicates counted.
3. 1,000 distinct users concurrently → 1,000 entries; record p95.
4. Entry exactly as the window closes: run 200 concurrent entries straddling close; every accepted entry has `entered_at < reg_closes_at`; every rejected one got WINDOW_CLOSED; none slipped in after.
5. SCHEDULED → 403 WINDOW_NOT_OPEN.
6. `/me` with no entry → entry null; after entry → REGISTERED; refresh repeatedly → identical payload (minus server_time).
7. Cache invalidation: changing status via a test helper that uses the invalidation helper is visible on the next `/me`.
8. Redis down → both endpoints still work (slower), poll_after_ms floor raised.
9. poll_after_ms values follow the table and are jittered within ±10%.

## 7. Verification / Definition of Done

Tests pass; numbers recorded; `docs/contract/polling.md` written.

## 8. Plan-update obligations

- Plans 08, 09, 10, 11 must call `invalidate_entry_status` after every status change.
- Plan 10 fills the admission token hook; Plan 11 fills `dev_otp` and offer_head updates; Plan 13 fills `score_entry`.
- Plan 16/18 clients must honour `poll_after_ms` (humans) — bots may ignore it (that's the attack).

## 9. Review log must explain

- The "10,000 POSTs collapse into one entry" story and which line of defence does it (the unique index).
- How the window close is enforced without a race.
- Why `/me` exists (single source of truth) and how caching stays correct.
- How the server slows polling under load, and why that protects humans without affecting fairness.

## Close-out sequence (do all of these, in order)

1. **Definition of Done.** Run every check in the Verification section. Record the actual numbers (tests passed, latencies, oversold counts) — never write a number you did not observe.
2. **Refinement Pass (R3).** Re-read the full diff as a strict reviewer; remove dead code and debug output; check names against `docs/GLOSSARY.md`; walk each failure path (Postgres slow, Redis gone, request retried, two tabs at once); run the whole test suite, not just the new tests; ask "is there a simpler way?" and apply R2 if yes.
3. **Downstream sync (R2).** Open each plan listed in "Plan-update obligations" (and any other later plan you affected). Edit stale instructions, mark them `> Updated by D-00X`, and append to `PLAN_CHANGELOG.md`. If nothing changed, write "no downstream changes" in the review log.
4. **Review log (R4).** Write `docs/review-logs/07-entries-and-me.md` from the template, covering every item in this plan's "Review log must explain" list.
5. **Attribution check (R1).** List unpushed commits with their author and full message; confirm the author is the repo owner and no message contains AI attribution. Fix by rewording before pushing if needed.
6. **Commit** the plan's final state with a conventional message (e.g. `feat(alloc): atomic claim with skip-locked seats`) and the repo owner's identity.
7. **Chat summary.** 5–10 lines: what is done, key numbers, deviations, link to the review log, what Plan 08 starts with.
