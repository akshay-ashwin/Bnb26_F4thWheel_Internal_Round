# D-001 — Cross-platform stack: Docker-only runtime, `uv run fd` task CLI, LF everywhere, Node 22 LTS, skills policy

Date: 2026-10-04  ·  Raised during: before Plan 01 (human-directed stack decision)  ·  Status: ADOPTED

## The original plan said

- Plan 01 §4.7 and many later plans (02, 03) use GNU Make targets: `make up`, `make migrate`, `make secrets`, `make hooks`, and so on. Plan 01 §4.6 asks for Python 3.12 and Node 20 on the host, and a pre-commit framework running host tools.
- Plan 01 §4.4 uses `ulimit nofile` for the `api` and `sim` containers, and a symlinked root `compose.yaml`. Plan 18 §8 tells the reader to "raise open file limits; widen ephemeral port range; enable TCP reuse settings appropriate for the OS" without saying which OS.
- Plan 01 §3 and §4.6 say "Node 20 LTS".
- Nothing says which Claude Code skills or plugins may be used while building.

## What we do instead

### 1. Two operating systems, one way to run everything

macOS and Windows 11 are both first-class. Linux is what the containers and CI run. The only things installed on a developer machine are **git, Docker (Compose v2; on Windows, Docker Desktop in Linux-container / WSL2 mode) and uv**. Python 3.12 and Node 22 live inside the images.

