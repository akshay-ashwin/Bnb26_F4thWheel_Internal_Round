# Plan 10 — Admission Tokens (L4) & the Fair-Mode Claim Path

| Field | Value |
|---|---|
| Design-doc sections | §3 option 4, §4 step 4, §4 attacker-lever table (token replay/sharing), §7 claim operation + session reliability (expired token), §8 L4, §11 admission token, §14 "token replayed", §15 token replay scenario |
| Original owner | Akshay (token) + Saanvi (L4 counters) |
| Depends on | Plans 07, 08, 09 |
| Unlocks | Fair end-to-end claims; Plans 11, 18 (replay scenarios) |
| Target time | 1.5–2 hours |

## 0. Mandatory operating protocol (read before writing anything)

1. Read `CLAUDE.md` (repo root) — it overrides this plan if they conflict.
2. Read `docs/implementation/PLAN_CHANGELOG.md`. If an earlier step edited this plan, the edited text is authoritative.
3. Read the review log of the previous plan in `docs/review-logs/` for handover notes.
4. Run the Pre-flight checks in section 3. Do not start building on a broken foundation.

Standing rules that apply to every line of work in this plan:

- **R1 — No AI attribution.** Never add Claude/Anthropic as author, co-author or contributor; no `Co-Authored-By` trailers, no "Generated with Claude Code" lines in commits or PRs; never touch git identity; never use `--no-verify`.
- **R2 — Better solution wins, and the future is rewritten.** If you find a better approach at any point, implement it (respecting the invariants in CLAUDE.md), write a Deviation Record in `docs/decisions/`, edit every affected later plan, and log it in `PLAN_CHANGELOG.md`.
- **R3 — Refine every step.** Do the Refinement Pass at the end of this plan before declaring it done, and refine the remaining plans if this step taught you something.
- **R4 — Review log.** Write `docs/review-logs/10-admission-tokens.md` in plain language from the template.

## 1. Goal

Only the session that holds an offer can turn it into a seat, a token can't be forged, shared across clients, or replayed to gain anything, and an expired token never costs a user their seat while the offer itself is still valid.

## 2. Scope

In scope: token format and keys, minting in `/me`, verification order, single-use semantics, reissue, rejection metrics and abuse events, Fair offer check in the claim transaction.
Out of scope: step-up flow (Plan 11).

## 3. Pre-flight checks

1. Plan 08's claim works in FIFO with the minimal token (or tokens were built then).
2. Draw issues OFFERED entries with offer_expires_at (Plan 09).

## 4. Token specification (write into `docs/contract/admission-token.md`)

- Format: compact JWS (JWT) with HS256 using `TOKEN_SIGNING_KEY`; header includes `kid` = `TOKEN_KEY_ID` to allow rotation (verifier keeps a small map of active keys).
- Claims: `drop_id`, `entry_id`, `sid_hash`, `jti` (128-bit random), `iat`, `exp`, plus `run` (drop run_no — prevents a token from a previous run being valid after reset; record this addition).
- `exp` rules: Fair → min(offer_expires_at, now + 60 s). FIFO → now + 60 s while phase OPEN. Never beyond offer expiry (design).
- Size target < 400 bytes.
- Library: a maintained JWT library (PyJWT) with algorithms pinned to HS256 only (reject `none` and anything else explicitly).

## 5. Minting (in `/me`, filling Plan 07's hook)

- Fair: entry status OFFERED and offer not expired → mint. STEP_UP_REQUIRED → no token until step-up passes. Other statuses → null.
- FIFO: entry REGISTERED and phase OPEN → mint.
- A fresh token (new jti) on every `/me` call is acceptable and simplest; tokens are cheap HMACs and the session binding + entry row lock + natural idempotency make multiple valid tokens for the same entry harmless. Alternative (cache one token per entry until near expiry in Redis) reduces jti churn — optional optimisation; record choice.
- Never cache the token inside `me:{entry_id}` (it is session-bound).

## 6. Verification order in claim (replace Plan 08's minimal checks)

