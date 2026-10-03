# Plan 09 — Fair Mode: Window Freeze, Commit-Reveal Provable Draw, Offers & Waitlist

| Field | Value |
|---|---|
| Design-doc sections | §3 options table (why option 6), §4 steps 2–3, §4 "Bound the damage", §11 `/draw-proof`, §12 entries.draw_rank, §14 "Draw job crashes midway", §17 Build first #2, §19 "Couldn't you rig the draw?", §20 operator-trust weakness |
| Original owner | Akshay |
| Depends on | Plans 06, 07, 08 |
| Unlocks | Plans 10, 11, 17 (draw-verify button), 19 (fairness evaluation) |
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
- **R4 — Review log.** Write `docs/review-logs/09-fair-draw.md` in plain language from the template.

## 1. Goal

When registration closes, freeze the entry set without races, publish its hash, reveal the seed, rank every eligible entry by a keyed hash anyone can recompute, give ranks 1..capacity an offer and the rest an ordered waitlist — all in one deterministic, re-runnable transaction.

## 2. Scope

In scope: close grace period, eligibility, entry_set_hash, byte-exact ranking algorithm, bulk rank write, offer issuing (with step-up holds for flagged entries), waitlist, offer_head, draw-proof endpoint (admin + public), determinism tests, drand design (stretch).
Out of scope: claiming offers (Plan 10), expiry/promotion (Plan 11), risk scores themselves (Plan 13 — the draw only reads them).

## 3. Pre-flight checks

1. Entries enforce the window inside the insert on the DB clock (Plan 07).
2. Phase machine calls a draw service (Plan 06).
3. `public_id` format is fixed and recorded in the GLOSSARY (Plan 04).

## 4. Byte-exact definitions (write into `docs/contract/draw.md` FIRST; the browser verifier in Plan 17 implements the same text)

- `seed`: 32 random bytes; transported as 64 lowercase hex chars.
- `seed_commit = hex(SHA-256(seed_bytes))`.
- Eligible entries: entries of this drop and run with status REGISTERED at freeze time (DISQUALIFIED excluded — nothing sets it yet; it exists for manual admin action).
- `entry_set_hash = hex(SHA-256(UTF-8( join("\n", sorted(eligible user_public_ids)) )))` — sorted by plain byte order, no trailing newline.
- Rank key per entry: `hex(HMAC-SHA256(key = seed_bytes, message = UTF-8(drop_id_lowercase_canonical_uuid + "|" + user_public_id)))`. The design writes `drop_id ‖ user_public_id`; the explicit `|` separator removes any ambiguity — record this as a refinement.
- Ranking: sort eligible entries by rank key ascending (hex string compare = byte compare); tie-break by user_public_id (practically never used). Rank 1 = first.
- Algorithm string returned by draw-proof: `HMAC_SHA256(seed, drop_id|user_public_id) asc; ties by user_public_id`.

Why this is fair (put in review log): every eligible identity has exactly one key; the key is a pseudorandom function of a seed fixed before entry; arrival time and request count are not inputs. So P(rank ≤ 500) = 500/N for everyone.

## 5. Implementation steps

### 5.1 Freeze with a grace period (race-free close)
- Close sets `reg_closes_at` (if closing early) and phase CLOSED.
- In-flight insert transactions that started before `reg_closes_at` may still commit. The insert's own condition used `now()` (transaction start time) < reg_closes_at, and `statement_timeout` bounds how long they can run. Therefore the draw must not read the entry set until `reg_closes_at + grace`, where grace > the entries statement timeout (recommend grace = 3 s with a 2 s statement timeout). The draw action called earlier than that waits (or returns 409 "DRAW_NOT_READY, retry in X ms" — choose; recommended: server waits up to the remaining grace, then proceeds).
- After grace: compute and store `entry_set_hash`; publish it (GET /drops shows it).

