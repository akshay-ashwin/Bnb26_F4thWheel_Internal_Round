# Plan 08 — Allocation Engine: Atomic Claim, FIFO Mode End-to-End & Live Integrity

> **[CUT] by D-004 (2026-10-04, LEAN MODE):** do not build: remaining-counter reconciliation job, integrity in-process cache, kill-mid-transaction test. Also skip anything in "stretch", "optional" or "if time permits" text.

| Field | Value |
|---|---|
| Design-doc sections | §7 Allocation integrity, Critical claim operation, Idempotency table, Session reliability, §11 `POST /claim` + FIFO note, `/admin/.../integrity`, §14 failure rows, §17 "Build FIFO end to end first" |
| Original owner | Akshay |
| Depends on | Plans 05, 06, 07 (and a minimal token from Plan 10 — see 4.1) |
| Unlocks | The first demo-able system (FIFO "before"), Plan 09 Fair path, Plan 18 first scenarios |
| Target time | 3 hours |

## 0. Mandatory operating protocol (read before writing anything)

1. Read `CLAUDE.md` (repo root) — it overrides this plan if they conflict.
2. Read `docs/implementation/PLAN_CHANGELOG.md`. If an earlier step edited this plan, the edited text is authoritative.
3. Read the review log of the previous plan in `docs/review-logs/` for handover notes.
4. Run the Pre-flight checks in section 3. Do not start building on a broken foundation.

Standing rules that apply to every line of work in this plan:

- **R1 — No AI attribution.** Never add Claude/Anthropic as author, co-author or contributor; no `Co-Authored-By` trailers, no "Generated with Claude Code" lines in commits or PRs; never touch git identity; never use `--no-verify`.
- **R2 — Better solution wins, and the future is rewritten.** If you find a better approach at any point, implement it (respecting the invariants in CLAUDE.md), write a Deviation Record in `docs/decisions/`, edit every affected later plan, and log it in `PLAN_CHANGELOG.md`.
- **R3 — Refine every step.** Do the Refinement Pass at the end of this plan before declaring it done, and refine the remaining plans if this step taught you something.
- **R4 — Review log.** Write `docs/review-logs/08-allocation-engine-fifo.md` in plain language from the template.

> Updated by D-001 (2026-10-04): stack rules apply to this plan. Everything runs through Docker Compose and `uv run fd <task>` (no `make`, no host Python or Node), files are LF, Node is 24 LTS (D-002), macOS and Windows are both supported, and fullstack-dev-skills may be used as advice but never its `project:*` workflow commands. CLAUDE.md always wins. See CLAUDE.md "Stack and platform rules" and `docs/decisions/D-001-cross-platform-docker-uv-node22-skills.md`. Read any `make X` below as `uv run fd X`.

## 1. Goal

Implement the one transaction that turns a free seat into a sold seat, make it correct under any concurrency (last-seat race, multi-tab, retries, lost responses), make it fast with `FOR UPDATE SKIP LOCKED`, run FIFO mode end-to-end, and expose `/integrity` computed by SQL. Gate: 0 oversold under 1,000 concurrent claims.

## 2. Scope

In scope: claim service + endpoint, FIFO semantics, sold-out → DONE, post-commit side effects, remaining-counter reconciliation, integrity endpoint, concurrency tests.
Out of scope: Fair-mode offer logic (Plan 10 adds it to the same function), step-up (Plan 11).

## 3. Pre-flight checks

1. Idempotency service supports in-transaction store and semantic hashing (Plan 05).
2. `invalidate_entry_status` helper exists (Plan 07).
3. FIFO drop can be created and opened (Plan 06).

## 4. Implementation

### 4.1 The FIFO admission-token gap (resolve first, record as decision)
The contract requires `{admission_token}` in every claim, but the design defines tokens only for Fair offers. Decision (recommended): in FIFO mode, `/me` mints an admission token for any REGISTERED entry while phase is OPEN (short `exp`, e.g. 60 s), so the claim request shape is identical in both modes and the simulator/UI need no second code path (design §11 goal). Implement a minimal version of the token mint/verify now (HMAC-signed claims: drop_id, entry_id, sid_hash, jti, iat, exp) or build Plan 10's token module first if you prefer; if you reorder, apply R2 and record it.

### 4.2 Claim sequence (prose specification — implement exactly this order)

