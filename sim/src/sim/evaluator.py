"""Fairness evaluator: joins the backend export with the simulator's private ground truth.

  sim eval runs/<run>                          -> runs/<run>/scorecard.json
  sim compare --fifo runs/a --fair runs/b      -> side-by-side scorecard

Headline metrics (design section 9):
  advantage ratio A = (W_bot/E_bot) / (W_human/E_human), per identity; ~1 is fair
  bot seat share vs bot entry share; hypergeometric 95% chance band for bot seats
  Spearman(arrival order, won): strongly negative = speed wins; ~0 = speed is irrelevant
Primary question: can a genuine user still enter and claim while bots attack?
  genuine users with exactly one entry, genuine request first-try success, genuine 429 rate,
  429s that carried Retry-After and whose retry succeeded, genuine winners who confirmed
"""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any

STEP_UP_SCORE = 60


def load_ndjson(p: Path) -> list[dict[str, Any]]:
    if not p.exists():
        return []
    return [json.loads(line) for line in p.read_text(encoding="utf-8").splitlines() if line.strip()]


def div(a: float, b: float) -> float | None:
    return a / b if b else None


def _lcomb(n: int, k: int) -> float:
    return math.lgamma(n + 1) - math.lgamma(k + 1) - math.lgamma(n - k + 1)


def hypergeom_band(
    total: int, bot_entries: int, seats: int, mass: float = 0.95
) -> dict[str, Any] | None:
    """Central 95% interval of bot seats if seats were drawn uniformly from all entries."""
    if total <= 0 or seats <= 0:
        return None
    seats = min(seats, total)
    lo_k, hi_k = max(0, seats - (total - bot_entries)), min(seats, bot_entries)
    denom = _lcomb(total, seats)
    pmf = {
        k: math.exp(_lcomb(bot_entries, k) + _lcomb(total - bot_entries, seats - k) - denom)
        for k in range(lo_k, hi_k + 1)
    }
    tail = (1 - mass) / 2
    acc, lo = 0.0, lo_k
    for k in range(lo_k, hi_k + 1):
        acc += pmf[k]
        if acc > tail:
            lo = k
            break
    acc, hi = 0.0, hi_k
    for k in range(hi_k, lo_k - 1, -1):
        acc += pmf[k]
        if acc > tail:
            hi = k
            break
    return {"mean": round(seats * bot_entries / total, 2), "lo95": lo, "hi95": hi}


def _ranks(xs: list[float]) -> list[float]:
    order = sorted(range(len(xs)), key=lambda i: xs[i])
    ranks = [0.0] * len(xs)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and xs[order[j + 1]] == xs[order[i]]:
            j += 1
        for k in range(i, j + 1):
            ranks[order[k]] = (i + j) / 2 + 1
        i = j + 1
    return ranks


def spearman(xs: list[float], ys: list[float]) -> float | None:
    if len(xs) < 3 or len(set(ys)) < 2 or len(set(xs)) < 2:
        return None
    rx, ry = _ranks(xs), _ranks(ys)
    mx, my = sum(rx) / len(rx), sum(ry) / len(ry)
    cov = sum((a - mx) * (b - my) for a, b in zip(rx, ry, strict=True))
    vx = math.sqrt(sum((a - mx) ** 2 for a in rx))
    vy = math.sqrt(sum((b - my) ** 2 for b in ry))
    return round(cov / (vx * vy), 3) if vx and vy else None


