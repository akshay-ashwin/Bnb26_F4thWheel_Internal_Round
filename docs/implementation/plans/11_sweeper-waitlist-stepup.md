# Plan 11 — Leader Jobs, Offer-Expiry Sweeper, Waitlist Promotion, Step-Up (L8) & Failure Resilience

| Field | Value |
|---|---|
| Design-doc sections | §4 step 4 + "Gate rather than block", §7 session reliability (expired offer), §8 L8, §11 `POST /step-up`, §13 state machine, §14 failure table (Redis down, Postgres down, restart, sweeper crash), §16 hour 14 deliverable |
| Original owner | Akshay (sweeper, step-up endpoint) + Saanvi (step-up wiring) |
| Depends on | Plans 09, 10 |
| Unlocks | Full Fair lifecycle to DONE; Plans 13 (flags have a consequence), 18–19 (chaos) |
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
- **R4 — Review log.** Write `docs/review-logs/11-sweeper-waitlist-stepup.md` in plain language from the template.

## 1. Goal

Offers that are not claimed in time go back to the pool and the next person on the waitlist gets one; flagged winners must re-verify with a fresh OTP before their seat confirms; background work runs exactly once even with 4 workers; and outages of Redis, Postgres or the API process cost time, never seats.

## 2. Scope

In scope: leader election, job scheduler, sweeper, promotion, DONE detection, step-up issue + verify, SIM-mode OTP exposure, heartbeat + outage extension, Redis-down fallbacks audit, cleanup jobs.
Out of scope: risk scoring (Plan 13) — this plan consumes `risk_score`.

## 3. Pre-flight checks

1. Draw produces OFFERED / STEP_UP_REQUIRED / WAITLISTED with expiries (Plan 09).
2. Claim rejects expired offers and marks them (Plan 10).
3. OTP module reusable (Plan 04).

## 4. Leader election and job runner

- With 4 uvicorn workers, every worker starts a lightweight job runner, but only the holder of a Postgres session-level advisory lock (`pg_try_advisory_lock` on a constant key, held on a dedicated connection outside the pool) executes jobs. If the leader process dies, its connection closes and the lock frees; another worker acquires it within one tick. Record the design and replace the temporary per-process tasks from Plans 06 and 08.
- Jobs and cadence: auto-close (1 s), sweeper (every 5 s, design value; make configurable — consider 2 s in the demo for snappier waitlist movement, record choice), remaining-counter reconciliation (5 s), heartbeat (1 s), idempotency/abuse-event cleanup (hourly).
- Each job run is wrapped with timing + error logging; a failing job never kills the runner.

## 5. Sweeper (one transaction per drop in CLAIMING)

1. Expire: guarded-update all entries with status in (OFFERED, STEP_UP_REQUIRED) and offer_expires_at < now() → OFFER_EXPIRED; collect ids.
2. Compute promotable slots = free seats − active offers (OFFERED + STEP_UP_REQUIRED with offer_expires_at ≥ now). Use SQL counts inside the transaction.
3. Promote: select that many WAITLISTED entries ordered by draw_rank FOR UPDATE SKIP LOCKED; each becomes OFFERED (or STEP_UP_REQUIRED if risk_score ≥ threshold and L8 enabled) with a new offer_expires_at = now + claim_window_s. Waitlist promotion must strictly follow rank order — SKIP LOCKED here is only to avoid blocking on a row locked by a concurrent claim attempt; since waitlisted rows aren't claimable, contention is nil; verify ordering in tests.
4. Update `drop:{id}:offer_head` to the highest rank offered so far (after commit).
5. DONE detection: if free seats = 0, or (no active offers and no WAITLISTED entries) → phase DONE; mark remaining WAITLISTED and REGISTERED entries NOT_SELECTED in batches.
6. After commit: invalidate `/me` cache for all changed entries; metrics (offers_expired, promoted).

Correctness note: the claim path itself rejects expired offers, so a dead sweeper only delays promotion; it never lets someone claim late (design §14). Test this.

## 6. Step-up (L8)

### 6.1 Issuing the challenge
When an entry becomes STEP_UP_REQUIRED (draw or promotion), a fresh OTP must be "sent" to the same phone. Store the step-up OTP in Redis keyed by entry (`stepup:{entry_id}`: otp_hash, attempts, created; TTL = remaining offer time). The catch: the server deliberately keeps only `phone_hash`, never the raw phone, so it has nowhere to send the SMS. Decision required — record it:
- (Recommended for the hackathon) The simulated SMS provider is keyed by `phone_hash` (it "knows" the destination in the simulation). In SIM_MODE, `/me` exposes `entry.dev_otp` for STEP_UP_REQUIRED entries so the simulator and the demo can complete it; the human UI shows "enter the code we just sent" (and in SIM_MODE a small dev hint).
- (Real deployment) Keep an encrypted phone (envelope encryption with a KMS key) solely for re-verification. Document as the production path; do not build it.

Generation is lazy-or-eager: generate when the entry enters STEP_UP_REQUIRED (sweeper/draw post-commit) and lazily in `/me` if missing (e.g., Redis was down).

