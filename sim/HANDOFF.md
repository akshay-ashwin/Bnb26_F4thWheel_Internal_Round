# Handoff — Saanvi lane (abuse protection, simulator, fairness evidence)

Branch `saanvi-attacks`. Every number below was measured on this machine (Windows 11, Docker
Desktop, 12 CPUs) against the **dev stub** (`api/app/abuse/devstub`), a one-worker, in-memory
implementation of the frozen contract with the real L1–L3 limiter, L6 guard and L7 scoring in
front. It is NOT Akshay's backend; his real endpoints (Plans 03–11) do not exist yet. Re-run the
same commands with `--target http://api:8000` once they do.

## 1. Status

| Item | Status |
|---|---|
| S1 Redis Lua L1–L3 limiter | Done. Atomic multi-bucket (check all, spend only if all pass, Redis TIME), reserved signed-in L1 pools, anonymous-only IP cooldown, `slow:{session}`, bounded per-worker fallback, live config (Postgres hook + Redis version + 1 s refresh), 429 + `Retry-After` + `retry_after_ms`. |
| S2 simulator | Done. Humans: sign-in/OTP typing/repeated entry/refresh/second tab/poll/step-up/claim/Retry-After/Wi-Fi→mobile. Bots: flood, burst, duplicate, replay, farm, anonymous junk. Ground truth local only. |
| S3 genuine_retry | Done, FIFO + Fair, 4 seeds. Fair passes every criterion we can measure on the stub (section 3). |
| S4 evaluator | Done: advantage ratio, exact hypergeometric band, Spearman, genuine success/429/step-up, bot rejection, requests/seat, integrity, latency/errors, detection precision/recall/FPR. |
| S5 L6/L7 | Done. Campus test passes; farm recall 60.9% (target "most"). See the L6/L7 interaction finding. |
| S6 network switch | Pass with a 60 s window (200/200, 0 flagged). In the 25 s-window run 4/200 missed the window under flood load; the switch never broke a session (section 5). |
| S7 evidence | Sweep, 3 fixed-seed finals, claim stampede, 50k logical-client crowd: done. The 50k run shows the stub's capacity ceiling, not the real backend's. |

## 2. What changed (files)

- `api/app/abuse/` (my lane): `buckets.lua`, `limiter.py`, `middleware.py`, `config.py`, `router.py`, `risk.py`, `__init__.py`, `devstub/` (dev-only, never imported by `app.main`).
- `api/tests/abuse/`: limiter (12), risk (10), dev stub flows (4), isolation (2) = 28 new tests.
- `sim/`: `src/sim/{model,api,runner,evaluator,stampede,cli}.py`, `src/sim/clients/{human,bot}.py`, `scenarios/*.toml` (13), `scripts/evidence.py`, tests (8), `README.md`, this file, `evidence/` (summary outputs), `pyproject.toml`/`uv.lock` (aiohttp replaces httpx).
- Nothing outside `api/app/abuse`, `api/tests/abuse` and `sim/`. No contract change.

## 3. Primary question: can a genuine user still enter and claim while bots attack?

`genuine_retry`: 200 genuine users (4–5 entry attempts, a refresh, a second tab, polling, claim)
+ one attacker with 10,000 clients on one identity + a 100-identity farm. Final runs, 3 fixed
seeds (101/202/303), same seed in both modes:

| Metric (per seed) | FIFO | Fair |
|---|---|---|
| genuine users entered (of 200) | 186 / 183 / 173 | **200 / 200 / 200** |
| genuine requests answered 429 | 0 / 0 / 0 | **0 / 0 / 0** |
| genuine winners who confirmed | n/a (19 seats each) | **40/40, 42/42, 47/47** |
| bot request rejection | 98.0–98.3% | 99.0–99.1% |
| attacker entry share → seat share | ~14.5% → 62% | 13.4% → 20% / 16% / 6% |
| advantage ratio A | 9.79 / 9.63 / 9.11 | 1.61 / 1.23 / 0.41 |
| attacker seats vs 95% chance band | 31 vs 3–12 (outside, every seed) | 10, 8, 3 vs 3–11 (inside, every seed) |
| arrival-vs-win Spearman | −0.68 to −0.70 | 0.06 / 0.05 / 0.17 |
| oversold / duplicate seats | 0 / 0 | 0 / 0 |
| genuine p95 latency | 15–422 ms | 1.35–1.40 s |

Flood identity: exactly 1 entry in every run. L6 let the farm create only 31 of 101 accounts
(3 per device). An earlier seed-42 run had 12 attacker seats vs band 3–11 (P = 1.6%); across the
4 seeds the mean is 8.25 vs an expected 6.7, consistent with chance.

**Verdict:** in Fair mode, under a 10,000-client flood, every genuine user entered exactly once,
none saw a 429, and every genuine winner confirmed a seat, in all 3 seeds. Cost: genuine p95
rose to ~1.4 s because the single stub worker was absorbing ~1,500 req/s of mostly rejected
traffic.

## 4. All scenarios (final code, one run each; `evidence/scorecard.md` has every metric)