def evaluate(run: Path) -> dict[str, Any]:
    gt = load_ndjson(run / "ground_truth.ndjson")
    export = load_ndjson(run / "export.ndjson")
    humans = load_ndjson(run / "humans.ndjson")
    cs = json.loads((run / "client_stats.json").read_text(encoding="utf-8"))
    integrity = json.loads((run / "integrity.json").read_text(encoding="utf-8"))
    meta = json.loads((run / "run_meta.json").read_text(encoding="utf-8"))
    by_pub = {g["user_public_id"]: g for g in gt if g.get("user_public_id")}

    rows = [
        dict(
            r,
            _label=by_pub[r["user_public_id"]]["label"],
            _actor=by_pub[r["user_public_id"]]["actor_id"],
        )
        for r in export
        if r.get("user_public_id") in by_pub
    ]
    E = {"human": 0, "bot": 0}
    W = {"human": 0, "bot": 0}
    entries_per_identity: dict[str, int] = {}
    for r in rows:
        E[r["_label"]] += 1
        W[r["_label"]] += 1 if r.get("seat_no") else 0
        entries_per_identity[r["user_public_id"]] = (
            entries_per_identity.get(r["user_public_id"], 0) + 1
        )
    seats = W["human"] + W["bot"]
    p_bot, p_hum = div(W["bot"], E["bot"]), div(W["human"], E["human"])
    adv = round(p_bot / p_hum, 3) if p_bot is not None and p_hum else None
    band = hypergeom_band(E["bot"] + E["human"], E["bot"], seats) if E["bot"] else None
    if band is not None:
        band["bot_seats"] = W["bot"]
        band["inside"] = band["lo95"] <= W["bot"] <= band["hi95"]

    timed = sorted((r for r in rows if r.get("entered_at")), key=lambda r: r["entered_at"])
    rho = spearman(
        [float(i) for i in range(len(timed))], [1.0 if r.get("seat_no") else 0.0 for r in timed]
    )

    # ---------------- genuine users (the primary question)
    lab = cs["by_label"]
    hum_c, bot_c = lab.get("human", {}), lab.get("bot", {})
    h_ids = [h for h in humans if h.get("logged_in")]
    one_entry = sum(1 for h in h_ids if entries_per_identity.get(h["user_public_id"]) == 1)
    req = sum(h.get("requests", 0) for h in humans)
    first_ok = sum(h.get("first_try_ok", 0) for h in humans)
    rl = sum(h.get("rate_limited", 0) for h in humans)
    rl_ra = sum(h.get("rate_limited_with_retry_after", 0) for h in humans)
    retry_ok = sum(h.get("retried_after_429_ok", 0) for h in humans)
    retry_fail = sum(h.get("retried_after_429_failed", 0) for h in humans)
    offered = [h for h in humans if h.get("offered")]
    human_winners = [
        r
        for r in rows
        if r["_label"] == "human"
        and r.get("rank") is not None
        and r["rank"] <= int(meta["scenario"].get("capacity", 500))
    ]
    users = len(humans)
    entered = sum(
        1 for h in humans if entries_per_identity.get(h.get("user_public_id") or "", 0) >= 1
    )
    users_429 = sum(1 for h in humans if h.get("rate_limited", 0) > 0)
    step_passed = sum(1 for h in humans if str(h.get("step_up") or "").startswith("passed"))
    step_failed = sum(1 for h in humans if str(h.get("step_up") or "").startswith("failed"))
    fair_mode = meta["mode"] == "fair"
    winners_confirmed = sum(1 for r in human_winners if r.get("seat_no"))
    genuine = {
        # denominators are ALL simulated genuine users, including anyone blocked before entering
        "users_attempted": users,
        "logged_in": len(h_ids),
        "entered": entered,
        "with_exactly_one_entry": one_entry,
        "entry_success_rate": div(one_entry, users),
        "users_with_any_429": users_429,
        "users_429_rate": div(users_429, users),
        "requests": req,
        "first_try_success_rate": div(first_ok, req),
        "rate_limited_responses": rl,
        "rate_limited_rate": div(rl, req),
        "rate_limited_with_retry_after": rl_ra,
        "retry_after_429_succeeded": retry_ok,
        "retry_after_429_still_failing": retry_fail,
        "retry_after_header_missing": cs.get("retry_after_missing", {}).get("human", 0),
        "server_errors_5xx": hum_c.get("server_error", 0),
        "transport_errors": hum_c.get("transport_error", 0),
        "offered_or_fifo_token": len(offered),
        "seats_confirmed": sum(1 for h in humans if h.get("seat_no")),
        "fair_winners": len(human_winners) if fair_mode else None,
        "fair_winners_confirmed": winners_confirmed if fair_mode else None,
        "claim_success_rate": (
            div(winners_confirmed, len(human_winners))
            if fair_mode
            else div(sum(1 for h in humans if h.get("seat_no")), len(offered))
        ),
        "step_up_required": step_passed + step_failed,
        "step_up_passed": step_passed,
        "step_up_failed": step_failed,
        "dropped_users": users - len(h_ids),
        "network_switched": sum(1 for h in humans if h.get("network_switched")),
        "network_switched_dropped": sum(
            1
            for h in humans
            if h.get("network_switched")
            and entries_per_identity.get(h.get("user_public_id") or "", 0) != 1
        ),
    }

    # ---------------- farming detection vs ground truth (threshold = step-up score)
    flagged = [r for r in rows if (r.get("risk_score") or 0) >= STEP_UP_SCORE]
    tp = sum(1 for r in flagged if r["_label"] == "bot")
    fp = len(flagged) - tp
    farm_rows = [r for r in rows if by_pub[r["user_public_id"]].get("kind") == "farm"]
    detection = {
        "flagged_entries": len(flagged),
        "precision": div(tp, len(flagged)),
        "recall_all_bots": div(tp, E["bot"]),
        "recall_farm": div(
            sum(1 for r in farm_rows if (r.get("risk_score") or 0) >= STEP_UP_SCORE), len(farm_rows)
        ),
        "human_false_positive_rate": div(fp, E["human"]),
        "human_flagged": fp,
        "network_switch_users_flagged": sum(
            1
            for r in rows
            if r["_label"] == "human"
            and by_pub[r["user_public_id"]].get("alt_ip")
            and (r.get("risk_score") or 0) >= STEP_UP_SCORE
        ),
    }

    actors: dict[str, dict[str, Any]] = {}
    for g in gt:
        a = actors.setdefault(
            g["actor_id"], {"identities": 0, "clients": 0, "entries": 0, "seats": 0}
        )
        a["identities"] += 1
        a["clients"] += g.get("clients", 1)
    for r in rows:
        actors[r["_actor"]]["entries"] += 1
        actors[r["_actor"]]["seats"] += 1 if r.get("seat_no") else 0
    for name, a in actors.items():
        a["max_entries_per_identity"] = max(
            (
                entries_per_identity.get(g["user_public_id"], 0)
                for g in gt
                if g["actor_id"] == name and g.get("user_public_id")
            ),
            default=0,
        )

    return {
        "scenario": meta["scenario"]["name"],
        "mode": meta["mode"],
        "sim_seed": meta["sim_seed"],
        "capacity": meta["scenario"].get("capacity"),
        "genuine": genuine,
        "bots": {
            "requests": bot_c.get("requests", 0),
            "rate_limited": bot_c.get("rate_limited", 0),
            "rejection_rate": div(bot_c.get("rate_limited", 0), bot_c.get("requests", 0)),
            "token_rejected": bot_c.get("token_rejected", 0),
            "replay_successes": cs.get("replay_successes", 0),
            "forged_successes": cs.get("forged_successes", 0),
            "requests_per_seat": div(bot_c.get("requests", 0), W["bot"]),
            "identities": cs.get("bot_identities"),
            "identities_with_account": cs.get("bot_identities_with_account"),
        },
        "fairness": {
            "entries": E,
            "seats": W,
            "bot_entry_share": div(E["bot"], E["bot"] + E["human"]),
            "bot_seat_share": div(W["bot"], seats),
            "advantage_ratio": adv,
            "chance_band": band,
            "arrival_vs_win_spearman": rho,
        },
        "detection": detection,
        "integrity": {
            "oversold": integrity.get("oversold"),
            "duplicate_seats": integrity.get("duplicate_entries_with_seats"),
            "invariant_ok": integrity.get("invariant_ok"),
            "sold": integrity.get("sold"),
        },
        "latency_ms": cs.get("latency_ms", {}),
        "achieved_load": cs.get("achieved", {}),
        "actors": actors,
    }


