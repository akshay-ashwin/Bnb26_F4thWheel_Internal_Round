# Plan 05 — Idempotency Framework (Keys, Request Hashing, Stored Responses)

| Field | Value |
|---|---|
| Design-doc sections | §7 Idempotency: three identical POST /claim, §7 What lives where (idempotency records), §8 L5, §14 timeouts / lost responses |
| Original owner | Akshay |
| Depends on | Plans 03, 04 |
| Unlocks | Plans 07 (entries), 08 (claim), 11 (step-up), 16 (client retry), 18 (simulator retries) |
| Target time | 1.5 hours |

## 0. Mandatory operating protocol (read before writing anything)

1. Read `CLAUDE.md` (repo root) — it overrides this plan if they conflict.
2. Read `docs/implementation/PLAN_CHANGELOG.md`. If an earlier step edited this plan, the edited text is authoritative.
3. Read the review log of the previous plan in `docs/review-logs/` for handover notes.
4. Run the Pre-flight checks in section 3. Do not start building on a broken foundation.

Standing rules that apply to every line of work in this plan:

- **R1 — No AI attribution.** Never add Claude/Anthropic as author, co-author or contributor; no `Co-Authored-By` trailers, no "Generated with Claude Code" lines in commits or PRs; never touch git identity; never use `--no-verify`.
- **R2 — Better solution wins, and the future is rewritten.** If you find a better approach at any point, implement it (respecting the invariants in CLAUDE.md), write a Deviation Record in `docs/decisions/`, edit every affected later plan, and log it in `PLAN_CHANGELOG.md`.
- **R3 — Refine every step.** Do the Refinement Pass at the end of this plan before declaring it done, and refine the remaining plans if this step taught you something.
- **R4 — Review log.** Write `docs/review-logs/05-idempotency.md` in plain language from the template.

## 1. Goal

Retries, double-taps, lost responses and multi-tab duplicates must return the same answer and never cause a second write. The real guarantee is natural idempotency (database constraints); the key layer makes retries cheap and responses identical. Build it as a reusable component so entries, claim and step-up all use the same rules.

## 2. Scope

In scope: header parsing, request hashing, two storage backends (Postgres for claim/step-up, Redis for registration), the lookup-before-work and store-inside-transaction protocol, conflict handling, cleanup.
Out of scope: the business logic of each endpoint.

## 3. Pre-flight checks

1. `idempotency_records` table exists with `drop_id` column (Plan 02).
2. Session dependency works (Plan 04).

## 4. Design (write this into `docs/contract/idempotency.md` too)

### 4.1 Key rules

- Header `Idempotency-Key`, must parse as a UUID; otherwise `400 VALIDATION_ERROR`.
- Required on: `POST /claim`, `POST /step-up`. Missing → `400 IDEMPOTENCY_KEY_MISSING`.
- Optional on: `POST /entries` (contract says key optional; natural idempotency via `UNIQUE(drop,user)` covers it).
- Admin POSTs: not required (admin actions are idempotent by state machine guards).
- Keys are scoped per user: the same key from two different users are unrelated (PK is (user_id, key)).
- The client generates one key per user ACTION (e.g. "claim drop X"), persists it in sessionStorage until a terminal outcome, and reuses it on every retry of that action (Plan 15).

### 4.2 Request hash

`request_hash = SHA-256(method + "\n" + route template with resolved drop_id + "\n" + canonical JSON of body)`. Canonical JSON = sorted keys, no insignificant whitespace, UTF-8. Do NOT include headers. Special case for claim: the claim body contains the admission token, and `/me` mints a fresh token on each call (Plan 10), so a legitimate retry after re-fetching `/me` would carry a DIFFERENT token string and wrongly trigger `422 IDEMPOTENCY_KEY_REUSED` if the raw body were hashed. Decision (recommended, record it): for claim, the hash covers the *meaning* of the request — `(endpoint, drop_id, entry_id from the verified token)` — not the raw token string. Implement this by letting each endpoint supply a "semantic body" to hash, computed after token verification. Generic default = canonical body.

### 4.3 Which responses get stored

Store only successful outcomes (2xx). Errors are recomputed on retry because the world may have changed (e.g. a 429 or 503 must not be replayed forever; a `409 SOLD_OUT` in FIFO is final anyway and recomputes to the same answer). Record this decision.

### 4.4 Identical responses

"Identical" means identical payload except `server_time`, which always reflects the current response time. Document this in the contract so the frontend/simulator compare payloads without `server_time`. Responses for an existing allocation must be built deterministically from the allocation row (confirmed_at = allocations.created_at, never `now()`), so path #2 and path #4 in the design table yield the same bytes as path #1.

### 4.5 Storage backends

