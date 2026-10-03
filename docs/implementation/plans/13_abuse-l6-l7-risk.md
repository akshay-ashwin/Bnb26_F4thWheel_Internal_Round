# Plan 13 — Identity-Farming Defences: L6 OTP Abuse Controls & L7 Cluster / Velocity Risk Scoring

| Field | Value |
|---|---|
| Design-doc sections | §2 Identity farming row, §4 "Make farmed identities visible", §8 L6, L7, Risk scoring table, design rules, §12 Redis `cl:*`, §19 "What if you flag a legitimate user?", §20 attack vector 1 |
| Original owner | Saanvi (rules) + Akshay (call sites) |
| Depends on | Plans 04 (hooks), 07 (score_entry call site), 11 (step-up consumes scores), 12 (config + switches) |
| Unlocks | Identity-farming scenario (18), detection precision/recall + FPR (19), ground-truth-vs-detected panel (17) |
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
- **R4 — Review log.** Write `docs/review-logs/13-abuse-l6-l7-risk.md` in plain language from the template.

> Updated by D-001 (2026-10-04): stack rules apply to this plan. Everything runs through Docker Compose and `uv run fd <task>` (no `make`, no host Python or Node), files are LF, Node is 24 LTS (D-002), macOS and Windows are both supported, and fullstack-dev-skills may be used as advice but never its `project:*` workflow commands. CLAUDE.md always wins. See CLAUDE.md "Stack and platform rules" and `docs/decisions/D-001-cross-platform-docker-uv-node22-skills.md`. Read any `make X` below as `uv run fd X`.

## 1. Goal

Make identity farms expensive to create (L6) and visible once created (L7), with transparent additive rules whose only consequence is step-up friction for flagged winners — never blocking, never down-weighting — and keep the false-positive rate on simulated humans under 2%.

## 2. Scope

In scope: L6 throttles in OTP request/verify, L7 cluster windows, timing-regularity signal, scoring at entry, re-scoring at close, explainable flags, tuning procedure, dashboard counts.
Out of scope: ASN lookup with a real database (optional; see 5.5).

## 3. Pre-flight checks

1. Hooks `otp_request_guard`, `on_identity_verified`, `score_entry` exist and are no-ops (Plans 04, 07).
2. app_settings abuse config has `rules` and `thresholds` sections (Plan 12).
3. Draw and sweeper read `risk_score` against `thresholds.step_up_score` (Plans 09, 11).

## 4. L6 — OTP abuse controls (in `otp_request_guard`; throw `429 OTP_THROTTLED` with retry_after)

| Control | Key | Default limit | Rationale |
|---|---|---|---|
| Per phone | phone_hash | 3 requests / 10 min (beyond the 30 s dedupe) | SMS cost, brute force |
| Per device | device_id | 3 distinct phones / 10 min | One device requesting many numbers |
| Per IP | ip | 10 OTP requests / min | Bulk farms on one host |
| Per /24 | ip24 | 100 / min (campus-safe) | Subnet farms |
| Per phone prefix | phone_prefix | > 20 distinct numbers in 60 s → throttle that prefix for 60 s | Sequential-number farms |
| Verify attempts | request_id | 5 (already in Plan 04) | Brute force |

Distinct-phone counting uses Redis sorted sets or HyperLogLog with TTL windows. All L6 controls obey the L6 switch. Counters go to metrics (`otp_throttled`).

## 5. L7 — cluster windows and signals