def _f(x: Any, nd: int = 3) -> str:
    if x is None:
        return "-"
    if isinstance(x, float):
        return f"{x:.{nd}f}"
    return str(x)


ROWS: list[tuple[str, Any]] = [
    (
        "genuine users attempted / entered",
        lambda s: f"{s['genuine']['users_attempted']} / {s['genuine']['entered']}",
    ),
    (
        "genuine entry success (exactly 1 entry)",
        lambda s: _f(s["genuine"]["entry_success_rate"], 4),
    ),
    (
        "genuine users who saw a 429",
        lambda s: f"{s['genuine']['users_with_any_429']} ({_f(s['genuine']['users_429_rate'], 4)})",
    ),
    ("genuine first-try success", lambda s: _f(s["genuine"]["first_try_success_rate"], 4)),
    ("genuine 429 rate", lambda s: _f(s["genuine"]["rate_limited_rate"], 4)),
    (
        "genuine 429 -> retry ok / still failing",
        lambda s: (
            f"{s['genuine']['retry_after_429_succeeded']} / "
            f"{s['genuine']['retry_after_429_still_failing']}"
        ),
    ),
    (
        "genuine seats confirmed / claim success",
        lambda s: f"{s['genuine']['seats_confirmed']} / {_f(s['genuine']['claim_success_rate'])}",
    ),
    (
        "genuine step-up required / passed / failed",
        lambda s: (
            f"{s['genuine']['step_up_required']} / {s['genuine']['step_up_passed']} / "
            f"{s['genuine']['step_up_failed']}"
        ),
    ),
    (
        "genuine 5xx / transport errors",
        lambda s: f"{s['genuine']['server_errors_5xx']} / {s['genuine']['transport_errors']}",
    ),
    (
        "fair winners confirmed",
        lambda s: (
            f"{_f(s['genuine']['fair_winners_confirmed'])} / {_f(s['genuine']['fair_winners'])}"
        ),
    ),
    (
        "genuine p95 / p99 ms",
        lambda s: (
            f"{s['latency_ms'].get('human', {}).get('p95')} / "
            f"{s['latency_ms'].get('human', {}).get('p99')}"
        ),
    ),
    (
        "bot identities / got an account (L6)",
        lambda s: (
            f"{_f(s['bots'].get('identities'))} / {_f(s['bots'].get('identities_with_account'))}"
        ),
    ),
    ("bot requests", lambda s: _f(s["bots"]["requests"])),
    ("bot rejection rate", lambda s: _f(s["bots"]["rejection_rate"], 4)),
    (
        "bot entry share / seat share",
        lambda s: f"{_f(s['fairness']['bot_entry_share'])} / {_f(s['fairness']['bot_seat_share'])}",
    ),
    ("advantage ratio A", lambda s: _f(s["fairness"]["advantage_ratio"])),
    (
        "bot seats / 95% chance band",
        lambda s: (
            f"{s['fairness']['seats']['bot']} / "
            + (
                f"{s['fairness']['chance_band']['lo95']}-{s['fairness']['chance_band']['hi95']}"
                if s["fairness"]["chance_band"]
                else "-"
            )
        ),
    ),
    ("arrival-vs-win Spearman", lambda s: _f(s["fairness"]["arrival_vs_win_spearman"])),
    (
        "flagged / human FPR / farm recall",
        lambda s: (
            f"{s['detection']['flagged_entries']} / "
            f"{_f(s['detection']['human_false_positive_rate'])}"
            f" / {_f(s['detection']['recall_farm'])}"
        ),
    ),
    (
        "replay / forged successes",
        lambda s: f"{s['bots']['replay_successes']} / {s['bots']['forged_successes']}",
    ),
    (
        "oversold / duplicate seats",
        lambda s: f"{s['integrity']['oversold']} / {s['integrity']['duplicate_seats']}",
    ),
    (
        "achieved avg / peak req/s",
        lambda s: f"{s['achieved_load'].get('avg_rps')} / {s['achieved_load'].get('peak_rps')}",
    ),
]


