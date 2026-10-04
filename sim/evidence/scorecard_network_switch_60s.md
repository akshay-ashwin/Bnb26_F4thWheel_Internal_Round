| metric | network_switch_60s fifo | network_switch_60s fair |
|---|---:|---:|
| genuine users attempted / entered | 200 / 166 | 200 / 200 |
| genuine entry success (exactly 1 entry) | 0.8300 | 1.0000 |
| genuine users who saw a 429 | 0 (0.0000) | 0 (0.0000) |
| genuine first-try success | 1.0000 | 0.9920 |
| genuine 429 rate | 0.0000 | 0.0000 |
| genuine 429 -> retry ok / still failing | 0 / 0 | 0 / 0 |
| genuine seats confirmed / claim success | 49 / 0.320 | 49 / 1.000 |
| genuine step-up required / passed / failed | 0 / 0 / 0 | 0 / 0 / 0 |
| genuine 5xx / transport errors | 0 / 0 | 0 / 27 |
| fair winners confirmed | - / - | 49 / 49 |
| genuine p95 / p99 ms | 21.8 / 1505.7 | 1989.3 / 10675.3 |
| bot identities / got an account (L6) | 1 / 1 | 1 / 1 |
| bot requests | 8244 | 128974 |
| bot rejection rate | 0.9945 | 0.9976 |
| bot entry share / seat share | 0.006 / 0.020 | 0.005 / 0.020 |
| advantage ratio A | 3.388 | 4.082 |
| bot seats / 95% chance band | 1 / 0-1 | 1 / 0-1 |
| arrival-vs-win Spearman | -0.572 | 0.012 |
| flagged / human FPR / farm recall | 0 / 0.000 / - | 0 / 0.000 / - |
| replay / forged successes | 0 / 0 | 0 / 0 |
| oversold / duplicate seats | 0 / 0 | 0 / 0 |
| achieved avg / peak req/s | 122.5 / 2513 | 1473.4 / 2559 |
