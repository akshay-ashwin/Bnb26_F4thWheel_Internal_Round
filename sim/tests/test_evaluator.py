"""Evaluator maths and population generation."""

from __future__ import annotations

import json
import math
import random
from pathlib import Path
from typing import Any

from sim.evaluator import evaluate, hypergeom_band, spearman
from sim.runner import load_scenario, make_population

SCENARIOS = Path(__file__).resolve().parents[1] / "scenarios"


def test_chance_band_matches_design_example() -> None:
    # design section 9: 2,000 bot identities in 52,000 entries, 500 seats -> mean ~19, band ~11-27
    band = hypergeom_band(52_000, 2_000, 500)
    assert band is not None
    assert math.isclose(band["mean"], 19.23, abs_tol=0.01)
    assert 10 <= band["lo95"] <= 12 and 26 <= band["hi95"] <= 28


def test_chance_band_edges() -> None:
    assert hypergeom_band(0, 0, 10) is None
    band = hypergeom_band(100, 100, 10)
    assert band is not None and band["lo95"] == band["hi95"] == 10  # everyone is a bot


def test_spearman() -> None:
    xs = [float(i) for i in range(10)]
    assert spearman(xs, [1.0] * 5 + [0.0] * 5) is not None
    rho = spearman(xs, [1.0] * 5 + [0.0] * 5)
    assert rho is not None and rho < -0.8  # early arrivals win -> strongly negative
    assert spearman(xs, [1.0] * 10) is None  # no variation in outcome


def test_all_scenarios_parse_and_build() -> None:
    for path in SCENARIOS.glob("*.toml"):
        sc = load_scenario(path)
        pop = make_population(sc, random.Random(1))  # noqa: S311 - test data
        assert pop, path.name
        assert len({p.identity_id for p in pop}) == len(pop)


def _write(
    run: Path,
    gt: list[dict[str, Any]],
    export: list[dict[str, Any]],
    humans: list[dict[str, Any]],
    mode: str = "fair",
) -> None:
    run.mkdir()
    (run / "ground_truth.ndjson").write_text("\n".join(json.dumps(g) for g in gt))
    (run / "export.ndjson").write_text("\n".join(json.dumps(r) for r in export))
    (run / "humans.ndjson").write_text("\n".join(json.dumps(h) for h in humans))
    (run / "client_stats.json").write_text(json.dumps({"by_label": {}, "latency_ms": {}}))
    (run / "integrity.json").write_text(json.dumps({"oversold": 0}))
    (run / "run_meta.json").write_text(
        json.dumps({"scenario": {"name": "t", "capacity": 2}, "mode": mode, "sim_seed": 1})
    )


def test_advantage_and_genuine_denominator_include_blocked_users(tmp_path: Path) -> None:
    gt = [
        {
            "identity_id": f"h{i}",
            "label": "human",
            "actor_id": "human",
            "kind": "human",
            "user_public_id": f"uh{i}",
            "clients": 1,
        }
        for i in range(4)
    ]
    gt += [
        {
            "identity_id": f"b{i}",
            "label": "bot",
            "actor_id": "A",
            "kind": "farm",
            "user_public_id": f"ub{i}",
            "clients": 1,
        }
        for i in range(4)
    ]
    export = [
        {"user_public_id": "uh0", "entered_at": "1", "rank": 1, "seat_no": 1},
        {"user_public_id": "uh1", "entered_at": "2", "rank": 3},
        {"user_public_id": "ub0", "entered_at": "3", "rank": 2, "seat_no": 2},
        {"user_public_id": "ub1", "entered_at": "4", "rank": 4, "risk_score": 65},
    ]
    humans = [
        {
            "user_public_id": f"uh{i}",
            "logged_in": i < 3,
            "requests": 4,
            "first_try_ok": 4,
            "seat_no": 1 if i == 0 else None,
        }
        for i in range(4)
    ]
    _write(tmp_path / "r", gt, export, humans)
    card = evaluate(tmp_path / "r")
    g = card["genuine"]
    assert g["users_attempted"] == 4 and g["entered"] == 2
    assert g["entry_success_rate"] == 0.5  # blocked users stay in the denominator
    assert card["fairness"]["advantage_ratio"] == 1.0  # 1/2 bots vs 1/2 humans
    assert card["detection"]["precision"] == 1.0 and card["detection"]["recall_farm"] == 0.5