1. Parse; algorithm/kid allowed; signature valid → else `401 TOKEN_INVALID` (reason=forged).
2. `exp` with 2 s leeway → else `401 TOKEN_INVALID` (reason=expired). The client's correct reaction is to call `/me` and get a fresh token; the UI does this automatically (Plan 16).
3. `drop_id` matches path and `run` matches the drop's current run_no → else 401 (reason=wrong_drop/stale_run).
4. `sid_hash` equals current session's sid_hash → else 401 (reason=session_mismatch). This is what defeats token sharing across clients and cross-session replay.
5. `jti` fast check: if Redis says used → do not reject outright; go to the claim transaction, which will find the entry ALLOCATED and return the existing allocation (design §14: same session after use → returns existing allocation). If the entry is somehow not allocated (shouldn't happen), reject 401 reason=replayed. If Redis is down, skip (PG constraints are the backstop).
6. Continue into the claim transaction with `entry_id` from the token (the request never supplies entry_id separately — the token is the only source).

Every 401 increments the `token_rejected` outcome counter and records an abuse event (layer L4, reason, session's user_id, sampled 100% — token rejections are rare for humans and valuable evidence).

## 7. Fair-mode offer check (inside the transaction, Plan 08 step 9)

With the entry row locked:
- status ALLOCATED → existing allocation (idempotent).
- status STEP_UP_REQUIRED → `423 STEP_UP_REQUIRED`.
- status OFFERED and offer_expires_at ≥ now() (DB clock) → proceed to take a seat.
- status OFFERED but expired → `409 OFFER_EXPIRED` and, in the same transaction, guarded-update the entry to OFFER_EXPIRED (so the sweeper and claim agree; design §14 "sweeper crashes" row) — invalidate cache after commit.
- any other status → `403 NOT_OFFERED`.
- Seat take returning nothing in Fair mode should be impossible (offers ≤ free seats by construction). If it happens: `409 SOLD_OUT`, log at ERROR, and the integrity panel will show why (this is an invariant breach indicator, not a normal path).

## 8. Reissue on expiry (design session-reliability row)

Offer expiry, not token expiry, is what loses a seat. Because `/me` mints a new token whenever the offer is still valid, a user whose token expired simply refreshes `/me`. Test this explicitly.

## 9. Tests (map 1:1 to the token replay scenario in design §15)

1. Valid token, right session → seat.
2. Same token, same session, second time → same allocation (200), not an error.
3. Token from session A presented by session B (another user) → 401 session_mismatch, and B's own entry is untouched.
4. Token from session A presented by another session of the SAME user (other device) → 401 (tokens bind to sessions, not users). Their own `/me` gives them a valid token. Record this behaviour in the contract.
5. Expired token while offer valid → 401 expired; `/me` → new token → claim succeeds.
6. Forged token (wrong key), `alg: none`, tampered claims → 401 forged.
7. Token from previous run after reset → 401 stale_run.
8. Expired offer → 409 OFFER_EXPIRED and entry status becomes OFFER_EXPIRED.
9. STEP_UP_REQUIRED entry → 423, no token issued in `/me`.
10. Fair storm: 500 offered users + 2,000 waitlisted users all hammer claim (waitlisted ones with stolen/forged tokens) → exactly the offered users get seats; integrity ok.

## 10. Verification / Definition of Done

Tests pass; first complete Fair drop end-to-end (create → open → enter → close → draw → claim) produces 500 allocations with 0 oversold; numbers recorded.

## 11. Plan-update obligations

- Plan 11: step-up success must make the entry OFFERED so `/me` starts minting.
- Plan 14: `token_rejected` outcome + abuse event schema.
- Plan 16: client auto-refreshes `/me` on `TOKEN_INVALID` once before showing an error.
- Plan 18: replay scenario cases must match tests 2–7 (with labelled expectations).

## 12. Review log must explain

- What an admission token is, what's inside it, and the five checks it must pass, in plain words.
- The "friend sends you their winning token" story and why it fails.
- Why a used token returns the existing seat instead of an error.
- Why offer expiry, not token expiry, is what matters.

## Close-out sequence (do all of these, in order)

1. **Definition of Done.** Run every check in the Verification section. Record the actual numbers (tests passed, latencies, oversold counts) — never write a number you did not observe.
2. **Refinement Pass (R3).** Re-read the full diff as a strict reviewer; remove dead code and debug output; check names against `docs/GLOSSARY.md`; walk each failure path (Postgres slow, Redis gone, request retried, two tabs at once); run the whole test suite, not just the new tests; ask "is there a simpler way?" and apply R2 if yes.
3. **Downstream sync (R2).** Open each plan listed in "Plan-update obligations" (and any other later plan you affected). Edit stale instructions, mark them `> Updated by D-00X`, and append to `PLAN_CHANGELOG.md`. If nothing changed, write "no downstream changes" in the review log.
4. **Review log (R4).** Write `docs/review-logs/10-admission-tokens.md` from the template, covering every item in this plan's "Review log must explain" list.
5. **Attribution check (R1).** List unpushed commits with their author and full message; confirm the author is the repo owner and no message contains AI attribution. Fix by rewording before pushing if needed.
6. **Commit** the plan's final state with a conventional message (e.g. `feat(alloc): atomic claim with skip-locked seats`) and the repo owner's identity.
7. **Chat summary.** 5–10 lines: what is done, key numbers, deviations, link to the review log, what Plan 11 starts with.
