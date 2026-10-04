# Fair Drop — judge notes (REAL BACKEND)

**Bots can generate extreme traffic without gaining a speed-based advantage over genuine users.**

Setup: the real integrated backend (4 workers, Postgres, Redis) with abuse protection ON
(`ABUSE_ENFORCE=true`). 200 genuine users (campus NAT, carrier NAT, Wi-Fi→mobile switches) +
10,000 bot clients (flood, burst, duplicate, farm, token replay, anonymous). 50 seats. Seed 101,
clean database and Redis per run. Same population in both modes. Numbers:
`docs/evidence/final_scorecard.json`.

| | FIFO (before) | Fair (after) |
|---|---|---|
| Attacker seats | **34 / 50** | **14 / 50** |
| Advantage ratio (attacker win rate ÷ genuine win rate, per entry; 1 = fair) | **9.006×** | **0.871×** |
| Attacker seats vs 95% luck-only band | outside (band 5–14) | **inside (band 10–21)** |
| Arrival order vs winning (Spearman) | **−0.651** (early wins) | **0.039** (order irrelevant) |
| Bot attack requests rejected (429) | 80.2% | **98.4%** |

98.4% is request-level rejection, not "98.4% of bots kept from seats". Fair does not promise
zero bot seats: each verified identity gets exactly one entry and one equal chance, whatever its
speed or request count.

**Integrity (both modes):** oversold 0 · duplicate seats 0 · token replay/forgery successes 0.

**Risk scoring:** genuine shared-IP / campus-NAT false positives 0 · genuine network-switch
false positives 0.

**Smoke test (real backend, enforcement on):** 41/41 checks: OTP retry, entry repeat, 429 +
Retry-After + successful retry, refresh, second tab, draw, idempotent and concurrent claims,
replay and forgery rejected, FIFO race sells exactly 1 of 1.

**Bug found and fixed by this test:** the rate limiter shared the backend's Redis pool; the
flood exhausted it and sign-in returned 503. The limiter now has its own pool.
Regression (400 concurrent anonymous requests + 20 genuine OTP requests): before 0/20, **after
20/20**. API suite: **279 passed**.

## Limitation (not hidden)

Under the 10k-client real-backend run, only 121/200 genuine users entered in the Fair run, so
full genuine-user availability under this attack load is not yet proven. This is an
availability/capacity limitation, not evidence that the fairness mechanism failed.

All 79 who did not enter never completed sign-in after their retries (623 genuine 5xx). Everyone
who signed in entered exactly once, and 35/35 genuine Fair winners confirmed their seat. One
post-fix seed only. Before the fix the same run had 37/200.