def table(cards: list[dict[str, Any]]) -> str:
    head = "| metric | " + " | ".join(f"{c['scenario']} {c['mode']}" for c in cards) + " |"
    sep = "|---|" + "---:|" * len(cards)
    lines = [head, sep] + [
        f"| {name} | " + " | ".join(fn(c) for c in cards) + " |" for name, fn in ROWS
    ]
    return "\n".join(lines)


def sweep_report(cards: list[dict[str, Any]]) -> dict[str, Any]:
    """Identity share vs seat share per run, plus a small SVG with the proportional diagonal."""
    points = []
    for c in cards:
        f = c["fairness"]
        band = f.get("chance_band") or {}
        points.append(
            {
                "mode": c["mode"],
                "attacker_identities": c["actors"].get("SYBIL", {}).get("identities", 0),
                "identity_share": f.get("bot_entry_share") or 0.0,
                "seat_share": f.get("bot_seat_share") or 0.0,
                "attacker_seats": f["seats"]["bot"],
                "seats_total": f["seats"]["bot"] + f["seats"]["human"],
                "advantage_ratio": f.get("advantage_ratio"),
                "chance_band": [band.get("lo95"), band.get("hi95")] if band else None,
                "inside_band": band.get("inside") if band else None,
            }
        )
    return {"points": points, "svg": _svg(points)}


