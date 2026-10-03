# Plan 12 — Abuse Layers L1–L5: Redis Lua Token Buckets, Shedding Middleware, Layer Switches

| Field | Value |
|---|---|
| Design-doc sections | §2 Scalability row, §8 Layers table L1–L5 + design rules, §12 Redis `rl:*`, §14 Redis down, §16 middleware signature, §20 attack vector 3 (polling flood) |
| Original owner | Saanvi (limiter) + Akshay (integration) |
| Depends on | Plans 03, 04 (session parsing), 07 (poll_after_ms) |
| Unlocks | Bot-flood scenario (18), traffic chart outcomes (14, 17), before/after per-layer demo |
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
- **R4 — Review log.** Write `docs/review-logs/12-abuse-l1-l5.md` in plain language from the template.

> Updated by D-001 (2026-10-04): stack rules apply to this plan. Everything runs through Docker Compose and `uv run fd <task>` (no `make`, no host Python or Node), files are LF, Node is 22 LTS, macOS and Windows are both supported, and fullstack-dev-skills may be used as advice but never its `project:*` workflow commands. CLAUDE.md always wins. See CLAUDE.md "Stack and platform rules" and `docs/decisions/D-001-cross-platform-docker-uv-node22-skills.md`. Read any `make X` below as `uv run fd X`.

## 1. Goal

Floods are rejected in under 1 ms of CPU each without touching Postgres, humans at normal pace are never rate-limited, every layer can be switched on/off live from the admin panel, and if Redis disappears the limiter degrades to per-worker buckets instead of failing open or closed.

## 2. Scope

In scope: decision interface, Lua bucket script (multi-bucket atomic), scopes and default limits, middleware, cooldowns, 429 responses, per-worker fallback, abuse config store + live reload, outcome counters, abuse event sampling, the L4/L5 switch semantics.
Out of scope: L6–L7 (Plan 13), L8 (Plan 11), metrics endpoint (Plan 14).

## 3. Pre-flight checks

1. Middleware slot exists before routing (Plan 03).
2. Signed session token can be verified without I/O (Plan 04).
3. Client IP helper exists (Plan 04).

## 4. Interfaces

- `check(request) → Decision` where Decision = allow, or reject with `{layer, code, retry_after_ms, scope, key}`. This is the frozen hour-2 interface; keep it.
- Abuse config document (persisted in `app_settings['abuse_config']`, cached in Redis and in-process with 1 s refresh): `layers {L1..L8: bool}`, `limits {scope → {rate_per_s, burst}}` per endpoint group, `thresholds {step_up_score: 60, cooldown_violations: 20, cooldown_s: 30, …}`, `rules {L7 rule id → enabled, points}`. `PUT /admin/abuse/config` validates, persists to PG, bumps a config version in Redis; workers reload within 1 s. `GET` variant (addition, record it) returns the current config for the dashboard.

## 5. Token bucket in Redis Lua (describe and implement; no code in this plan)

- Bucket state per key `rl:{scope}:{id}`: tokens (float) and last-refill timestamp (ms), stored as a hash.
- Time source: Redis `TIME` inside the script (consistent across workers; no client clock skew).
- Algorithm: refill = elapsed × rate, capped at burst; if tokens ≥ cost → consume and allow; else deny with retry_after = (cost − tokens) / rate.
- **Multi-bucket atomicity:** one script call evaluates ALL applicable buckets for the request (global endpoint bucket, IP, /24, session, user) and only consumes from all of them if all allow. One network round trip per request. Record why: separate calls double latency and can consume from early buckets when a later one denies.
- Key TTL: 2× the time to refill from empty (design), set on every write.
- Load the script once per worker (`SCRIPT LOAD` + `EVALSHA`, reload on NOSCRIPT).

## 6. Scopes and default limits (starting values — tune in Plan 19; humans must never hit them)

| Layer | Scope | Applies to | Start rate / burst | Key source |
|---|---|---|---|---|
| L1 | global per endpoint group | entries, claim, me, otp, drops-read, step-up | sized to measured capacity, e.g. entries 3,000/s, claim 2,000/s, me 15,000/s, otp 500/s, drops-read 20,000/s | constant |
| L2 | per IP | all public | 20/s, burst 40 | client IP |
| L2 | per /24 | all public | 300/s, burst 600 (shared campus Wi-Fi must pass — 2,000 students behind one NAT is realistic; test it) | ip24 |
| L3 | per session | entries, claim, step-up | 3/s, burst 6 | session id from signed token (no I/O) |
| L3 | per session | me | ~2× the server's poll pace, e.g. 2/s burst 5 | session id |
| L3 | per user | all authenticated | 6/s, burst 12 (covers a user with a few tabs/devices) | user id — requires session lookup; use the Redis session cache from Plan 04; skip if unavailable |

L1 sizes must be derived from a load measurement, not guessed: in Plan 19, measure max sustainable RPS per endpoint at P99 < 1 s, set L1 at ~80% of it. Until then use the starting values above and record them.

## 7. Middleware behaviour

