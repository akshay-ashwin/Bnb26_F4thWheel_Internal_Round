# Fair Drop

A ticket/seat "drop" system: 500 seats, up to 50,000 humans, and attackers with up to 10,000 bot clients. The claim we set out to prove:

> A client's chance of a seat depends only on how many distinct verified identities it controls, never on how fast or how often it sends requests.

One backend with two switchable modes (FIFO = the unfair "before", Fair = Verified Entry Window + Provable Draw = the "after"), an attack simulator that uses the same public API, an evaluator that produces a fairness scorecard, and a user app plus judge dashboard. Design: [`docs/design/Fair_Drop_Architecture.md`](docs/design/Fair_Drop_Architecture.md).

## Quick start

You need **git**, **Docker** (Compose v2.20+; Docker Desktop in Linux-container mode on Windows) and **uv**. Nothing else. Per-OS details and troubleshooting: [`docs/PLATFORMS.md`](docs/PLATFORMS.md).

```
uv run fd doctor     # pre-flight: tools, Docker mode, ports, hooks
uv run fd secrets    # write .env with random secrets (LF endings)
uv run fd hooks      # install git hooks (once per clone)
uv run fd up         # start everything, wait until healthy
```

Then open http://127.0.0.1:5173 (web) and http://127.0.0.1:5173/api/healthz (API through the same origin). The same commands work in macOS Terminal and Windows PowerShell.

## Tasks (`uv run fd <task>`, replaces Make)

| Task | What it does |
| --- | --- |
| `up [--build]` | `docker compose up -d --wait` (needs `.env`) |
| `down` | stop the stack; named volumes (database, node_modules, venvs) are kept |
| `logs [service]` | follow container logs |
| `migrate` | apply `api/migrations` (idempotent), set the `fairdrop_app` password, dump `api/migrations/schema.sql` |
| `reset-db` | remove the Postgres volume and migrate again |
| `test-api` | migrate `fairdrop_test`, then run the api tests in the container (extra pytest arguments after `--`) |
| `test-web [--e2e]` | web tests in the container (`--e2e` adds Playwright, Plan 15) |
| `lint [--staged]` | no-CR check, then ruff, mypy, eslint, tsc and prettier on host tools and in containers. `--staged` runs only the fast no-CR check |
| `fmt` | ruff format and prettier |
| `hooks` | set `core.hooksPath=infra/git-hooks` (repo-local) |
| `secrets [--force]` | generate `.env` from `.env.example`; refuses to overwrite without `--force` |
| `sim --scenario <name> --mode <fifo\|fair> [--native]` | run an attack scenario (Plan 18) |
| `eval --run <id> [--native]` | evaluate a run (Plan 19) |
| `demo-reset` | one-command demo reset (Plan 19) |
| `openapi` | export the OpenAPI snapshot (Plan 03) |
| `doctor` | pre-flight checks for this machine |
| `attribution-check ...` | the Rule R1 guard used by the hooks and CI |

Tasks owned by later plans print `not implemented yet (Plan NN)` until then. The CLI lives in [`tools/fd/`](tools/fd/README.md) and uses only the standard library.

## Layout

| Path | What |
| --- | --- |
| `api/` | FastAPI backend (Python 3.12), runs only in a Linux container |
| `web/` | React + Vite + TypeScript + Tailwind (Node 24 LTS in its container) |
| `sim/` | attack simulator and evaluator; container or native on Windows/macOS |
| `tools/fd/` | the `uv run fd` task CLI |
| `infra/` | Docker Compose, Postgres and Redis config, git hooks |
| `docs/` | design, contract, glossary, decisions, plans, review logs, platform notes |

## Rules of the repository

- [`CLAUDE.md`](CLAUDE.md) is the standing instruction file (invariants, rules R1 to R4, stack rules). It exists only at the repo root.
- [`docs/CONTRIBUTING.md`](docs/CONTRIBUTING.md): hooks, commits, and the no-AI-attribution rule.
- [`docs/GLOSSARY.md`](docs/GLOSSARY.md): the words we use.
- [`docs/contract/README.md`](docs/contract/README.md): the API contract (frozen at Plan 03).
- [`docs/decisions/`](docs/decisions/README.md): Deviation Records. [`docs/review-logs/`](docs/review-logs/README.md): one plain-language log per plan.