### 5.2 Draw transaction (one transaction, admin-triggered)
1. Lock the drop row FOR UPDATE; require phase CLOSED (if already DRAWN/CLAIMING → return existing proof: idempotent; if a previous attempt crashed, the phase is still CLOSED and we simply run again — deterministic, identical result).
2. Call the risk re-score hook `rescore_eligible(drop_id)` (Plan 13 §6 — re-computes every eligible entry's risk_score from final cluster sizes in one batch; a no-op until Plan 13 exists). Run it inside this transaction before reading scores.
3. Read eligible entries (id, user_public_id, risk_score) with a server-side cursor or one fetch (52k rows is fine in memory).
4. Recompute entry_set_hash and assert it equals the stored one (guards against late writes); mismatch → abort with a loud error.
5. Compute rank keys in Python (52k HMACs ≈ tens of ms), sort, assign ranks 1..N.
6. Bulk write ranks: COPY (entry_id, rank, new_status, offer_expires_at) into a temporary table, then one UPDATE … FROM join — never 52k individual updates.
   - rank ≤ capacity and risk_score < step-up threshold → OFFERED, offer_expires_at = now + claim_window_s, offered_at = now.
   - rank ≤ capacity and risk_score ≥ threshold (and L8 enabled) → STEP_UP_REQUIRED, offer_expires_at = now + claim_window_s (the seat is held while they re-verify; design §4 "Gate rather than block").
   - rank > capacity → WAITLISTED.
   The threshold and L8 toggle come from app_settings (Plan 12/13); default threshold 60.
7. Update drop: seed revealed (it was stored; now exposed), drawn_at, phase DRAWN then CLAIMING in the same transaction (record: DRAWN is instantaneous in practice; keep both phase values for the story strip — the dashboard can show DRAWN via drawn_at).
8. Commit.
9. After commit: set Redis `drop:{id}:offer_head = min(capacity, N)`; bump the per-drop `/me` cache version (`drop:{id}:me_ver`, defined in Plan 07) so every entry's cached status is invalidated in O(1); invalidate public drop cache; metrics event "draw done".

### 5.3 Draw input independence (explicit guard)
The draw query selects only id, user_public_id, risk_score. Add a unit test that the draw service module never references `entered_at`, request counts, IP, or device fields (static check via simple source inspection is fine), and a property test: shuffling insertion order and entered_at values yields identical ranks.

### 5.4 `GET /admin/drops/{id}/draw-proof` and public proof
- Admin endpoint per contract: `{seed_commit, seed, entry_set_hash, algorithm}`.
- Public verifiability needs the list of eligible public ids. Add (record + CONTRACT CHANGE) `GET /drops/{id}/draw-proof` (public, only after DRAWN) returning the same fields plus `eligible_public_ids` sorted, and `ranked_public_ids` (top capacity + waitlist order) — optionally as NDJSON or gzip for size (52k × ~30 bytes ≈ 1.5 MB raw; gzip it). Public ids are random and reveal nothing personal. Cache aggressively (immutable after draw).

### 5.5 Stretch: public randomness (drand)
Design only, implement if time permits: at drop creation, commit to a future drand round number R (first round after planned close). At draw, fetch round R's randomness and use `final_seed = SHA-256(seed_bytes || drand_randomness_bytes)` for ranking. Proof adds R and the round signature. This removes the operator's ability to pre-grind seeds. If not implemented, Plan 19's Q&A pack states it as the deployment step.

## 6. Tests

1. Determinism: run the draw twice on a cloned dataset → identical ranks; crash after step 5 (raise) → phase still CLOSED → rerun produces the same ranks as an uncrashed run.
2. Independence: permute entered_at and insertion order → identical ranks.
3. Statistical sanity: 10,000 simulated draws over a synthetic set where group A has 3.8% of entries → mean share ≈ 3.8%, empirical 95% interval matches the hypergeometric band (11–27 of 500 at 2,000/52,000). Record.
4. Recompute ranks from the public proof in an independent test script (no backend imports) → match. This is the reference the browser verifier will be checked against.
5. Flagged entries in top capacity get STEP_UP_REQUIRED with an expiry; with L8 disabled they get OFFERED.
6. Draw in FIFO → 409. Draw while OPEN → 409. Draw twice → same proof, no changes.
7. Performance: draw over 52,000 entries completes in < 3 s (record actual).

## 7. Verification / Definition of Done

Tests pass; `docs/contract/draw.md` written; an end-to-end Fair run via curl: create → open → enter ×N → close → draw → `/me` shows OFFERED for some and WAITLISTED for others → proof recomputed by the independent script matches.

## 8. Plan-update obligations

- Plan 10: Fair claim checks OFFERED + not expired.
- Plan 11: sweeper uses `offer_head` and the same step-up threshold.
- Plan 17: browser verifier implements `docs/contract/draw.md` exactly; uses the public proof endpoint.
- Plan 19: evaluator re-verifies the draw independently and reports it on the scorecard.

## 9. Review log must explain

- The four-phase Fair drop in plain words, and the exact recipe anyone can follow to check the draw.
- Why closing has a grace period (with the "request in flight at the bell" example).
- Why the draw can crash safely.
- What the commitment does and doesn't prove (operator trust) and the drand upgrade path.
- Measured draw time and the statistical sanity numbers.

## Close-out sequence (do all of these, in order)

1. **Definition of Done.** Run every check in the Verification section. Record the actual numbers (tests passed, latencies, oversold counts) — never write a number you did not observe.
2. **Refinement Pass (R3).** Re-read the full diff as a strict reviewer; remove dead code and debug output; check names against `docs/GLOSSARY.md`; walk each failure path (Postgres slow, Redis gone, request retried, two tabs at once); run the whole test suite, not just the new tests; ask "is there a simpler way?" and apply R2 if yes.
3. **Downstream sync (R2).** Open each plan listed in "Plan-update obligations" (and any other later plan you affected). Edit stale instructions, mark them `> Updated by D-00X`, and append to `PLAN_CHANGELOG.md`. If nothing changed, write "no downstream changes" in the review log.
4. **Review log (R4).** Write `docs/review-logs/09-fair-draw.md` from the template, covering every item in this plan's "Review log must explain" list.
5. **Attribution check (R1).** List unpushed commits with their author and full message; confirm the author is the repo owner and no message contains AI attribution. Fix by rewording before pushing if needed.
6. **Commit** the plan's final state with a conventional message (e.g. `feat(alloc): atomic claim with skip-locked seats`) and the repo owner's identity.
7. **Chat summary.** 5–10 lines: what is done, key numbers, deviations, link to the review log, what Plan 10 starts with.
