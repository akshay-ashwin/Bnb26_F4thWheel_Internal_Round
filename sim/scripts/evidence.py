"""Reproducible evidence runs (Windows PowerShell and macOS alike; standard library only).

  python sim/scripts/evidence.py <suite> [--target devstub|URL]

Suites: normal, genuine_retry, bot_flood, identity_farm, campus, network_switch, flash_crowd,
        repeated_attempts, multi_tab, token_replay, flash_crowd_50k (not in `all`),
        claim_stampede, identity_budget_sweep, final, all

Every run starts from clean backend state: with the default `--target devstub` the dev stub
container is recreated (one worker: its state is in memory) on its own Redis database, which is
flushed first. Same-seed reruns would otherwise inherit the previous run's OTP counters. With
`--target http://api:8000` the runs go to the real backend instead (no restart: reset it yourself).

Run outputs land in sim/out/ (gitignored); the summary scorecard is sim/out/scorecard.{json,md}.
Needs the stack up (`uv run fd up`) and a `.env` (`uv run fd secrets`).
"""

from __future__ import annotations

import argparse
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
    base = STUB_URL if target == "devstub" else target
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
            "claim_stampede",
            "identity_budget_sweep",
            "final",
            "all",
        ],
    )
    ap.add_argument("--target", default="devstub", help="'devstub' or a base URL")
    a = ap.parse_args()
    t = a.target
    base = STUB_URL if t == "devstub" else t

    def pair(name: str) -> list[str]:
        out = [run(t, name, m, f"out/{name}_{m}") for m in MODES]
        compare(out, f"scorecard_{name}")
        return out

    if a.suite in SCENARIOS or a.suite == "flash_crowd_50k":
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
    else:  # all
        runs = []
        for name in SCENARIOS:
            runs += pair(name)
        compare(runs, "scorecard")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