| Scenario | FIFO: genuine entered / attacker seat share / A / ρ | Fair: genuine entered / attacker seat share / A / ρ |
|---|---|---|
| normal (300 h) | 163/300 · – · – · −0.65 | 300/300 · – · – · 0.08 |
| bot_flood (10k clients, 1 id) | 194/300 · 2% · 3.96 · −0.61 | 300/300 · 0% · 0 · −0.02 |
| flash_crowd (2,000 h, 80% in 5 s) | 935/2000 · – · – · −0.33 | 2000/2000 · – · – · 0.00 |
| repeated_attempts (200 ids × 50) | 77/300 · 96% · 9.24 · −0.32 | 300/300 · 36% (entry 40%) · 0.84 · 0.03 |
| multi_tab (2 tabs claim) | 187/300 · – · – · −0.65 | 300/300 · – · – · 0.01 |
| token_replay | 150/200 · 0 replay/forged wins | 200/200 · 0 replay/forged wins |
| identity_farm (5,000 ids) | **0/300** · 100% | 300/300 · 83% of seats won (entry 91.5%) · 0.46 |
| campus (30 on one IP) | 160/200 | 200/200, 0 flagged |
| network_switch (2k-client flood) | 160/200 | 196/200, 0 flagged (see section 5) |

Genuine 429s: 0 in every run above (checked on the raw per-label counters). Oversold and
duplicate seats: 0 in every run.

## 5. Farming and network results

- **Campus** (30 genuine users on one public IP, plus 170 elsewhere): 0 flagged. Unit test with
  30 on one IP: 0 flagged; OTP guard: 0 throttled.
- **Identity farm** (5,000 identities, 20 devices, 10 /24s, 40% clean proxies): L1/L2 shed 6,410
  farm sign-in requests, so 3,249 identities got accounts. 1,980 entries flagged: precision 100%,
  farm recall 60.9%, human FPR 0/300. Flagged winners failed step-up; 26 of 50 seats went unused
  because the stub has no waitlist promotion.
- **No single signal flags**: max rule 40 < 60 (test). Signals count distinct users per device,
  /24 or synthetic ASN, UA; a user's own network change adds nothing (test).
- **L6/L7 interaction (finding)**: L6 lets a device create exactly 3 accounts per 10 min, and
  R_DEVICE fires at > 3 users, so a farm that stays within L6 is invisible to R_DEVICE
  (genuine_retry farm: recall 0). Kept the design threshold; flagging it for tuning.
- **Under overload L7 degrades open**: Redis calls past the 200 ms timeout mark entries
  `risk_unavailable` (score 0), never blocked.
- **Network switch** (all 200 users switch Wi-Fi → mobile after sign-in, 2,000-client flood):
  0 flagged, no session lost (no 401). 4/200 got `403 WINDOW_CLOSED`: their sign-in was slowed by
  the overloaded worker and they reached the entry step after the 25 s window. With a 60 s window (`network_switch_60s`, same flood): Fair 200/200 entered, 0 flagged, 0
  genuine 429s, every genuine winner confirmed; 27 client timeouts during the flood, all recovered
  by retries. The switch itself never broke a session.

## 6. Claim integrity (stampede, `evidence/stampede_*.json`)

400 users / 100 seats. Each eligible user fires 3 claims with one key + 2 with new keys + 1 replay
of its token from another session, all at once. FIFO: 2,000 claims → 500 × 200 (same seat per
user) + 1,500 × SOLD_OUT, sold 100. Fair: 500 claims → 500 × 200. Both: oversold 0, duplicate
seats 0, no user with 2 seats, 0 of 500 replays succeeded (TOKEN_INVALID). Ready to run against
the real backend (contract endpoints only).

## 7. Identity-budget sweep (`evidence/sweep.{csv,svg,json}`)

1,000 genuine users, attacker identities with clean proxies (L7 sees little: this isolates Sybil
fairness from speed fairness):

| Attacker identities (with account) | Fair identity share → seat share, A, band | FIFO attacker seat share |
|---|---|---|
| 500 (500) | 33.3% → 27%, A 0.74, inside | 100% |
| 2,000 (1,655) | 62.3% → 64%, A 1.07, inside | 100% |
| 5,000 (3,268) | 76.6% → 72%, A 0.79, inside | 100% |
| 10,000 (6,322) | 86.3% → 91.4%, A 1.68, inside | 100% |

Fair removes the speed advantage (seat share tracks identity share, inside the chance band);
it does not and cannot remove the identity-count (Sybil) advantage — that is L6–L8's job.
FIFO: ≥ 500 identities take every seat; 0–1 genuine users of 1,000 even entered.
At 10,000 identities the stub saturates: genuine claim success fell to 8/15 (358 client
timeouts, p99 15.4 s). Below ~3,300 active attacker sessions it was 100%.

## 8. Load and latency methodology

- Latency = client-observed time from request start to body read, per label.
- Server cost per request ≈ 1.2 ms in-process (limiter Redis call ≈ 0.6 ms); reject path CPU
  p50 0.41 ms / p99 0.81 ms (limiter only, real Redis, test).