def _svg(points: list[dict[str, Any]]) -> str:
    w = h = 360
    pad = 40

    def xy(x: float, y: float) -> tuple[float, float]:
        return pad + x * (w - 2 * pad), h - pad - y * (h - 2 * pad)

    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{w}" height="{h}" '
        f'font-family="sans-serif" font-size="11">',
        f'<rect width="{w}" height="{h}" fill="white"/>',
    ]
    x0, y0 = xy(0, 0)
    x1, y1 = xy(1, 1)
    parts.append(
        f'<line x1="{x0}" y1="{y0}" x2="{x1}" y2="{y1}" stroke="#999" stroke-dasharray="4 3"/>'
    )
    parts.append(f'<line x1="{x0}" y1="{y0}" x2="{x1}" y2="{y0}" stroke="#333"/>')
    parts.append(f'<line x1="{x0}" y1="{y0}" x2="{x0}" y2="{y1}" stroke="#333"/>')
    parts.append(
        f'<text x="{w / 2}" y="{h - 8}" text-anchor="middle">attacker identity share '
        f"of entries</text>"
    )
    parts.append(
        f'<text x="12" y="{h / 2}" transform="rotate(-90 12 {h / 2})" '
        f'text-anchor="middle">attacker seat share</text>'
    )
    colors = {"fifo": "#c0392b", "fair": "#2471a3"}
    for p in points:
        cx, cy = xy(min(1.0, p["identity_share"]), min(1.0, p["seat_share"]))
        parts.append(f'<circle cx="{cx:.1f}" cy="{cy:.1f}" r="4" fill="{colors[p["mode"]]}"/>')
    parts.append(f'<text x="{pad + 6}" y="{pad - 20}" fill="#c0392b">FIFO</text>')
    parts.append(f'<text x="{pad + 46}" y="{pad - 20}" fill="#2471a3">Fair</text>')
    parts.append(f'<text x="{x1 - 70}" y="{y1 + 14}" fill="#777">proportional</text>')
    parts.append("</svg>")
    return "\n".join(parts)