### 6.2 `POST /drops/{id}/step-up {otp}`
1. Session + idempotency key required.
2. Lock the entry (user's entry for this drop). Status ALLOCATED or OFFERED with step_up_passed_at set → idempotent `200 {status: "OFFERED"}` (or the current status).
3. Status must be STEP_UP_REQUIRED and offer not expired → else `409 OFFER_EXPIRED` (or `403 NOT_OFFERED`).
4. Check OTP (constant time, attempts ≤ 3). Wrong → `401 OTP_INVALID`; after 3 wrong → step-up failed: entry → OFFER_EXPIRED immediately (seat returns to the pool; design "fail → waitlist" means the seat passes to the waitlist) — record interpretation.
5. Correct → guarded update to OFFERED, step_up_passed_at = now; offer_expires_at unchanged (they used part of their window — design: "a flagged human loses about 20 seconds").
6. Store idempotency record; commit; invalidate cache; metrics `step_ups.passed/failed`.

### 6.3 Simulator realism hook
Farmed bot identities in the simulator pass step-up only with a configured probability (models "farms can't receive fresh OTPs at scale"; Plan 18). Humans always can, after a think time. The backend has no knowledge of this — it just sees correct or incorrect OTPs.

## 7. Failure resilience (implement and test each row of design §14)

| Failure | What to build | Test |
|---|---|---|
| Redis down | Verify every Redis call goes through the circuit breaker with a fallback: rate limits → per-worker in-memory buckets (Plan 12), jti → skip (PG backstop), `/me` cache → PG, poll floor raised, idempotency for entries → unique index, metrics → dropped (counted in-process) | Stop Redis mid-claim-storm: claims continue, integrity ok |
| Postgres down | All DB paths → 503 + Retry-After; leader loses lock; heartbeat stops | Stop PG 10 s during CLAIMING; on return offers were extended (below) |
| Offer time lost to PG outage | Heartbeat writes `system_state['heartbeat']` every 1 s. On leader start or each sweep, if last heartbeat is older than 3 s (outage gap G), in one transaction extend offer_expires_at by G for all active offers BEFORE expiring anything, and log an abuse/ops event | Outage test above |
| API restart | Stateless workers; in-flight transactions roll back; clients retry with same key | Kill workers during storm; no duplicates, integrity ok |
| Draw crash | Already deterministic (Plan 09) | Covered |
| Sweeper crash | Claim checks expiry itself | Kill leader; late claims rejected; promotion resumes on new leader |

## 8. Tests

1. Unclaimed offers expire after claim_window_s; the same number of waitlisted entries get offers in rank order.
2. Repeated expiry cascades until DONE; final state: allocated + NOT_SELECTED + OFFER_EXPIRED = all eligible entries.
3. Step-up: correct OTP → OFFERED → claim succeeds; 3 wrong → OFFER_EXPIRED and a waitlisted entry gets promoted next sweep.
4. Leader election: 4 workers, only one sweeper log line per tick; kill leader → another takes over within 2 s.
5. All failure-table tests.
6. Idempotent step-up retry returns the same result.

## 9. Verification / Definition of Done

A Fair drop runs from OPEN to DONE unattended with some winners deliberately not claiming; the waitlist moves; flagged winners step up; integrity ok throughout; chaos tests pass. Record timings.

## 10. Plan-update obligations

- Plan 13 must provide the threshold + L8 toggle via app_settings (same keys used here).
- Plan 14: metrics fields `offers`, `step_ups{issued,passed,failed}`, offers_expired, promoted.
- Plan 16: step-up screen and waitlist screen behaviour.
- Plan 18: simulator step-up pass probability, non-claiming humans, chaos toggles.
- Plan 19: chaos scenarios list.

## 11. Review log must explain

- The waitlist in plain words ("if you don't confirm in 2 minutes, the next person in line gets your seat").
- Step-up: who sees it, why it's friction and not punishment, and the phone-storage decision.
- Leader election in one paragraph.
- The outage-extension trick and why it's fair to users.
- Results of each chaos test.

## Close-out sequence (do all of these, in order)

1. **Definition of Done.** Run every check in the Verification section. Record the actual numbers (tests passed, latencies, oversold counts) — never write a number you did not observe.
2. **Refinement Pass (R3).** Re-read the full diff as a strict reviewer; remove dead code and debug output; check names against `docs/GLOSSARY.md`; walk each failure path (Postgres slow, Redis gone, request retried, two tabs at once); run the whole test suite, not just the new tests; ask "is there a simpler way?" and apply R2 if yes.
3. **Downstream sync (R2).** Open each plan listed in "Plan-update obligations" (and any other later plan you affected). Edit stale instructions, mark them `> Updated by D-00X`, and append to `PLAN_CHANGELOG.md`. If nothing changed, write "no downstream changes" in the review log.
4. **Review log (R4).** Write `docs/review-logs/11-sweeper-waitlist-stepup.md` from the template, covering every item in this plan's "Review log must explain" list.
5. **Attribution check (R1).** List unpushed commits with their author and full message; confirm the author is the repo owner and no message contains AI attribution. Fix by rewording before pushing if needed.
6. **Commit** the plan's final state with a conventional message (e.g. `feat(alloc): atomic claim with skip-locked seats`) and the repo owner's identity.
7. **Chat summary.** 5–10 lines: what is done, key numbers, deviations, link to the review log, what Plan 12 starts with.