- Client: aiohttp ≈ 0.8 ms per sequential request from the sim container. httpx's async client
  measured 5–16 ms and was replaced (it inflated early latency numbers).
- `.env` sets `UVICORN_WORKERS=4` and uvicorn reads `UVICORN_*` env vars as flags: the in-memory
  stub silently ran as 4 processes (random 404s/401s). The runner forces `--workers 1`.
- The stub (one worker) saturates around 1,500–2,500 req/s achieved. Every scorecard prints
  achieved average and peak req/s next to the configured client count.
- 50k logical-client flash crowd (no bots, 80% arriving in 10 s, 150 s window): the stub
  achieved 1,494 (FIFO) / 1,624 (Fair) req/s on average, peaks ~8,000. Fair: 17,290 of 50,000
  genuine users (34.6%) entered; 67 of 500 winners confirmed; 274,589 client timeouts (30 s);
  p95 ≈ 35 s; 6,082 users (12%) saw a 429 (1.85% of requests), 3,170 retries after a 429
  succeeded and 2,903 were still failing at the end. FIFO: 10,007 entered. Oversold 0 in both.
  This is the one-worker stub's ceiling with no attacker at all; it is not evidence about the
  real backend. The same command against the real backend is the meaningful run.
- Each run starts from clean state: stub recreated, its Redis db 2 flushed (same-seed reruns
  otherwise inherit OTP counters and get throttled).

## 9. Tests

- api: 39 passed (11 existing + 28 new), ruff + mypy strict clean. Limiter tests run against the
  real Redis container.
- sim: 8 passed, ruff + mypy strict clean. Includes the wire-capture label-isolation test and a
  regression test for counting sign-in 429s.

## 10. Reproduce

```
uv run fd up                                   # stack (after `uv run fd secrets`, `uv run fd migrate`)
python sim/scripts/evidence.py all             # every scenario pair -> sim/out/scorecard.md
python sim/scripts/evidence.py genuine_retry
python sim/scripts/evidence.py final           # 3 fixed seeds
python sim/scripts/evidence.py claim_stampede
python sim/scripts/evidence.py identity_budget_sweep
python sim/scripts/evidence.py flash_crowd_50k
docker compose run --rm --no-deps api sh -c "uv sync --frozen --no-install-project && pytest tests/abuse"
docker compose run --rm --no-deps sim sh -c "uv sync --frozen && pytest"
```

## 11. Limitations

- All evidence is against the dev stub (one worker, in memory), not the real backend.
- The stub has no waitlist promotion: seats freed by failed step-ups stay unused.
- Farms pass step-up with probability `step_up_success_p` (0 here); SIM_MODE exposes `dev_otp`
  to anyone, so step-up strength is modelled, not measured.
- The farm in identity_farm/sweep creates accounts with L6 off (`prestage_l6_off`) to model
  accounts made days earlier; "new user" uses the stub's own creation time.
- Identity farming is reduced (detection + step-up), not eliminated; clean-proxy identities
  (unique device/IP/UA, human-like OTP timing) are invisible to L7 by design.
- Lean-mode cuts were overridden only where the brief asked (multi-bucket Lua, fallback,
  UA/ASN/prefix signals). R_TIMING (inter-request regularity) is not built.

## 12. What Akshay's backend needs to provide (integration)

1. Install the middleware: `install_abuse(app, Limiter(redis, session_secret=SESSION_SECRET), ConfigStore(redis, persist=<write app_settings['abuse_config']>))` and mount `config_router(store, require_admin)`. Session tokens must stay `<uuid>.<b64url HMAC(SESSION_SECRET, uuid)>` (Plan 04) — the limiter verifies them without I/O. Optional `user_resolver` (Redis session cache) enables the per-user L3 bucket.
2. Call the hooks: `risk.otp_guard` before issuing an OTP (→ `429 OTP_THROTTLED` + Retry-After); `risk.record_verify(user_id, latency_ms)` after verify; `risk.score_entry(...)` after inserting an entry (insert first, then score); `risk.rescore(...)` at close before ranking; step-up for winners with score ≥ `step_up_score`.
3. `/me` must triple `poll_after_ms` when Redis key `slow:{session_id}` exists.
4. `X-Sim-Client-IP` honoured only when SIM_MODE=true; `/me` exposes `entry.dev_otp` for STEP_UP_REQUIRED in SIM_MODE (Plan 11).
5. FIFO sell-out must move remaining REGISTERED entries to NOT_SELECTED (design §13) or clients keep polling.
6. Size L1 pools from a real load measurement (defaults are placeholders).
7. Export rows exactly as the contract (risk_flags as a list of strings), and `/integrity`.

## 13. Next steps when you return

1. Review and merge `saanvi-attacks` (I did not merge or touch `main`).
2. Send Akshay section 12; when Plans 04/07/08/09 land, run `python sim/scripts/evidence.py all --target http://api:8000`.
3. Decide the L6/L7 device-threshold interaction (R_DEVICE > 3 vs L6's 3 phones/device).
4. Re-run the 10k sweep and the 50k crowd on the real 4-worker backend; the stub's ceiling is not the system's.
