# Platform notes: macOS and Windows 11

macOS and Windows 11 are both first-class. Linux runs the containers (and CI). Everything runs through Docker Compose; the host needs only **git**, **Docker** (Compose v2.20 or newer) and **uv**. No Python, Node or Make on the host.

> **Verification status.** Plan 01 was verified end to end on **Windows 11 (Docker Desktop, Linux containers, WSL2 backend)**. It has **not yet been run on macOS**. Everything below marked macOS is written from the design (same compose file, same `fd` CLI) and is an open item for the first teammate with a Mac: run the commands in "First start" and report in the Plan 01 review log.

## Prerequisites

| | macOS | Windows 11 |
| --- | --- | --- |
| git | `brew install git` or Xcode CLT | Git for Windows (ships `sh`, which the git hooks need) |
| Docker | Docker Desktop for Mac (Apple Silicon or Intel; images are multi-arch) | Docker Desktop in **Linux-container mode**, WSL2 backend recommended |
| uv | `brew install uv` or the install script from docs.astral.sh/uv | `winget install --id=astral-sh.uv -e` or the install script |

## First start

Run from the repo root (any subfolder works; `fd` finds the root itself).

macOS:

```sh
uv run fd doctor      # tools, Docker mode, free ports, cloud-sync folder, hooks
uv run fd secrets     # writes .env with random secrets and LF line endings
uv run fd hooks       # installs the git hooks (once per clone)
uv run fd up          # starts everything and waits until healthy
```

Windows (PowerShell):

```powershell
uv run fd doctor
uv run fd secrets
uv run fd hooks
uv run fd up
```

Measured on the Windows machine used for Plan 01 (16 CPUs, about 12 GB given to Docker): `uv run fd up` took **40 s** with images already built and fresh volumes. A first run that has to pull and build images takes longer (one cold run took 84 s before failing on a port conflict, see below). Treat "healthy within 60 s" as true for a warm machine, not for a first pull on a slow network.

Check the same-origin path (browser origin to API through the dev proxy):

macOS:

```sh
curl -s http://127.0.0.1:5173/api/healthz
```

Windows (PowerShell). In Windows PowerShell 5.1 plain `curl` is an alias for `Invoke-WebRequest`, so use `curl.exe` or `Invoke-RestMethod`:

```powershell
curl.exe -s http://127.0.0.1:5173/api/healthz
Invoke-RestMethod http://127.0.0.1:5173/api/healthz
```

Both print `{"status":"ok","server_time":"..."}`.

## Ports

| Variable | Default | Notes |
| --- | --- | --- |
| `POSTGRES_PORT` | **15432** | Remapped from 5432. A locally installed PostgreSQL very often owns 5432 (it did on the Windows machine we tested, and Docker then failed with "ports are not available"). Containers still talk to Postgres on 5432 inside the network. |
| `REDIS_PORT` | 6379 | |
| `API_PORT` | 8000 | |
| `WEB_PORT` | 5173 | Vite dev server |
| `WEB_PROD_PORT` | 8080 | nginx, compose profile `prod` |
| `BIND_ADDRESS` | 127.0.0.1 | API and web bind address. Set `0.0.0.0` to let another laptop (for example the simulator) reach them. Postgres and Redis always bind 127.0.0.1. |

A port can be unusable without anything listening on it: on Windows, Hyper-V and WSL reserve ranges. Check with `netsh int ipv4 show excludedportrange protocol=tcp`. `uv run fd doctor` tests every port by binding it, which catches both a listener and a reserved range on both OSes. Change a port in `.env` and run `fd doctor` again.

## Line endings (LF always)

`.gitattributes` (`* text=auto eol=lf`) overrides `core.autocrlf`, so Windows checkouts get LF. `uv run fd lint` fails if a tracked text file contains a CR byte, and `fd doctor` checks `.env`. A CR inside `.env`, an entrypoint or SQL breaks Linux containers in confusing ways (a trailing `\r` ends up inside every secret).