Outside the transaction (cheap checks):
1. Session required.
2. Idempotency key required; parse.
3. Verify token signature and `exp`; check token.drop_id = path drop; token.sid_hash = current session's sid_hash. Failure → `401 TOKEN_INVALID` (detail reason in logs, not in the response).
4. Compute semantic request hash `(claim, drop_id, token.entry_id)`; idempotency lookup → replay or 422 or continue.
5. Load drop (mode, phase) — cached is fine for routing; authoritative checks happen inside.

Inside one transaction (READ COMMITTED; `SET LOCAL lock_timeout = '1s'`, `SET LOCAL statement_timeout = '2s'`):
6. Lock the entry row: select by id AND user_id = session user, FOR UPDATE. Not found → `403 NOT_OFFERED` (the entry isn't theirs or doesn't exist).
7. If status = ALLOCATED → fetch existing allocation → idempotent success (same payload built from the allocation row).
8. If status = STEP_UP_REQUIRED → `423 STEP_UP_REQUIRED` (Fair only; harmless in FIFO).
9. Mode checks: FIFO → drop phase must be OPEN and entry status REGISTERED, else SOLD_OUT if DONE / NOT_OFFERED otherwise. Fair → Plan 10 inserts the offer check here.
10. Take a seat: one statement that updates the first free seat of the drop selected with ORDER BY seat_no, FOR UPDATE SKIP LOCKED, LIMIT 1, setting status sold, entry_id, sold_at; the outer update must ALSO re-check `status = 'free'` in its WHERE (defence in depth under READ COMMITTED re-evaluation). No row → `409 SOLD_OUT` (transaction rolls back; nothing written).

> Updated by D-004 (2026-10-04) after Plan 02: the free-seat index is `(drop_id, seat_no) WHERE status = 'free'`, so the lowest-free-seat subquery is O(1) however many seats are sold (measured: 133 buffers against 19,899). Inside the claim transaction the order is forced by the foreign keys: update the seat row first, then insert the ledger row (`allocations_seat_entry_fk` needs the seat to already hold the entry). Set `allocations.run_no` (NOT NULL) and `sold_at` together with `status = 'sold'` (a CHECK ties them).
11. Guarded entry update to ALLOCATED (`WHERE id = $1 AND status = <expected>`); if 0 rows → raise internal error and roll back (should be impossible because we hold the row lock; assert it).
12. Insert allocation (drop_id, entry_id, seat_id, idempotency_key, run_no).
13. Store the idempotency record (same connection).
14. Commit.

After commit only (each best-effort; failures logged, never affect the response):
15. Mark jti used (`SET NX` with TTL = token lifetime) — Plan 10 uses it.
16. Decrement `drop:{id}:remaining` (display only).
17. `invalidate_entry_status([entry_id])`.
18. Metrics: claim accepted, latency.
19. FIFO: if remaining reached 0 → trigger the OPEN → DONE transition (guarded; idempotent) and bulk-mark REGISTERED entries NOT_SELECTED in a separate transaction (batching 5,000 rows per statement to keep locks short). Confirm with a SQL count of free seats, not the Redis counter.

### 4.3 Lock ordering and deadlock analysis
Always lock entry → then seat. No code path locks a seat first and then an entry. Write this rule in the service module docstring and the review log. Different users never contend on the same entry row; on seats they skip each other.

### 4.4 Errors under load
- `lock_timeout` hit or pool exhausted → `503 SERVICE_UNAVAILABLE` with small `Retry-After` (client retries with the same key — safe).
- Never convert a database error into SOLD_OUT.

### 4.5 Remaining counter reconciliation
A periodic task (leader-only; temporary per-process task with advisory lock until Plan 11) every 5 s compares `drop:{id}:remaining` with the SQL free count and corrects it. Display only — never used for decisions.

### 4.6 `GET /admin/drops/{id}/integrity`
Reads `v_drop_integrity` for the drop: `{seats_total, sold, free, oversold, duplicate_entries_with_seats, invariant_ok}` plus the extra cross-table fields from Plan 02 under an `extra` object (contract addition — record it; harmless for clients). Must be computed by SQL every call (design: "not counters"). Cache at most 250 ms in-process to protect PG during dashboard polling.

