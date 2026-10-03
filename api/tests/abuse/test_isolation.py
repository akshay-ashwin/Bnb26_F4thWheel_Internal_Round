"""Invariant 6: decision code never reads simulator ground truth.

Scans api/app/abuse (limiter, risk, config, dev stub) for anything that could read a label, an
actor id, the ground-truth file or a `sim:*` Redis key. The only `sim:` key allowed is the dev
stub's write-only telemetry sink (presentation data; nothing reads it back).
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
            if BAD.search(line):
                hits.append(f"{p.name}:{i}: {line.strip()}")
            if SIM_KEY.search(line) and not (p.name == "app.py" and ".set(" in line):
                hits.append(f"{p.name}:{i}: {line.strip()}")
    assert hits == [], hits


def test_only_one_sim_key_write_exists() -> None:
    writes = [
        line
        for p in ABUSE.rglob("*.py")
        for line in p.read_text(encoding="utf-8").splitlines()
        if SIM_KEY.search(line)
    ]
    assert len(writes) == 1 and ".set(" in writes[0]
