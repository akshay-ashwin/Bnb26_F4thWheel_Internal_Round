# Plan 18 — Attack Simulator: Labelled Client Population, Scenarios, Multiprocess Runner, Ground Truth & Telemetry

| Field | Value |
|---|---|
| Design-doc sections | §1 Adversarial testing row, §6 Attack simulator + throughput reality check, §15 entire section, §16 Saanvi deliverables, §18 demo (launch attacks live), §20 "you simulate your own attackers" |
| Original owner | Saanvi |
| Depends on | Public API (Plans 04–11), telemetry endpoint (14). Start the skeleton right after Plan 03; scenarios come online as endpoints land |
| Unlocks | Plan 19 (evaluation), tuning in 12/13, the demo attack |
| Target time | 4 hours |

## 0. Mandatory operating protocol (read before writing anything)

1. Read `CLAUDE.md` (repo root) — it overrides this plan if they conflict.
2. Read `docs/implementation/PLAN_CHANGELOG.md`. If an earlier step edited this plan, the edited text is authoritative.
3. Read the review log of the previous plan in `docs/review-logs/` for handover notes.
4. Run the Pre-flight checks in section 3. Do not start building on a broken foundation.

Standing rules that apply to every line of work in this plan:

- **R1 — No AI attribution.** Never add Claude/Anthropic as author, co-author or contributor; no `Co-Authored-By` trailers, no "Generated with Claude Code" lines in commits or PRs; never touch git identity; never use `--no-verify`.
- **R2 — Better solution wins, and the future is rewritten.** If you find a better approach at any point, implement it (respecting the invariants in CLAUDE.md), write a Deviation Record in `docs/decisions/`, edit every affected later plan, and log it in `PLAN_CHANGELOG.md`.
- **R3 — Refine every step.** Do the Refinement Pass at the end of this plan before declaring it done, and refine the remaining plans if this step taught you something.
- **R4 — Review log.** Write `docs/review-logs/18-attack-simulator.md` in plain language from the template.

> Updated by D-001 (2026-10-04): stack rules apply to this plan. Everything runs through Docker Compose and `uv run fd <task>` (no `make`, no host Python or Node), files are LF, Node is 24 LTS (D-002), macOS and Windows are both supported, and fullstack-dev-skills may be used as advice but never its `project:*` workflow commands. CLAUDE.md always wins. See CLAUDE.md "Stack and platform rules" and `docs/decisions/D-001-cross-platform-docker-uv-node22-skills.md`. Read any `make X` below as `uv run fd X`.

## 1. Goal

A reproducible population of humans and bots, each a small state machine using ONLY the public API exactly as a browser would, that can model 50,000 logical clients arriving in a 30 s burst plus attackers with up to 10,000 clients and thousands of farmed identities, writes a ground-truth file the backend never sees, streams labelled telemetry to the dashboard, and honestly reports the load it actually achieved.

## 2. Scope

In scope: client models (human, bot variants), identity factory, network identity (sim IP, device, UA), scenario YAML schema, all nine scenarios + identity-budget sweep config, multiprocess runner with coordinator, ground truth output, telemetry, run manifest, safety guard, OS tuning notes.
Out of scope: metrics/fairness computation (19).

## 3. Pre-flight checks

1. `SIM_MODE=true` in the target backend (dev_otp, X-Sim-Client-IP, telemetry enabled).
2. Contract examples available; `sim/` is a separate Python project with NO imports from `api/` (invariant 6).
3. The simulator must work in BOTH modes (D-001, 2026-10-04): in the compose `sim` container (default, Linux) and natively on Windows or macOS with `uv run --project sim sim ...` (also on a second laptop). Neither mode may require `uvloop`, `fork`, `resource`, POSIX signals or any other Unix-only API (see 7 and 8).

## 4. Safety guard (build first)

