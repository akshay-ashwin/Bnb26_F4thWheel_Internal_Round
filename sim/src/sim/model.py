"""Simulator-side data. GROUND-TRUTH RULE: `label` and `actor_id` exist only here and in the
run folder's private files. `Api` (api.py) builds every request from the session token, device,
IP and user agent only, so there is no code path that puts a label on the wire."""

from __future__ import annotations

import os
import time
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from typing import Any


@dataclass
class Identity:
    identity_id: str
    label: str  # "human" | "bot"        (never sent)
    actor_id: str  # attacker group or "human"  (never sent)
    kind: str  # behaviour name, e.g. "human", "flood", "farm"  (never sent)
    phone: str
    device_id: str
    ip: str
    user_agent: str
    arrival_s: float = 0.0
    clients: int = 1
    alt_ip: str | None = None  # network_switch: the mobile-data IP used after login
    network: str = "home"  # "home" | "campus" | "cgnat" (never sent; for the evaluator)
    session_token: str | None = None
    public_id: str | None = None
    entered: bool = False
    seat_no: int | None = None
    # per-identity outcome log for genuine-user metrics
    log: dict[str, Any] = field(default_factory=dict)


class Stats:
    """Per-label request outcomes and client-observed latency (kept in the simulator only)."""

    def __init__(self) -> None:
        self.c: dict[str, Counter[str]] = defaultdict(Counter)
        self.lat: dict[str, list[float]] = defaultdict(list)
        self.per_second: Counter[int] = Counter()
        self.retry_after_missing: Counter[str] = Counter()
        # SIM_TRACE=1 keeps every request (t, label, endpoint, status, code, ms) for debugging
        self.trace: list[tuple[float, str, str, int, str | None, float]] | None = (
            [] if os.environ.get("SIM_TRACE") == "1" else None
        )

    def record(
        self,
        label: str,
        endpoint: str,
        status: int,
        code: str | None,
        ms: float,
        has_retry_after: bool,
    ) -> None:
        c = self.c[label]
        c["requests"] += 1
        c[f"req:{endpoint}"] += 1
        self.per_second[int(time.time())] += 1
        if status == 429:
            c["rate_limited"] += 1
            c[f"rate_limited:{endpoint}"] += 1
            if not has_retry_after:
                self.retry_after_missing[label] += 1
        elif status == 0:
            c["transport_error"] += 1
        elif status >= 500:
            c["server_error"] += 1
        elif status == 401 and code == "TOKEN_INVALID":
            c["token_rejected"] += 1
        elif 200 <= status < 300:
            c["success"] += 1
        else:
            c["denied"] += 1
            c[f"denied:{code}"] += 1
        if self.trace is not None:
            self.trace.append((round(time.time(), 3), label, endpoint, status, code, round(ms, 1)))
        if len(self.lat[label]) < 500_000:
            self.lat[label].append(ms)


def percentiles(values: list[float]) -> dict[str, float]:
    if not values:
        return {}
    s = sorted(values)

    def q(p: float) -> float:
        return round(s[min(len(s) - 1, int(p * len(s)))], 1)

    return {"p50": q(0.50), "p95": q(0.95), "p99": q(0.99), "max": round(s[-1], 1), "n": len(s)}
