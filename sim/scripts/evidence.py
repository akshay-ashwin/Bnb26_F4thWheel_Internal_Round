"""Reproducible evidence runs (Windows PowerShell and macOS alike; standard library only).

  python sim/scripts/evidence.py <suite> [--target devstub|URL]

Suites: normal, genuine_retry, bot_flood, identity_farm, campus, network_switch, flash_crowd,
        repeated_attempts, multi_tab, token_replay, flash_crowd_50k (not in `all`),
        claim_stampede, identity_budget_sweep, final, all
        real_smoke, real_main, real_final   (REAL backend only: --target http://api:8000 --reset)

Every run starts from clean backend state: with the default `--target devstub` the dev stub
container is recreated (one worker: its state is in memory) on its own Redis database, which is
flushed first. Same-seed reruns would otherwise inherit the previous run's OTP counters. With
`--target http://api:8000` the runs go to the real backend instead (no restart: reset it yourself).

Run outputs land in sim/out/ (gitignored); the summary scorecard is sim/out/scorecard.{json,md}.
Needs the stack up (`uv run fd up`) and a `.env` (`uv run fd secrets`).
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
STUB = "fairdrop-devstub"
STUB_URL = f"http://{STUB}:8001"
STUB_REDIS_DB = "2"
MODES = ("fifo", "fair")
RESET_REAL = False  # --reset: clean real-backend state before every run
REAL_SEEDS = (101, 202, 303)


def docker() -> str:
    found = shutil.which("docker")
    if found:
        return found
    for cand in (
        r"C:\Program Files\Docker\Docker\resources\bin\docker.exe",
        "/usr/local/bin/docker",
        "/opt/homebrew/bin/docker",
    ):
        if Path(cand).exists():
            return cand
    sys.exit("docker not found; install Docker Desktop and run `uv run fd doctor`")


def sh(args: list[str], check: bool = True, quiet: bool = False) -> int:
    print("+", " ".join(args[1:]) if args and "docker" in args[0] else " ".join(args), flush=True)
    res = subprocess.run(
        args,
        cwd=ROOT,
        check=False,  # noqa: S603 - argument list, no shell
        stdout=subprocess.DEVNULL if quiet else None,
        stderr=subprocess.DEVNULL if quiet else None,
    )
    if check and res.returncode != 0:
        sys.exit(f"command failed ({res.returncode})")
    return res.returncode


def fresh_stub() -> None:
    d = docker()
    sh([d, "rm", "-f", STUB], check=False, quiet=True)
    sh(
        [d, "compose", "exec", "-T", "redis", "redis-cli", "-n", STUB_REDIS_DB, "flushdb"],
        quiet=True,
    )
    sh(
        [
            d,
            "compose",
            "run",
            "-d",
            "--name",
            STUB,
            "--no-deps",
            "-e",
            f"REDIS_URL=redis://redis:6379/{STUB_REDIS_DB}",
            "api",
            "sh",
            "-c",
            "uv sync --frozen --no-install-project -q && exec uvicorn app.abuse.devstub.app:app "
            "--host 0.0.0.0 --port 8001 --workers 1 --no-access-log --log-level warning",
        ],
        quiet=True,
    )
    probe = "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8001/api/healthz')"
    for _ in range(60):
        if sh([d, "exec", STUB, "/opt/venv/bin/python", "-c", probe], check=False, quiet=True) == 0:
            return
        time.sleep(1)
    sys.exit("dev stub did not become healthy")


def fresh_real() -> None:
    """Clean real-backend state: new Postgres volume (re-migrated) and a new Redis container.
    Without this a same-seed rerun inherits users, OTP counters and cluster sets."""
    uv = shutil.which("uv") or sys.exit("uv not found (needed for `uv run fd reset-db`)")
    sh([uv, "run", "fd", "reset-db"], quiet=True)
    sh([docker(), "compose", "up", "-d", "--wait", "api"], quiet=True)


def api_health(out: str, since: str) -> None:
    """Count backend log lines by level and the failure kinds we care about, for one run."""
    res = subprocess.run(  # noqa: S603 - argument list, no shell
        [docker(), "compose", "logs", "--no-color", "--since", since, "api"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    lines = res.stdout.splitlines()
    low = [x.lower() for x in lines]
    # Access-log lines: every status >= 400 is logged (2xx are sampled), so 5xx counts are exact.
    status_by_route: dict[str, int] = {}
    for x in lines:
        i = x.find("{")
        try:
            d = json.loads(x[i:]) if i >= 0 else {}
        except ValueError:
            continue
        if isinstance(d.get("status"), int) and d["status"] >= 500:
            k = f"{d['status']} {d.get('route')}"
            status_by_route[k] = status_by_route.get(k, 0) + 1
    summary = {
        "server_errors_by_route": status_by_route,
        "since": since,
        "lines": len(lines),
        "error": sum('"level": "error"' in x for x in low),
        "warning": sum('"level": "warning"' in x for x in low),
        "traceback": sum("traceback" in x for x in low),
        "redis_mentions": sum("redis" in x and '"level": "info"' not in x for x in low),
        "pool_or_postgres": sum(
            ("pool" in x or "postgres" in x or "asyncpg" in x) and '"level": "info"' not in x
            for x in low
        ),
        "rescored": [x.split('"msg": ')[-1][:80] for x in lines if "rescored" in x],
        "samples": [x[:300] for x in lines if '"level": "error"' in x.lower()][:5],
    }
    path = ROOT / "sim" / out / "api_health.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(summary, indent=2), encoding="utf-8")


def sim(args: list[str]) -> int:
    return sh(
        [
            docker(),
            "compose",
            "run",
            "--rm",
            "--no-deps",
            "sim",
            "sh",
            "-c",
            "uv sync --frozen -q && sim " + " ".join(args),
        ],
        check=False,
    )


def run(target: str, scenario: str, mode: str, out: str, extra: list[str] | None = None) -> str:
    if target == "devstub":
        fresh_stub()
    elif RESET_REAL:
        fresh_real()
    base = STUB_URL if target == "devstub" else target
    since = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    sim(
        [
            "run",
            f"scenarios/{scenario}.toml",
            "--mode",
            mode,
            "--base-url",
            base,
            "--out",
            out,
            *(extra or []),
        ]
    )
    if target != "devstub":
        api_health(out, since)
    return out


def compare(runs: list[str], name: str) -> None:
    sim(["compare", *runs, "--out", f"out/{name}.json", "--md", f"out/{name}.md"])


SCENARIOS = (
    "normal",
    "genuine_retry",
    "bot_flood",
    "identity_farm",
    "campus",
    "network_switch",
    "flash_crowd",
    "repeated_attempts",
    "multi_tab",
    "token_replay",
)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "suite",
        choices=[
            *SCENARIOS,
            "flash_crowd_50k",
            "network_switch_60s",
            "claim_stampede",
            "identity_budget_sweep",
            "final",
            "all",
            "real_smoke",
            "real_main",
            "real_final",
        ],
    )
    ap.add_argument("--target", default="devstub", help="'devstub' or a base URL")
    ap.add_argument(
        "--reset",
        action="store_true",
        help="real backend only: `fd reset-db` + recreate api before every run",
    )
    a = ap.parse_args()
    global RESET_REAL  # noqa: PLW0603 - script-level switch
    RESET_REAL = a.reset
    t = a.target
    base = STUB_URL if t == "devstub" else t

    def pair(name: str) -> list[str]:
        out = [run(t, name, m, f"out/{name}_{m}") for m in MODES]
        compare(out, f"scorecard_{name}")
        return out

    if a.suite in SCENARIOS or a.suite in ("flash_crowd_50k", "network_switch_60s"):
        pair(a.suite)
    elif a.suite == "claim_stampede":
        for m in MODES:
            if t == "devstub":
                fresh_stub()
            sim(
                [
                    "stampede",
                    "--mode",
                    m,
                    "--users",
                    "400",
                    "--capacity",
                    "100",
                    "--base-url",
                    base,
                    "--out",
                    f"out/stampede_{m}",
                ]
            )
    elif a.suite == "identity_budget_sweep":
        runs = []
        for n in (0, 500, 2000, 5000, 10000):
            for m in MODES:
                runs.append(
                    run(t, "identity_budget", m, f"out/sweep_{n}_{m}", ["--identities", str(n)])
                )
        compare(runs, "scorecard_sweep")
        sim(["sweep-report", *runs, "--out", "out/sweep.json"])
    elif a.suite == "final":
        runs = []
        for seed in (101, 202, 303):
            for m in MODES:
                runs.append(
                    run(t, "genuine_retry", m, f"out/final_{seed}_{m}", ["--seed", str(seed)])
                )
        compare(runs, "scorecard_final")
    elif a.suite == "real_smoke":
        if t == "devstub":
            sys.exit("real_smoke needs --target http://api:8000")
        if RESET_REAL:
            fresh_real()
        return sim(["smoke", "--base-url", t, "--out", "out/real_smoke"])
    elif a.suite in ("real_main", "real_final"):
        if t == "devstub":
            sys.exit(f"{a.suite} needs --target http://api:8000 (this suite is real-backend only)")
        seeds = REAL_SEEDS[:1] if a.suite == "real_main" else REAL_SEEDS
        runs = [
            run(t, "genuine_retry_real", m, f"out/real_{s}_{m}", ["--seed", str(s)])
            for s in seeds
            for m in MODES
        ]
        compare(runs, f"scorecard_{a.suite}")
    else:  # all
        runs = []
        for name in SCENARIOS:
            runs += pair(name)
        compare(runs, "scorecard")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