### 5.1 Cluster sets (Redis sorted sets, member = user_id, score = ms timestamp)
- `cl:{drop}:device:{device_id}` — drop lifetime (users seen on this device for this drop's entries; also include verify events from the last 24 h so farms that verify ahead of time are counted — record decision).
- `cl:{drop}:ip24:{net}` — window 60 s for "new users" (users whose account was created in the last 24 h — define "new" and record).
- `cl:{drop}:ua:{ua_hash}` — window 60 s.
- Optional `cl:{drop}:asn:{asn}` (5.5).
Operations per entry: add member, trim by score for windowed sets, read cardinality — in ONE pipeline or Lua call to stay within the 2 ms budget.

### 5.2 Rules (from design; each individually switchable, points configurable)

| Rule id | Signal | Window | Points |
|---|---|---|---|
| R_DEVICE | same device_id across > 3 users | drop lifetime | +40 |
| R_SUBNET | same /24 across > 20 new users | 60 s | +25 |
| R_TIMING | inter-request timing variance near zero for the session | per session | +20 |
| R_FAST_OTP | OTP verified < 2 s after request | per user | +15 |
| R_UA | same UA hash on > 50 users | 60 s | +10 |

Score = sum, capped at 100. Threshold `>= 60` → STEP_UP_REQUIRED if the entry wins (Plans 09, 11).

### 5.3 R_TIMING details
Per session, keep the last ~10 request inter-arrival gaps (ms) in a capped Redis list (pushed by the L1–L3 middleware at negligible cost, or by a sampled hook — choose and record). Signal fires when ≥ 6 samples exist and the coefficient of variation < 0.05 (machine-regular). Humans polling at server-chosen intervals with ±10% jitter (Plan 07) have CV ≈ 0.06+ — this interacts with the poll jitter! Verify the threshold against human sim traffic and adjust; record the final value and the reasoning. Requests during the burst can be few; missing data = no points.

### 5.4 R_FAST_OTP
`on_identity_verified` stores `fast_otp:{user_id}` = 1 (TTL 24 h) when verify latency < 2,000 ms. Note: simulated humans must have realistic OTP typing time (Plan 18), otherwise FPR is meaningless.

### 5.5 ASN (optional)
Real ASN needs a lookup database (e.g., an offline IP-to-ASN dataset). In the simulator world IPs are synthetic. Option: in SIM_MODE derive a synthetic ASN from the first octet range of `X-Sim-Client-IP`. If not implemented, say so in the review log and in Q&A prep; do not fake a capability.

### 5.6 Flags format
`risk_flags` jsonb = list of `{rule, points, evidence}` where evidence is a small object (e.g. `{"device_users": 7}`) — explainable to a judge and visible in `/export`. Never contains raw IPs beyond the /24 or raw device ids beyond a short hash prefix.

## 6. Scoring at entry AND re-scoring at close (improvement over design — record as decision)

Problem: a farm's first three accounts on a device enter before the device crosses the "> 3 users" threshold, so they get 0 points at entry time; only later entries are flagged. Fix: at freeze time (Plan 09, before ranking), re-score every eligible entry from the final cluster set cardinalities in one batch pass (Redis pipeline reads, then one bulk UPDATE of risk_score/risk_flags). Entry-time scores still feed the live dashboard ("backend flagged N clusters" rising during the attack). The draw uses the final scores. Plan 09 step 5.2 already calls the `rescore_eligible(drop_id)` hook; implement it here. Note: re-scoring changes only `risk_score`/`risk_flags`, never eligibility or rank.

## 7. Never block, never down-weight (enforce in code + tests)

- L7 must not change entry status, must not alter rank, and must not exclude from the draw.
- A test asserts the draw output (ranks) is identical with L7 on vs off; only statuses OFFERED vs STEP_UP_REQUIRED differ for top-ranked flagged entries.

## 8. Tuning procedure (do now with the normal scenario as soon as Plan 18 has it; leave the procedure here)

1. Run the Normal scenario (2,000 humans, realistic devices/IPs incl. shared campus /24s) with L7 on.
2. FPR = humans with risk_score ≥ threshold / all humans. Target < 2%.
3. If above target, raise per-rule thresholds (not the global threshold first), re-run, record each iteration in `docs/decisions/` as a short tuning log.
4. Run Identity Farming scenario; record recall on farmed identities (design expects ~60% clustered flagged; whatever you get, report honestly).

## 9. Tests

1. Each rule fires exactly at its boundary (e.g. 4th user on a device gets +40; 3rd does not at entry time but does after re-score).
2. Score capping and threshold behaviour.
3. Switch each rule/layer off → no points from it.
4. L6 limits trigger and expire; campus /24 at human pace not throttled.
5. Draw ranks identical with L7 on/off (invariant 5).
6. Scoring call stays < 2 ms p99 under 1,000 concurrent entries (record).
7. Redis down → entries proceed with `risk_unavailable` flag, score 0.

## 10. Verification / Definition of Done

Tests pass; flags visible in `/export`; FPR and recall numbers recorded (or explicitly marked "pending Plan 18 scenarios" and re-checked in Plan 19).

## 11. Plan-update obligations

- Plan 09: confirm the `rescore_eligible` hook is called before ranking and its runtime at 52k entries is acceptable (record).
- Plan 14: metrics `flagged_entries`, `flagged_clusters`, `otp_throttled`.
- Plan 17: ground-truth-vs-detected panel shows flagged clusters and flagged entries.
- Plan 18: humans need realistic OTP typing delay, jittered polling, campus NAT clusters; bots need configurable device/IP/UA reuse.
- Plan 19: FPR and recall on the scorecard.

## 12. Review log must explain

- What a "farm" looks like in data and each rule in one sentence.
- Why a flag only means "quick extra check if you win" and never "you lose".
- The re-score-at-close improvement and the case it fixes.
- Measured FPR and recall (or that they're pending), and the honest limit: rented real SIMs pass L6–L8; what's left is proportional share.

## Close-out sequence (do all of these, in order)

1. **Definition of Done.** Run every check in the Verification section. Record the actual numbers (tests passed, latencies, oversold counts) — never write a number you did not observe.
2. **Refinement Pass (R3).** Re-read the full diff as a strict reviewer; remove dead code and debug output; check names against `docs/GLOSSARY.md`; walk each failure path (Postgres slow, Redis gone, request retried, two tabs at once); run the whole test suite, not just the new tests; ask "is there a simpler way?" and apply R2 if yes.
3. **Downstream sync (R2).** Open each plan listed in "Plan-update obligations" (and any other later plan you affected). Edit stale instructions, mark them `> Updated by D-00X`, and append to `PLAN_CHANGELOG.md`. If nothing changed, write "no downstream changes" in the review log.
4. **Review log (R4).** Write `docs/review-logs/13-abuse-l6-l7-risk.md` from the template, covering every item in this plan's "Review log must explain" list.
5. **Attribution check (R1).** List unpushed commits with their author and full message; confirm the author is the repo owner and no message contains AI attribution. Fix by rewording before pushing if needed.
6. **Commit** the plan's final state with a conventional message (e.g. `feat(alloc): atomic claim with skip-locked seats`) and the repo owner's identity.
7. **Chat summary.** 5–10 lines: what is done, key numbers, deviations, link to the review log, what Plan 14 starts with.
