| metric | genuine_retry fifo | genuine_retry fair | genuine_retry fifo | genuine_retry fair | genuine_retry fifo | genuine_retry fair |
|---|---:|---:|---:|---:|---:|---:|
| genuine users attempted / entered | 200 / 186 | 200 / 200 | 200 / 183 | 200 / 200 | 200 / 173 | 200 / 200 |
| genuine entry success (exactly 1 entry) | 0.9300 | 1.0000 | 0.9150 | 1.0000 | 0.8650 | 1.0000 |
| genuine users who saw a 429 | 0 (0.0000) | 0 (0.0000) | 0 (0.0000) | 0 (0.0000) | 0 (0.0000) | 0 (0.0000) |
| genuine first-try success | 1.0000 | 1.0000 | 1.0000 | 1.0000 | 1.0000 | 1.0000 |
| genuine 429 rate | 0.0000 | 0.0000 | 0.0000 | 0.0000 | 0.0000 | 0.0000 |
| genuine 429 -> retry ok / still failing | 0 / 0 | 0 / 0 | 0 / 0 | 0 / 0 | 0 / 0 | 0 / 0 |
| genuine seats confirmed / claim success | 19 / 0.151 | 40 / 1.000 | 19 / 0.157 | 42 / 1.000 | 19 / 0.147 | 47 / 1.000 |
| genuine step-up required / passed / failed | 0 / 0 / 0 | 0 / 0 / 0 | 0 / 0 / 0 | 0 / 0 / 0 | 0 / 0 / 0 | 0 / 0 / 0 |
| genuine 5xx / transport errors | 0 / 0 | 0 / 0 | 0 / 0 | 0 / 0 | 0 / 0 | 0 / 0 |
| fair winners confirmed | - / - | 40 / 40 | - / - | 42 / 42 | - / - | 47 / 47 |
| genuine p95 / p99 ms | 21.3 / 2482.6 | 1354.5 / 1662.0 | 422.2 / 2964.4 | 1367.2 / 1981.0 | 15.0 / 2397.0 | 1399.5 / 2395.7 |
| bot identities / got an account (L6) | 101 / 31 | 101 / 31 | 101 / 31 | 101 / 31 | 101 / 31 | 101 / 31 |
| bot requests | 11372 | 92728 | 13557 | 90278 | 11417 | 92994 |
| bot rejection rate | 0.9803 | 0.9913 | 0.9831 | 0.9910 | 0.9807 | 0.9901 |
| bot entry share / seat share | 0.143 / 0.620 | 0.134 / 0.200 | 0.145 / 0.620 | 0.134 / 0.160 | 0.152 / 0.620 | 0.134 / 0.060 |
| advantage ratio A | 9.789 | 1.613 | 9.632 | 1.229 | 9.105 | 0.412 |
| bot seats / 95% chance band | 31 / 3-12 | 10 / 3-11 | 31 / 3-12 | 8 / 3-11 | 31 / 3-12 | 3 / 3-11 |
| arrival-vs-win Spearman | -0.684 | 0.058 | -0.681 | 0.047 | -0.697 | 0.172 |
| flagged / human FPR / farm recall | 0 / 0.000 / 0.000 | 0 / 0.000 / 0.000 | 0 / 0.000 / 0.000 | 0 / 0.000 / 0.000 | 0 / 0.000 / 0.000 | 0 / 0.000 / 0.000 |
| replay / forged successes | 0 / 0 | 0 / 0 | 0 / 0 | 0 / 0 | 0 / 0 | 0 / 0 |
| oversold / duplicate seats | 0 / 0 | 0 / 0 | 0 / 0 | 0 / 0 | 0 / 0 | 0 / 0 |
| achieved avg / peak req/s | 287.3 / 2467 | 1503.9 / 2576 | 338.1 / 2641 | 1468.1 / 2466 | 290.5 / 2692 | 1491.4 / 2619 |