Windows PowerShell 5.1 trap: `Set-Content`, `Out-File` and `>` write CRLF or a BOM. When you must write a file by hand, use `[IO.File]::WriteAllText($path, $text, (New-Object System.Text.UTF8Encoding $false))` and make sure the text uses `` `n``. `fd secrets` writes `.env` correctly. (This trap was hit once during Plan 01 and caught by `fd doctor`.)

## Cloud-synced folders (OneDrive, iCloud Drive, Dropbox)

Keep the repository **outside** them (for example `C:\dev\...` or `~/dev/...`). Sync clients lock files, can corrupt `.git`, and fight with Docker bind mounts (files-on-demand placeholders do not behave like real files). As a second line of defence `node_modules`, the Python virtualenvs and Postgres data live in **Docker named volumes**, never bind mounts. `fd doctor` warns when the repo is inside a synced folder.

## Docker Desktop settings

- **Windows:** WSL2 backend. Give the VM enough memory with `%UserProfile%\.wslconfig`, for example `[wsl2]` then `memory=8GB` and `processors=8`, then `wsl --shutdown` and restart Docker Desktop. Postgres is tuned for about 2 GB (`shared_buffers = 512MB`); raise it in `infra/postgres/postgresql.conf` on a bigger VM.
- **macOS:** Docker Desktop, Settings, Resources: at least 4 CPUs and 6 GB for the full stack plus a simulator run.
- **Both:** the Linux VM's CPU and memory cap the whole stack, and published ports pass through a proxy layer. Record these values with every performance number you report.
- File-change events do not cross a Windows or macOS bind mount reliably, so dev mode uses polling (Vite `watch.usePolling`, uvicorn `--reload` with `WATCHFILES_FORCE_POLLING=true`). For performance runs set `API_RELOAD=false` in `.env`.

## Simulator tuning (details in Plan 18 and `sim/README.md`)

The simulator runs in a container (`uv run fd sim ...`) or natively (`uv run fd sim ... --native`, which is `uv run --project sim sim ...`). It needs no uvloop (the optional `fast` extra is installed in the container only). Plan 01 verified the native placeholder runs on Windows without uvloop.

- **Compose / Linux containers:** `ulimits.nofile` is set in the compose file (65535) and behaves the same on both OSes.
- **macOS native:** `ulimit -n`, `launchctl limit maxfiles`, `sysctl kern.maxfilesperproc`; widen the ephemeral range with `sysctl net.inet.ip.portrange.first`.
- **Windows native:** there is no `ulimit`; the limit that bites is the dynamic port range, widened with `netsh int ipv4 set dynamicport tcp ...` from an administrator shell, plus optionally `TcpTimedWaitDelay`.

## Troubleshooting

| Symptom | Cause and fix |
| --- | --- |
| `failed to connect to the docker API ... dockerDesktopLinuxEngine` | Docker Desktop is not running. Start it and wait for "Engine running". `fd doctor` reports it. |
| `ports are not available: exposing port TCP 127.0.0.1:5432` | A local PostgreSQL (or a reserved range) holds the port. Pick another `POSTGRES_PORT` in `.env`. The default is already 15432. |
| `.env is missing: run uv run fd secrets first` | You skipped `fd secrets`. |
| `Compose v2 >= 2.20 required` | Update Docker Desktop. The root `compose.yaml` uses `include`. |
| `pnpm install` fails with "... was published at ..., within the minimumReleaseAge cutoff" | pnpm 12 refuses packages published within the last day. Pin the previous version in `web/package.json` (and regenerate `pnpm-lock.yaml`) rather than turning the safeguard off. |
| `typescript-eslint does not support TS 7.0` | `web/package.json` pins TypeScript `~6.0.3` until typescript-eslint supports 7.x. |
| Hook says `uv is not installed or not on PATH` | The hooks fail closed on purpose. Install uv, then `uv run fd doctor`. |
| A script fails with `bad interpreter` or `\r` in an error | A CR byte got in. `uv run fd lint` names the file. |
