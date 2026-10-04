# sim

**Purpose:** the attack simulator and evaluator. It hits the same public API as the browser, labels its own clients as human or bot (ground truth), and produces the fairness scorecard. Ground truth stays in this project; the backend never reads it (invariant 6).
**Owner:** Saanvi (simulator and evaluator).

## Layout

- `src/sim/model.py`: `Identity` (holds the label, never sent) and `Stats`.
- `src/sim/api.py`: aiohttp client for the contract endpoints. Builds requests from session token, device id, IP and user agent only. Cookie jar disabled (one shared session serves thousands of identities).
- `src/sim/clients/human.py`: think time, OTP typing, repeated entry clicks, refresh, second tab, Wi-Fi to mobile switch, honours `Retry-After` and `poll_after_ms`, step-up, claim.
- `src/sim/clients/bot.py`: flood, burst, duplicate, replay, farm, anonymous junk.
- `src/sim/runner.py`: builds the population, drives the drop through the admin API, writes the run folder.
- `src/sim/evaluator.py`: scorecard from the backend export joined with local ground truth.
- `src/sim/stampede.py`: claim stampede (double clicks, lost-response retries, replays at one instant).
- `scenarios/*.toml`: scenario definitions (standard-library TOML, no extra dependency).
- `scripts/evidence.py`: reproducible suites from clean backend state.
- Output: `out/` (git-ignored).

## Run

The stack must be up (`uv run fd up`, after `uv run fd secrets`). Then, from the repo root, in PowerShell or a macOS terminal:

```
python sim/scripts/evidence.py genuine_retry          # FIFO and Fair, then a side-by-side scorecard
python sim/scripts/evidence.py all                    # every scenario pair -> sim/out/scorecard.md
python sim/scripts/evidence.py claim_stampede
python sim/scripts/evidence.py identity_budget_sweep  # sim/out/sweep.{json,csv,svg}
python sim/scripts/evidence.py final                  # genuine_retry, 3 fixed seeds, both modes
python sim/scripts/evidence.py genuine_retry --target http://api:8000   # the real backend
```

Suites: `normal`, `genuine_retry`, `bot_flood`, `identity_farm`, `campus`, `network_switch`, `flash_crowd`, `repeated_attempts`, `multi_tab`, `token_replay`, `claim_stampede`, `identity_budget_sweep`, `final`, `all`.

Single commands inside the `sim` container (`docker compose run --rm sim sh -c "uv sync --frozen && sim ..."`):

```
sim run scenarios/normal.toml --mode fair --base-url http://fairdrop-devstub:8001 --out out/x
sim eval out/x
sim compare out/a out/b --out out/scorecard.json --md out/scorecard.md
sim stampede --mode fair --users 400 --capacity 100 --base-url URL
sim sweep-report out/sweep_* --out out/sweep.json
```

`--seed` and `--identities` override a scenario. `SIM_TRACE=1` writes every request to `trace.ndjson`.

## Target backend

Until the real endpoints exist, suites run against `api/app/abuse/devstub`, a dev-only, in-memory implementation of the frozen contract with the real abuse middleware, L6 guard and L7 scoring in front. It must run with ONE worker: `.env` sets `UVICORN_WORKERS=4` and uvicorn reads `UVICORN_*` variables as CLI flags, which silently split its state across 4 processes. `evidence.py` passes `--workers 1` and recreates it with a flushed Redis database (db 2) before every run, so same-seed reruns never inherit OTP counters.

## Measurement notes

- Latency is client-observed (request start to body read) per label. Measured overheads: server work per request about 1.2 ms in-process (limiter Redis call about 0.6 ms); aiohttp client about 0.8 ms per sequential request from the sim container. The earlier httpx client cost 5-16 ms per request and was replaced.
- "Logical clients" are coroutines, not concurrent requests. Every run reports achieved average and peak requests/s.
- Genuine-user metrics use all simulated genuine users as the denominator, including anyone blocked before entering.

## Ground-truth isolation

`label`, `actor_id` and `kind` exist only in `Identity` and the run folder (`ground_truth.ndjson`, `humans.ndjson`). `tests/test_label_isolation.py` captures every request the simulator sends (all client kinds) and asserts none of them appears. `api/tests/abuse/test_isolation.py` asserts the backend decision code never reads labels or `sim:*` keys. Telemetry (`--telemetry`, contract interface 6) sends only aggregate counts to the presentation-only endpoint and is off by default.

Native runs (`uv run --project sim sim ...`) work on Windows and macOS; uvloop is the optional extra `fast`, installed in the container image only.