- **Everything runs through Docker Compose:** `postgres`, `redis`, `migrate`, `api`, `web`, tests, lint, formatters, OpenAPI export, and the simulator by default. The root `compose.yaml` is a real file (not a symlink) that `include`s `infra/docker-compose.yml`. Git symlinks need extra privileges on Windows.
- **Make is replaced by a Python task CLI, invoked as `uv run fd <task>`.**
  - Location: `tools/fd/` plus a root `pyproject.toml` and `uv.lock`. Standard library only (argparse + subprocess with argument lists, never `shell=True`). It shells out to `docker compose` and `git`. It never imports from `api/` or `sim/`, so it cannot touch ground truth (invariant 6 stays physical).
  - Console output is forced to UTF-8, with no emoji or ANSI dependence, so Windows consoles do not crash on a dash.
  - Task names are the same as the old Make targets (kebab-case):

    | Old Make target | New command | Runs |
    |---|---|---|
    | `make up` / `down` / `logs` | `uv run fd up` / `down` / `logs` | `docker compose up -d --wait`, `down`, `logs -f` |
    | `make migrate` / `reset-db` | `uv run fd migrate` / `reset-db` | the `migrate` one-shot service (dbmate image); `reset-db` drops the Postgres volume and re-migrates |
    | `make test-api` / `test-web` | `uv run fd test-api` / `test-web` | `docker compose run --rm api pytest` / web container `pnpm test` (`--e2e` adds Playwright) |
    | `make lint` / `fmt` | `uv run fd lint` / `fmt` | ruff, mypy, eslint, tsc, prettier in containers, plus a "no CR bytes in tracked text files" check and the Plan 14 isolation script |
    | `make hooks` | `uv run fd hooks` | sets repo-local `core.hooksPath=infra/git-hooks` (not identity) and `chmod +x` on POSIX |
    | `make secrets` | `uv run fd secrets` | writes `.env` from `.env.example` with `secrets.token_hex(32)` values, LF endings, refuses to overwrite without `--force` |
    | `make sim SCENARIO=x MODE=y` | `uv run fd sim --scenario x --mode y [--native]` | sim container, or the host with `--native` |
    | `make eval RUN=id` | `uv run fd eval --run id [--native]` | evaluator, same two ways |
    | `make demo-reset` | `uv run fd demo-reset` | one-command reset used by the runbook |

  - **Two additions, flagged as such:** `uv run fd openapi` (Plan 03 already says "a make target" for the OpenAPI export but Plan 01's list omitted it) and `uv run fd doctor` (cross-platform pre-flight: tool versions, Docker mode, free ports, excluded Windows port ranges, repo-inside-cloud-sync warning, `.env` has no CR).
  - Anywhere an existing plan, review-log template or doc says `make X`, read it as `uv run fd X`.
  - Git hooks in `infra/git-hooks/` are two-line `sh` shims (Git for Windows ships `sh`) that call `uv run fd attribution-check ...`. All the logic is in Python, so it behaves the same on both OSes. The pre-commit framework is dropped; a `pre-commit` shim calls `uv run fd lint --staged` instead.
- **Line endings are forced to LF.**
  - `.gitattributes`: `* text=auto eol=lf`, with explicit `binary` entries.
  - `.editorconfig`: `end_of_line = lf`.
  - `fd secrets` writes `.env` with `newline="\n"`.
  - `fd lint` fails if a tracked text file contains a CR byte.
  - The attribute beats any user's `core.autocrlf`. This matters because CRLF in `.env`, entrypoint scripts or SQL breaks Linux containers in confusing ways (`\r` ends up inside values and shebangs).
- **Simulator runs natively too (Windows and macOS), because it is just Python + httpx.**
  - `uvloop` becomes optional: `sim/pyproject.toml` gets an extra `fast = ["uvloop; sys_platform != 'win32'"]`. The runner uses uvloop if importable, else the stdlib loop (ProactorEventLoop on Windows; never switch it to the Selector loop there, which has a 512-socket `select` limit). The container image installs the extra. The chosen loop is written into the run manifest.
  - Native runner rules: multiprocessing uses the `spawn` context explicitly (Windows and macOS default); no `os.fork`, no Unix-only signals; the `resource` module (used to raise the open-file limit) is imported only on non-Windows.
  - Run natively with `uv run --project sim sim run ...` or `uv run fd sim --native ...`.
- **`ulimit` notes become per-OS notes.** They live in `sim/README.md` (Plan 18) and `docs/PLATFORMS.md` (Plan 01). Summary of what they cover:
  - **Compose / Linux containers:** `ulimits.nofile` and `sysctls` set in the compose file, which behaves the same on Docker Desktop for both OSes.
  - **macOS native:** `ulimit -n`, `launchctl limit maxfiles`, `sysctl kern.maxfilesperproc`, widen the ephemeral range with `sysctl net.inet.ip.portrange.first`.
  - **Windows native:** there is no `ulimit`; the limit that bites is the dynamic port range, widened with `netsh int ipv4 set dynamicport tcp ...` (admin shell), plus optional `TcpTimedWaitDelay`.
  - **Docker Desktop (both OSes):** the Linux VM's CPU/RAM allocation (WSL2 `.wslconfig` on Windows, Resources pane on macOS) caps the whole stack, and published ports go through a proxy layer. Evidence runs must record these values.
- **Cross-OS compose rules:**
  - Source is bind-mounted for dev; `node_modules`, the Python virtualenvs and Postgres data live in **named volumes** (bind-mounted dependency trees are very slow on Docker Desktop, and the repo may live in OneDrive).
  - Vite uses `server.watch.usePolling` and uvicorn `--reload` uses `WATCHFILES_FORCE_POLLING=true` in dev, because file-change events do not cross a Windows or macOS bind mount reliably. Performance runs start the API without reload.
  - A native simulator reaching a containerised API uses `localhost:<port>`; a container reaching a native process uses `host.docker.internal`. Both are on the safety allowlist.
- **CI:** full Compose-stack jobs run on `ubuntu-latest`. Windows and macOS runners cannot run Linux containers, so they run only what does not need Docker: the `fd` CLI unit tests, the attribution-check logic, the simulator unit tests and a native smoke run of the simulator's safety guard and identity factory, with uvloop absent.

### 2. Node 22 LTS instead of Node 20

The `web` image is `node:22-bookworm-slim` (Debian-based, not Alpine, so Playwright and native optional dependencies are painless). `web/package.json` sets `engines.node` to `>=22 <23` and `packageManager` to a pinned pnpm, enabled through corepack (bundled with Node 22). CI uses Node 22. The host needs no Node.

### 3. The `fullstack-dev-skills` plugin: use the skills, never the workflow commands

- Allowed, as advisory input only: the topical skills (for example `fastapi-expert`, `postgres-pro`, `sql-pro`, `python-pro`, `react-expert`, `typescript-pro`, `playwright-expert`, `test-master`, `devops-engineer`, `cli-developer`, `security-reviewer`, `secure-code-guardian`, `code-reviewer`, `chaos-engineer`, `monitoring-expert`, `database-optimizer`, `api-designer`, `debugging-wizard`).
- Forbidden: every `project:*` skill / `/project:*` command (the Jira ticket / epic / sprint workflow: execute-ticket, complete-ticket, create-epic-plan, create-implementation-plan, complete-epic, complete-sprint, the discovery commands) and the Atlassian integration. This repo's workflow is CLAUDE.md plus `docs/implementation/plans/`. `common-ground` is used only when the human asks for it.
- **Precedence: CLAUDE.md > the plans > any skill.**
  - A skill suggestion that conflicts with a plan or an invariant is ignored, or, if clearly better, adopted through Rule R2 with its own Deviation Record. It is never applied silently. Examples to watch: SQLAlchemy / ORMs (plans use asyncpg and plain SQL), server-side session or stock JWT-login patterns (Plan 04 specifies signed session tokens), Next.js (design doc chose Vite).
  - Skills never override Rule R1. Any commit / PR text, trailer, "generated by" line or settings change that a skill or the harness suggests is dropped.
  - Skills do not write outside the repository map, and do not create tickets, pages or comments in any external service.
- Advisory skill-to-plan hints (not binding):

  | Plans | Useful skills |
  |---|---|
  | 01 | `devops-engineer`, `cli-developer` (for `fd`), `python-pro` |
  | 02 | `postgres-pro`, `sql-pro`, `database-optimizer` |
  | 03–11 | `fastapi-expert`, `python-pro`, `api-designer`, `postgres-pro`, `test-master`; `chaos-engineer` for 11 |
  | 12–13 | `secure-code-guardian`, `security-reviewer`, `python-pro` |
  | 14 | `monitoring-expert`, `api-designer` |
  | 15–17 | `react-expert`, `typescript-pro`, `playwright-expert` |
  | 18 | `python-pro`, `test-master` |
  | 19 | `chaos-engineer`, `monitoring-expert`, `database-optimizer`, `pandas-pro` (evaluator statistics) |
  | every Refinement Pass | `code-reviewer`, `security-reviewer` |

## Why it is better (evidence)

- **Make fails on a stock Windows machine.** GNU Make is not installed, and recipes assume `sh`, `ulimit`, `chmod`, symlinks and `/dev/null`. A required hackathon laptop being Windows is a certainty here (this repository is being developed on Windows 11), and macOS is the other stated target. A Python CLI run through uv is the one thing that is identical on both, and uv is a single static binary.
- **"Everything in Docker" removes version drift.** One Python, one Node, one Postgres, one Redis for everybody, so a number measured on one laptop is not explained away by a toolchain difference. It also removes the Plan 01 pre-flight failure mode "Python 3.12 / Node 20 not installed".
- **CRLF is a real failure case, not taste.** Windows checkouts with `autocrlf=true` put `\r` into shell entrypoints (`/bin/sh^M: bad interpreter`), `.env` values (a trailing `\r` inside a secret) and SQL. `.gitattributes eol=lf` plus a lint check removes the whole class. (This machine has `core.autocrlf=true`.)
- **`hash(client_id) mod P` in Plan 18 is a latent bug that cross-platform exposes.** Python randomises `str` hashes per process, and `spawn` (the Windows and macOS default) starts fresh interpreters, so shard assignment would differ per process and break the "same seed, same run" claim. Replacing it with a stable hash (`zlib.crc32`) is required by this change.
- **uvloop does not exist for Windows,** so making it a platform-marked optional extra is the only way to keep one codebase.
- **Node 20 is past its end of life** (April 2026, to my knowledge; worth confirming). Node 22 LTS is supported and is what the human specified. Note for the record: Node 24 is the newer LTS line; Node 22 is in maintenance until about April 2027, which is enough for this project.
- **The plugin's `project:*` workflow is built around Jira tickets and its own commit/PR conventions.** That directly conflicts with R1 (no AI attribution), R2 (Deviation Records instead of ad-hoc plan edits) and the repository map. A written precedence rule prevents a silent conflict in the middle of a plan.

## Invariants check

1. 500 seats never 501 — unaffected (no change to schema or claim logic).
2. One identity = one entry = one seat — unaffected.
3. Postgres is the only source of truth; Redis is safe to lose — unaffected. Compose settings for Redis and Postgres are unchanged (`volatile-ttl`, `synchronous_commit=on`).
4. Arrival time and request count are never inputs in Fair mode — unaffected.
5. Bot detection never silently decides winners — unaffected.
6. Ground-truth labels never reach a backend decision path — **affected-but-preserved.** `fd` is a third project and must not import `api/` or `sim/`; `api/` and `sim/` remain separate uv projects with no shared decision code. The stable shard hash lives in `sim/` only.
7. Draw deterministic from a committed seed — unaffected.
8. Every entry state change is a guarded update — unaffected.

R1 is also unaffected and in fact restated: attribution logic moves from a shell hook into the same Python CLI, with the same patterns and the same forbidden `--no-verify`.

## Contract impact

None. No endpoint, request/response shape, status code, error code, `/me`, `/metrics`, `/export` or telemetry shape changes. One internal addition: the run manifest (Plan 18) records `event_loop`, host OS, and Docker backend/resources. This is an `sim/out` file, not the API contract.

## Plans edited

| Plan | Section | Summary of edit |
|---|---|---|
| `CLAUDE.md` (root and `docs/implementation/` copy) | new "Stack and platform rules (D-001)", repository map | binding rules for platforms, Docker-only, `fd` CLI, LF, versions, skills policy |
| `00_HOW_TO_USE` | Setup | prerequisites (git, Docker, uv), `fd` replaces Make, OS notes pointer, skills policy pointer |
| 01–19 | §0 standing rules | one-line pointer to the D-001 stack rules |
| 01 | §1, §3, §4.1–4.7, §4.10–4.11, §5, §6, §7, §8 | Make to `fd` CLI, host prerequisites, no symlink, compose ulimits/volumes/polling, LF files, hook shims, Node 22, CI matrix, `docs/PLATFORMS.md` |
| 02 | §3, §4.1, §7 | `fd up`/`fd migrate`, dbmate as a container, schema dump produced in-container with LF |
| 03 | §4.9, §4.10 | `fd openapi`; tests run through `fd test-api` in the container |
| 04, 06, 08, 09 | verification sections | "via curl" now says `curl.exe` in Windows PowerShell (plain `curl` is an alias there) |
| 14 | §8 | isolation check is a Python script run by `fd lint` and CI, so it works on every OS |
| 15 | §3, §4.13, §6 | Node 22 container, polling file-watch, Playwright location, `pnpm` runs inside the web container |
| 16 | §8 | how e2e is invoked; screenshots written to the bind-mounted `docs/screens/` |
| 18 | §3, §4, §5.1, §7, §8, §9, §10 | native + container modes, spawn-safe stable shard hash, optional uvloop, per-OS notes replace ulimit, allowlist adds `host.docker.internal`, manifest records loop and host info, CI smoke on Windows/macOS |
| 19 | §6, §7, §8, §9, §11 | record host OS and Docker resources with every number, chaos via `docker compose`, simulator laptop may be Windows or macOS, `fd demo-reset`, fresh-clone check on both OSes |

## Rollback

Revert this record's commit. Because no code exists yet, rollback is only a documentation revert. Once Plan 01 has been implemented, rolling back would mean restoring a Makefile, a Linux/macOS-only assumption, and the old Node version, which I would not recommend; supersede the specific piece with D-002 instead.
