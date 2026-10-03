"""Simulator entry point.

sim run <scenario.toml> --mode fair|fifo [--base-url URL] [--out DIR] [--telemetry]
sim eval <run dir>
sim compare <run dir> [<run dir> ...] [--out scorecard.json]
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from collections.abc import Sequence
from pathlib import Path

from sim import __version__


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="sim")
    ap.add_argument("--version", action="store_true")
    sub = ap.add_subparsers(dest="cmd")
    r = sub.add_parser("run")
    r.add_argument("scenario")
    r.add_argument("--mode", choices=["fair", "fifo"], required=True)
    r.add_argument("--base-url", default=os.environ.get("API_BASE_URL", "http://localhost:8000"))
    r.add_argument("--admin-key", default=os.environ.get("ADMIN_KEY", ""))
    r.add_argument("--out", default=None)
    r.add_argument("--seed", type=int, default=None, help="override the scenario seed")
    r.add_argument(
        "--identities",
        type=int,
        default=None,
        help="override the first attacker's identity count (0 = no attacker)",
    )
    r.add_argument(
        "--telemetry",
        action="store_true",
        help="post presentation-only counts to /api/sim/telemetry",
    )
    st = sub.add_parser("stampede")
    st.add_argument("--mode", choices=["fair", "fifo"], required=True)
    st.add_argument("--users", type=int, default=400)
    st.add_argument("--capacity", type=int, default=100)
    st.add_argument("--base-url", default=os.environ.get("API_BASE_URL", "http://localhost:8000"))
    st.add_argument("--admin-key", default=os.environ.get("ADMIN_KEY", ""))
    st.add_argument("--out", default=None)
    sw = sub.add_parser("sweep-report")
    sw.add_argument("runs", nargs="+")
    sw.add_argument("--out", default="out/sweep.json")
    e = sub.add_parser("eval")
    e.add_argument("run")
    c = sub.add_parser("compare")
    c.add_argument("runs", nargs="+")
    c.add_argument("--out", default=None, help="write scorecard.json here")
    c.add_argument("--md", default=None, help="write scorecard.md here")
    a = ap.parse_args(list(argv) if argv is not None else sys.argv[1:])

    if a.cmd is None:
        print(f"fairdrop-sim {__version__}")
        return 0
    if a.cmd == "run":
        from sim.runner import load_scenario, run

        sc = load_scenario(a.scenario)
        if a.seed is not None:
            sc["seed"] = a.seed
        if a.identities is not None:
            if a.identities == 0:
                sc["attackers"] = []
            else:
                sc["attackers"][0]["identities"] = a.identities
        out = Path(a.out or f"out/{sc['name']}_{a.mode}")
        key = os.environ.get("SIM_TELEMETRY_KEY") if a.telemetry else None
        asyncio.run(run(sc, a.mode, a.base_url, out, a.admin_key, key))
        return 0

    if a.cmd == "stampede":
        from sim.stampede import stampede

        out = Path(a.out or f"out/stampede_{a.mode}")
        rep = asyncio.run(stampede(a.base_url, a.admin_key, a.mode, a.users, a.capacity, out))
        return 0 if rep["pass"] else 1

    from sim.evaluator import evaluate, table

    if a.cmd == "sweep-report":
        from sim.evaluator import sweep_report

        rep = sweep_report([evaluate(Path(p)) for p in a.runs])
        out = Path(a.out)
        out.write_text(json.dumps(rep["points"], indent=2), encoding="utf-8")
        out.with_suffix(".svg").write_text(rep["svg"], encoding="utf-8")
        rows = ["mode,attacker_identities,identity_share,seat_share,advantage_ratio,inside_band"]
        rows += [
            f"{p['mode']},{p['attacker_identities']},{p['identity_share']:.4f},"
            f"{p['seat_share']:.4f},{p['advantage_ratio']},{p['inside_band']}"
            for p in rep["points"]
        ]
        out.with_suffix(".csv").write_text("\n".join(rows) + "\n", encoding="utf-8")
        print("\n".join(rows))
        return 0
    if a.cmd == "eval":
        card = evaluate(Path(a.run))
        (Path(a.run) / "scorecard.json").write_text(json.dumps(card, indent=2), encoding="utf-8")
        print(table([card]))
        return 0
    cards = [evaluate(Path(p)) for p in a.runs]
    if a.out:
        Path(a.out).write_text(json.dumps({"runs": cards}, indent=2), encoding="utf-8")
    md = table(cards)
    if a.md:
        Path(a.md).write_text(md + "\n", encoding="utf-8")
    print(md)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