> Updated by D-004 (2026-10-04) after Plan 02: query it as `SELECT * FROM v_drop_integrity WHERE drop_id = $1`. The `extra` object carries `allocations_count`, `sold_without_allocation`, `allocation_without_sold_seat`, `entries_allocated_count`, `entries_allocated_mismatch`, `sold_seat_entry_not_allocated` and `free_seat_with_sold_at` (all of them must be 0 except the two counts, which must equal `sold`). Measured at 52,000 entries the view runs in about 1-2 ms server time, so the 250 ms in-process cache below is a safety margin, not a necessity.

## 5. Concurrency test suite (the heart of this plan)

Run against real Postgres with 4 workers where possible (spin uvicorn in a subprocess for the heaviest tests):

1. **Last-seat race:** capacity 1, 50 users claim simultaneously → exactly 1 success, 49 SOLD_OUT, integrity ok.
2. **Storm:** capacity 500, 1,000 distinct users claim concurrently → exactly 500 sold, 500 SOLD_OUT, oversold 0, duplicates 0. Record p50/p95/p99 claim latency.
3. **Multi-tab:** 300 users × 5 concurrent claims each (different idempotency keys, same session) → each user holds ≤ 1 seat; all 5 responses for a winning user carry the same seat_no.
4. **Same key ×3** (design table rows #1–#3) → one write, identical payloads.
5. **New key after success** (row #4) → same allocation.
6. **Lost response:** commit then drop the HTTP response (test hook) → retry returns the stored success; `/me` shows ALLOCATED.
7. **Cross-session token:** token minted for session A used by session B (same user, different device) → 401.
8. **Kill mid-transaction:** terminate the backend connection between seat update and commit (test hook / pg_terminate_backend) → no partial state; retry succeeds.
9. **FIFO sold-out transition:** after the 500th claim, phase becomes DONE and leftover REGISTERED entries become NOT_SELECTED.
10. **Integrity endpoint** stays `invariant_ok: true` throughout test 2 when polled every 100 ms in parallel.

## 6. Verification / Definition of Done

All tests pass; the gate "0 oversold under 1,000 concurrent claims" is met and its latency numbers are recorded; a browser-free FIFO run works end to end via curl (`curl.exe` in Windows PowerShell; D-001): create → open → verify ×N → enter → me → claim → integrity.

## 7. Plan-update obligations

- Plan 10: add the Fair offer check at step 9 and harden token verification (single-use semantics, reissue).
- Plan 11: step-up status handling and sweeper rely on the guarded-update pattern here.
- Plan 14: record claim latency + outcomes via the metrics hook used in step 18.
- Plan 18: simulator FIFO bot strategy = enter, poll `/me` for token, claim as fast as possible.
- If you reordered Plan 10's token module earlier, update Plan 10 to "harden" rather than "create".

## 8. Review log must explain

- The claim transaction step by step in plain words, with the A-vs-B last-seat story and the two-tabs story.
- Why SKIP LOCKED is both correct and fast, compared with a single counter row.
- The FIFO token decision.
- The measured numbers from tests 2 and 3, and the integrity endpoint result.
- Explicitly: FIFO is consistent (0 oversold) but unfair — integrity and fairness are separate properties.

## Close-out sequence (do all of these, in order)

1. **Definition of Done.** Run every check in the Verification section. Record the actual numbers (tests passed, latencies, oversold counts) — never write a number you did not observe.
2. **Refinement Pass (R3).** Re-read the full diff as a strict reviewer; remove dead code and debug output; check names against `docs/GLOSSARY.md`; walk each failure path (Postgres slow, Redis gone, request retried, two tabs at once); run the whole test suite, not just the new tests; ask "is there a simpler way?" and apply R2 if yes.
3. **Downstream sync (R2).** Open each plan listed in "Plan-update obligations" (and any other later plan you affected). Edit stale instructions, mark them `> Updated by D-00X`, and append to `PLAN_CHANGELOG.md`. If nothing changed, write "no downstream changes" in the review log.
4. **Review log (R4).** Write `docs/review-logs/08-allocation-engine-fifo.md` from the template, covering every item in this plan's "Review log must explain" list.
5. **Attribution check (R1).** List unpushed commits with their author and full message; confirm the author is the repo owner and no message contains AI attribution. Fix by rewording before pushing if needed.
6. **Commit** the plan's final state with a conventional message (e.g. `feat(alloc): atomic claim with skip-locked seats`) and the repo owner's identity.
7. **Chat summary.** 5–10 lines: what is done, key numbers, deviations, link to the review log, what Plan 09 starts with.