The runner refuses to target any base URL not in an explicit allowlist (localhost, `host.docker.internal` (a containerised simulator reaching an API on the host; D-001), compose service names, the second laptop's LAN IP configured by flag) and refuses to run unless the target's `/api/drops/{id}` reports a drop and `/api/readyz` responds. This is an attack tool; it must not be pointable at arbitrary hosts. Note it in the README.

## 5. Client model

Every client carries `{client_id, label: human|bot, actor_id, identity_id, device_id, sim_ip, ua}`.

### 5.1 Identity factory
- Phones: synthetic Indian mobile numbers that pass validation. Humans: random across the full range. Farms: configurable mix of sequential blocks (to exercise L6 prefix detection) and scattered numbers ("good" farms).
- Each identity verifies once: OTP request → read `dev_otp` → wait a typing delay → verify. Humans' typing delay 3–15 s (lognormal) so R_FAST_OTP is realistic; bots 0–300 ms unless "careful".
- Store `user_public_id` and session token per identity (for ground truth join).
- Identity pre-warming: scenarios may verify identities BEFORE the drop opens (realistic: people sign up earlier). Make it a scenario parameter; the 50k flash crowd should pre-verify to avoid measuring OTP throughput as drop throughput — record the choice.

### 5.2 Network identity
- Humans: IPs spread across many /24s (residential), plus configurable "campus clusters" (e.g. 3 clusters of 1,000–2,000 humans behind one /24) — essential for honest FPR. Devices unique per human (5% share a device with a family member: 2 users per device). UA drawn from a realistic distribution of ~50 common UAs.
- Bots: per attacker config — number of devices, number of /24s, UA reuse, fraction using "good proxies" (unique device + unique /24 + varied UA).
- Sent as `X-Sim-Client-IP`; device_id in auth bodies.

### 5.3 Human state machine (`clients/human`)
Arrive per the arrival curve → (verify if not pre-verified) → open drop → think 1–4 s → enter (Fair) or get-seat (FIFO) → poll `/me` honouring `poll_after_ms` → when offered: react in 2–8 s → confirm → done. Imperfections (each a probability parameter): refresh (re-read `/me`), drop connection for 2–10 s then resume with the same idempotency key, open a second tab that also polls and sometimes taps confirm, retry on errors per the frontend retry table, a small share never confirms (models no-shows; drives waitlist movement). Honour Retry-After always.

### 5.4 Bot state machine (`clients/bot`) — variants via config
- **Flooder:** N parallel connections hammering entries/claim/me with zero think time; ignores Retry-After unless `polite: true`.
- **Racer (FIFO):** pre-verified identities, fires get-seat the instant phase turns OPEN (poll `/drops/{id}` at high rate), retries immediately on any error.
- **Farmer:** many identities spread per network config; each enters once; when offered, claims instantly; on STEP_UP passes with probability `step_up_pass_rate` (default 0.1 for farms — models inability to receive fresh OTPs at scale; humans always pass).
- **Replayer:** obtains a winner's token (from its own winning identity) and replays it from other sessions; reuses expired tokens; forges tokens (random signature, alg none, modified claims); every attempt labelled with its expected outcome so the evaluator can check expectations.
- **Multi-tab:** one identity × K sessions/tabs firing claim simultaneously.

## 6. Scenario files (`scenarios/*.yaml`) — schema
Top-level: `name`, `mode_expectations` (notes), `seed` (RNG), `drop` {capacity, window_s, claim_window_s}, `population` {humans, arrival curve: type (uniform | burst with share in first S seconds | custom points), pre_verify, imperfection probabilities, campus clusters}, `attackers[]` {actor_id, type, clients, identities, devices, ip24s, good_proxy_share, rate_per_client, polite, step_up_pass_rate, start_offset_ms}, `phases[]` (timeline of attack_phase labels for the story strip), `orchestration` {auto: create/reset drop with mode, open, close at window end, draw, wait for DONE}, `limits` {max_concurrent_sockets per shard}.

Write all scenarios from design §15: normal, flash_crowd, bot_flood, repeated_attempts, multi_tab, token_replay, identity_farming, burst_at_launch, combined_demo. Plus `identity_budget_sweep` (a meta-scenario that runs combined_demo with bot identity budgets 0, 500, 2,000, 5,000, 10,000 in both modes — design §20 "biggest weakness, fixed").

Each scenario file states its expected Fair and FIFO behaviour (copied from design §15) so the evaluator can print pass/fail per expectation.

## 7. Runner

- **Architecture:** a coordinator process + P shard processes (P ≈ CPU cores − 1, or configured). Each shard runs one asyncio loop with an httpx AsyncClient per logical session (cookie jar per session; or bearer tokens to save memory — record choice: bearer is lighter and the API treats both the same) and a shared connection pool limited to `max_concurrent_sockets`.
- **Cross-platform process model (D-001, 2026-10-04):** create processes with `multiprocessing.get_context("spawn")` explicitly, because `spawn` is the only start method on Windows and the default on macOS; therefore everything passed to a shard must be picklable, shard entry points live at module level, and the CLI entry is guarded by `if __name__ == "__main__"`. No `os.fork`, no `signal.SIGUSR1` or similar; handle Ctrl-C portably. Event loop: use `uvloop` only if it imports (optional extra `fast`, never on Windows); otherwise the stdlib loop (ProactorEventLoop on Windows — do not switch Windows to the Selector loop, which is limited to 512 sockets by `select`). Record the loop in use in the manifest. Import the Unix-only `resource` module (raising the open-file limit) only when `sys.platform != "win32"`.
- Clients are assigned to shards deterministically by `zlib.crc32(client_id.encode("utf-8")) % P` (a stable hash), so a seed reproduces the same run. > Updated by D-001 (2026-10-04): the plan said `hash(client_id) mod P`. Python randomises `str` hashes per interpreter process and `spawn` starts fresh interpreters, so that would assign clients differently in every shard and break reproducibility. Same rule for any other stable bucketing in `sim/` (never use built-in `hash()` on strings).
- Arrival scheduler: precompute arrival timestamps from the curve with the seeded RNG; shards sleep until each client's start time.
- **Orchestration:** the coordinator uses admin endpoints to reset/create the drop with the scenario's mode, open, close (or let auto-close), draw, and wait for DONE; records each phase timestamp.
- **Telemetry:** shards send per-second counts by label to the coordinator over a multiprocessing queue; the coordinator posts `/sim/telemetry` once per second (attack_phase, clients_by_label, identities_by_label, requests_by_label) and, if the live evaluator is enabled (Plan 19), `fairness_live`.
- **Ground truth:** each shard appends NDJSON lines to its own file; the coordinator merges at the end into `sim/out/<run_id>/ground_truth.ndjson`: per identity `{run_id, identity_id, user_public_id, label, actor_id, device_id, sim_ip24, clients_count, requests_sent, requests_by_outcome, entered, first_entry_ms, offered, claimed, seat_no?, step_up_seen, step_up_passed}`; per replay attempt `{expected_outcome, observed_status, observed_code}`.
- **Run manifest** `sim/out/<run_id>/manifest.json`: scenario file hash, RNG seed, drop id, mode, run_no, seed_commit, start/end, host info (OS and version, CPU count, Python version, event loop in use, run mode `container|native`, and for runs against a Dockerised API the Docker backend and the CPU/RAM given to Docker), achieved peak RPS, peak concurrent sockets, shards, errors by type, simulator CPU saturation warnings. > Updated by D-001 (2026-10-04): loop, mode and Docker resources added so numbers from macOS, Windows and Linux are never compared blindly.
- **Achieved load honesty:** measure RPS and concurrent sockets on the simulator side; print them next to "50,000 logical clients" in the summary. If any shard's event loop lag exceeds 100 ms, flag "simulator-bound" in the manifest — the numbers then measure the simulator, not the API.

## 8. OS / hardware notes (document in `sim/README.md`; general notes also in `docs/PLATFORMS.md`)

> Updated by D-001 (2026-10-04): the single "raise open file limits" note is replaced by per-OS notes, because `ulimit` does not exist on Windows and the limits that matter differ by OS.

The goal in every row is the same: allow a few thousand concurrent sockets and enough free local (ephemeral) ports. Prefer running the simulator on a second laptop over LAN (design); that laptop may be Windows or macOS and runs the simulator natively. Record the setup actually used for the final runs.

| Where the simulator runs | What to do | Notes |
|---|---|---|
| Compose container (Linux, either host OS) — default | Set `ulimits: nofile` (e.g. 65535) and, if needed, `sysctls: net.ipv4.ip_local_port_range` on the `sim` service in `infra/docker-compose.yml`. Nothing to do on the host. | Docker Desktop's Linux VM CPU/RAM allocation caps the whole stack (Windows: WSL2 `.wslconfig`; macOS: Docker Desktop Resources). Published ports pass through Docker Desktop's proxy layer, so a native simulator hitting a containerised API on the same machine measures that layer too. |
| macOS, native | In the shell that runs the simulator: `ulimit -n 65535` (the OS cap is `sysctl kern.maxfilesperproc`; persistent change via `launchctl limit maxfiles`). Widen local ports with `sudo sysctl -w net.inet.ip.portrange.first=10000` (resets on reboot). | Python's `resource` module can also raise the soft limit; the runner does it when available. |
| Windows, native | There is no `ulimit`. The limit that bites is the dynamic port range (default 49152–65535): widen it from an administrator PowerShell with `netsh int ipv4 set dynamicport tcp start=10000 num=55535`. Optionally shorten TIME_WAIT with the `TcpTimedWaitDelay` registry value (reboot needed). | Use the default ProactorEventLoop. Do not import `resource`. Windows Defender Firewall may prompt once; the simulator only makes outbound connections. |
| Any OS, native, API on another machine | Add that machine's LAN IP to the allowlist flag (section 4). | Best setup for honest load numbers (design: second laptop). |

Never present numbers from different setups as comparable; the manifest (section 7) records which setup produced them.

## 9. CLI
`sim run --scenario <file> --mode fifo|fair --target <url> --seed <n> [--live-eval]`, `sim sweep --budgets 0,500,2000,5000,10000`, `sim list`, `sim replay-expectations <run_id>`. Exit code non-zero if any scenario expectation hard-fails (e.g., integrity), so CI can run small versions.

> Updated by D-001 (2026-10-04): how to invoke it. Container (default): `uv run fd sim --scenario <name> --mode <fifo|fair>` (the `fd` wrapper passes the remaining flags through to `sim run`). Native on the host (Windows, macOS, second laptop): `uv sync --project sim` once, then `uv run --project sim sim run ...` or `uv run fd sim --native ...`. Output goes to `sim/out/<run_id>/` in both modes (bind-mounted for the container), so `uv run fd eval --run <id>` finds it either way.

## 10. Tests

1. Determinism: same scenario + seed → identical arrival schedule, identity set and attacker layout.
2. Humans honour poll_after_ms and Retry-After; bots configured impolite don't.
3. Ground truth lines join 1:1 with export rows by user_public_id.
4. Small CI versions (e.g., 200 humans + 1 small attacker) of each scenario run in < 60 s against the compose stack.
5. Safety guard blocks a non-allowlisted target.
6. Cross-platform (D-001, 2026-10-04): the simulator's unit tests (determinism, safety guard, identity factory, stable shard assignment — the same client ids map to the same shards in two separate interpreter processes) pass on the `windows-latest` and `macos-latest` CI runners with `uvloop` NOT installed, and a tiny native run against a stub server completes under the `spawn` start method. The full-stack scenarios in test 4 stay on Linux CI.

## 11. Verification / Definition of Done

All scenarios run at small scale in CI; normal + bot_flood + combined_demo run at full scale on the demo hardware with manifests recorded; achieved RPS and sockets noted in the review log, together with the run mode, OS and event loop. At least one small scenario has been run natively on each OS available to the team (D-001).

## 12. Plan-update obligations

- Plan 13: feed back human FPR from the normal scenario; adjust thresholds there via R2 if needed.
- Plan 12: if humans ever see 429s, fix limits.
- Plan 19: evaluator reads ground truth + manifest shapes defined here.
- Plan 17: story strip `attack_phase` values.

## 13. Review log must explain

- Who the simulated people are, how humans and bots differ, and why humans are deliberately imperfect.
- How labels are kept away from the backend.
- What "50,000 clients" actually meant on our hardware (achieved RPS and sockets).
- How a judge could re-run any result (scenario file + seed), on macOS or Windows, in the container or natively.
- The per-OS tuning that was applied for the final runs and what each OS can and cannot do (D-001).

## Close-out sequence (do all of these, in order)

1. **Definition of Done.** Run every check in the Verification section. Record the actual numbers (tests passed, latencies, oversold counts) — never write a number you did not observe.
2. **Refinement Pass (R3).** Re-read the full diff as a strict reviewer; remove dead code and debug output; check names against `docs/GLOSSARY.md`; walk each failure path (Postgres slow, Redis gone, request retried, two tabs at once); run the whole test suite, not just the new tests; ask "is there a simpler way?" and apply R2 if yes.
3. **Downstream sync (R2).** Open each plan listed in "Plan-update obligations" (and any other later plan you affected). Edit stale instructions, mark them `> Updated by D-00X`, and append to `PLAN_CHANGELOG.md`. If nothing changed, write "no downstream changes" in the review log.
4. **Review log (R4).** Write `docs/review-logs/18-attack-simulator.md` from the template, covering every item in this plan's "Review log must explain" list.
5. **Attribution check (R1).** List unpushed commits with their author and full message; confirm the author is the repo owner and no message contains AI attribution. Fix by rewording before pushing if needed.
6. **Commit** the plan's final state with a conventional message (e.g. `feat(alloc): atomic claim with skip-locked seats`) and the repo owner's identity.
7. **Chat summary.** 5–10 lines: what is done, key numbers, deviations, link to the review log, what Plan 19 starts with.
