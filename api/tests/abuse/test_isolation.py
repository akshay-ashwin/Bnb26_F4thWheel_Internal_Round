"""Invariant 6: decision code never reads simulator ground truth.

Scans api/app/abuse (limiter, risk, config, hooks, dev stub) for anything that could read a
label, an actor id, the ground-truth file or a `sim:*` Redis key. No `sim:` key appears here at
all: the dev stub keeps telemetry in memory, and in the real backend only the admin service
(`app/services/admin.py`) touches `sim:*` (see tests/test_admin.py).
"""

from __future__ import annotations

import re
from pathlib import Path

ABUSE = Path(__file__).resolve().parents[2] / "app" / "abuse"
BAD = re.compile(r"ground_truth|\bactor_id\b|\blabel\b|is_bot", re.IGNORECASE)
SIM_KEY = re.compile(r"""["']sim:""")


def test_decision_code_never_reads_ground_truth() -> None:
    hits = []
    files = sorted(ABUSE.rglob("*.py"))
    assert files
    for p in files:
        for i, line in enumerate(p.read_text(encoding="utf-8").splitlines(), 1):
            if BAD.search(line) or SIM_KEY.search(line):
                hits.append(f"{p.name}:{i}: {line.strip()}")
    assert hits == [], hits