| Endpoint | Store | Written when | TTL / cleanup |
|---|---|---|---|
| claim, step-up | Postgres `idempotency_records` | INSIDE the same transaction as the business write (so a committed seat always has its record, and a rolled-back attempt never has one) | Deleted on drop reset; cleanup job removes records older than 24 h |
| entries | Redis `idem:reg:{user_id}:{key}` (JSON response + request_hash) | After the insert commits | 10 min TTL; if Redis is down, fall back to the unique index (re-selecting the existing entry gives the same body) |

### 4.6 Protocol (for PG-backed endpoints)

1. Before any lock: look up (user_id, key).
   - Hit + same hash → return stored status + body (with fresh server_time). Count as `duplicate` metric.
   - Hit + different hash → `422 IDEMPOTENCY_KEY_REUSED`.
   - Miss → continue.
2. Business transaction runs (Plan 08 etc.). At its end, insert the record with `ON CONFLICT (user_id, key) DO NOTHING`.
3. If two requests with the same key race: both miss in step 1; the second blocks on the entry row lock inside the business transaction, then observes the committed state (e.g. ALLOCATED) and returns the existing result; its record insert is a no-op. Both responses identical by 4.4.
4. Expose this as a small service with three operations — lookup, store-in-transaction, and "replay response builder" — used by each endpoint. No decorator magic that hides the transaction boundary (reviewers must see where the record is written).

## 5. Implementation steps

1. Header extraction dependency returning an optional/required key per endpoint.
2. Canonicalization + hashing utility, with the semantic-body hook.
3. PG repository: lookup by (user_id, key); insert within a provided transaction connection; delete by drop; cleanup older-than.
4. Redis repository for registration with graceful degradation through the circuit breaker (Plan 03).
5. Metrics hook: increment `duplicate` counter on every replay or natural-idempotency collapse (Plan 14 wires the counter).
6. Admin reset (Plan 06) will call delete-by-drop — expose it now.
7. Background cleanup task registered but scheduled by the leader in Plan 11 (leave a note).

## 6. Tests (use a fake endpoint in tests if claim isn't built yet; re-run against real claim in Plan 08)

1. Same key, same body, sequential → second returns stored response, no second write.
2. Same key, different semantic body → 422.
3. Same key, concurrent ×10 → exactly one write, ten identical payloads.
4. New key, same action after success → natural idempotency returns the same payload (tested fully in Plan 08).
5. A failed business transaction leaves no idempotency record.
6. Redis down → registration retry still returns the same entry via the unique index.
7. Missing key on claim → 400 IDEMPOTENCY_KEY_MISSING; malformed key → 400.

## 7. Verification / Definition of Done

Tests pass; `docs/contract/idempotency.md` written; the semantic-hash decision for claim is documented and reflected in the contract.

## 8. Plan-update obligations

- Plan 08 and 10: claim must compute the semantic hash after token verification and write the record inside its transaction.
- Plan 15/16: client must reuse the same key across `/me` refreshes even though the token string changes.
- Plan 18: simulator's retry logic must reuse keys; its replay attacks reuse keys on purpose.

## 9. Review log must explain

- The difference between "natural idempotency" (constraints) and "key idempotency" (stored responses), with the three-identical-claims story.
- Why the claim hash ignores the raw token string, with the concrete failure it prevents.
- Why only successes are stored.
- Where exactly the record is written (inside the transaction) and why that matters for lost responses.

## Close-out sequence (do all of these, in order)

1. **Definition of Done.** Run every check in the Verification section. Record the actual numbers (tests passed, latencies, oversold counts) — never write a number you did not observe.
2. **Refinement Pass (R3).** Re-read the full diff as a strict reviewer; remove dead code and debug output; check names against `docs/GLOSSARY.md`; walk each failure path (Postgres slow, Redis gone, request retried, two tabs at once); run the whole test suite, not just the new tests; ask "is there a simpler way?" and apply R2 if yes.
3. **Downstream sync (R2).** Open each plan listed in "Plan-update obligations" (and any other later plan you affected). Edit stale instructions, mark them `> Updated by D-00X`, and append to `PLAN_CHANGELOG.md`. If nothing changed, write "no downstream changes" in the review log.
4. **Review log (R4).** Write `docs/review-logs/05-idempotency.md` from the template, covering every item in this plan's "Review log must explain" list.
5. **Attribution check (R1).** List unpushed commits with their author and full message; confirm the author is the repo owner and no message contains AI attribution. Fix by rewording before pushing if needed.
6. **Commit** the plan's final state with a conventional message (e.g. `feat(alloc): atomic claim with skip-locked seats`) and the repo owner's identity.
7. **Chat summary.** 5–10 lines: what is done, key numbers, deviations, link to the review log, what Plan 06 starts with.