1. Skip for `/api/healthz`, `/api/readyz`, admin routes (authenticated by key), and `/api/sim/telemetry`.
2. Determine endpoint group from the route path (cheap prefix match; no routing work).
3. Extract IP/ip24, and session id by verifying the signed token from cookie/bearer (HMAC only). Invalid/no session → session-scoped buckets skipped (auth will 401 later).
4. Check cooldown key `cd:ip:{ip}` — present → reject immediately (no Lua call).
5. Run the multi-bucket script for enabled layers only.
6. Reject → `429 RATE_LIMITED` envelope with `retry_after_ms` and `Retry-After` header; increment violation counter `viol:ip:{ip}` (TTL 60 s); exceeding `cooldown_violations` sets `cd:ip:{ip}` for `cooldown_s`. If the rejected request was `/me` at L3, set `slow:{session}` (TTL 30 s) which Plan 07's poll policy reads to triple `poll_after_ms`.
7. Record outcome counters (Plan 14 hook): `rate_limited` with layer + scope labels.
8. Budget: whole middleware < 1 ms CPU on reject paths. Measure with a micro-benchmark and record.

## 8. Redis-down fallback

Circuit breaker open → per-worker in-memory token buckets with the same algorithm, limits divided by worker count, keyed only by IP, ip24 and session (bounded LRU map, e.g. 100k keys). Never fail closed (blocking everyone) and never fail fully open. Record decision.

## 9. L4 and L5 switches (semantics — be honest in the review log)

- L4 (token validation) switch: when off, the claim path still requires a token to know the entry_id but skips the session-binding and jti checks — this exists ONLY to demonstrate in the before/after that sharing/replay would otherwise work. Signature and expiry stay on. Record clearly.
- L5 (duplicate collapse) switch: turning it off disables only the idempotency replay cache; the database unique constraints cannot and must not be turned off (invariant 2). The dashboard label should say "idempotency cache" for this toggle so nobody thinks integrity is switchable.
- L1–L3 switches: fully on/off.
- L6–L8 switches: consumed by Plans 13 and 11.

## 10. Abuse events

- Rate-limit rejections: count all in Redis; sample 1% into `abuse_events` (rate-limited by an in-process sampler so a flood can't flood Postgres).
- Identity-related events (L4, L6, L7 flags, L8 outcomes): log 100%, but batch-insert every 500 ms from an in-process queue (bounded; drop + count if full).

## 11. Tests

1. Normal human pace (one `/me` per poll_after_ms, one entry, one claim) → zero 429s over a 2,000-user simulated run.
2. 10,000 requests/s from one IP → > 99% rejected, Postgres sees none of them (assert via pg_stat_statements or query counters).
3. 2,000 users behind one /24 at human pace → zero 429s (campus NAT test).
4. Multi-bucket atomicity: when the session bucket denies, the IP bucket is not consumed.
5. Cooldown triggers after repeated violations and expires.
6. Toggle L2 off live → IP flood now passes L2 (and hits L3/L1 instead) within 1 s.
7. Redis killed → fallback buckets engage; no 500s.
8. Retry-After header and retry_after_ms agree.
9. Micro-benchmark: reject path median and p99 CPU time recorded.

## 12. Verification / Definition of Done

Tests pass; the bot-flood scenario (one identity, thousands of req/s) produces a wall of 429s while that identity still holds exactly one entry; numbers recorded.

## 13. Plan-update obligations

- Plan 07: poll policy reads `slow:{session}`.
- Plan 14: outcome counters labels (layer, scope).
- Plan 17: abuse toggles UI wording (L5 = "idempotency cache").
- Plan 19: L1 sizing procedure from load measurements.

## 14. Review log must explain

- Each layer in one plain sentence ("L2 stops one computer or one building from hogging the door").
- Why rejected requests never reach Postgres, with the measured cost per rejection.
- Why campus Wi-Fi users won't be punished (the /24 limit test).
- Exactly what each toggle does and doesn't disable — especially that integrity is never switchable.

## Close-out sequence (do all of these, in order)

1. **Definition of Done.** Run every check in the Verification section. Record the actual numbers (tests passed, latencies, oversold counts) — never write a number you did not observe.
2. **Refinement Pass (R3).** Re-read the full diff as a strict reviewer; remove dead code and debug output; check names against `docs/GLOSSARY.md`; walk each failure path (Postgres slow, Redis gone, request retried, two tabs at once); run the whole test suite, not just the new tests; ask "is there a simpler way?" and apply R2 if yes.
3. **Downstream sync (R2).** Open each plan listed in "Plan-update obligations" (and any other later plan you affected). Edit stale instructions, mark them `> Updated by D-00X`, and append to `PLAN_CHANGELOG.md`. If nothing changed, write "no downstream changes" in the review log.
4. **Review log (R4).** Write `docs/review-logs/12-abuse-l1-l5.md` from the template, covering every item in this plan's "Review log must explain" list.
5. **Attribution check (R1).** List unpushed commits with their author and full message; confirm the author is the repo owner and no message contains AI attribution. Fix by rewording before pushing if needed.
6. **Commit** the plan's final state with a conventional message (e.g. `feat(alloc): atomic claim with skip-locked seats`) and the repo owner's identity.
7. **Chat summary.** 5–10 lines: what is done, key numbers, deviations, link to the review log, what Plan 13 starts with.
