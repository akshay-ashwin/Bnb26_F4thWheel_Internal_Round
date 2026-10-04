"""Admin read models: integrity, metrics, export, simulator telemetry, abuse configuration.

These are read-only presentation modules. `sim:*` keys are touched ONLY here (invariant 6): no
decision module (auth, entries, draw, claim, tokens, sweeper, step-up, abuse) imports this file.
"""

from __future__ import annotations

import json
import time
import uuid
from collections.abc import AsyncIterator
from typing import Any

import asyncpg
from pydantic_core import to_jsonable_python

from app.cache import Cache, RedisUnavailableError
from app.clock import server_time
from app.errors import NotFound, ServiceUnavailable, ValidationFailed
from app.metrics import LATENCY_BUCKETS_MS
from app.schemas.admin import AbuseConfigIn, MetricsOut, RpsPoint
from app.schemas.sim import TelemetryIn

EXTRA_FIELDS = (
    "capacity",
    "allocations_count",
    "sold_without_allocation",
    "allocation_without_sold_seat",
    "entries_allocated_count",
    "entries_allocated_mismatch",
    "sold_seat_entry_not_allocated",
    "free_seat_with_sold_at",
)


async def integrity(pool: asyncpg.Pool, drop_id: uuid.UUID) -> dict[str, Any]:
    """Computed by SQL on every call (never counters): the canonical `v_drop_integrity` view."""
    row = await pool.fetchrow("SELECT * FROM v_drop_integrity WHERE drop_id = $1", drop_id)
    if row is None:
        raise NotFound("Drop not found")
    return {
        "seats_total": row["seats_total"],
        "sold": row["sold"],
        "free": row["free"],
        "oversold": row["oversold"],
        "duplicate_entries_with_seats": row["duplicate_entries_with_seats"],
        "invariant_ok": row["invariant_ok"],
        "extra": {k: row[k] for k in EXTRA_FIELDS},
    }


# --- metrics ---

_DB_CACHE: dict[uuid.UUID, tuple[float, dict[str, Any]]] = {}
_DB_TTL_S = 1.0


async def _db_counts(pool: asyncpg.Pool, drop_id: uuid.UUID) -> dict[str, Any]:
    hit = _DB_CACHE.get(drop_id)
    if hit and time.monotonic() - hit[0] < _DB_TTL_S:
        return hit[1]
    row = await pool.fetchrow(
        "SELECT d.phase, d.mode, d.run_no, d.capacity, v.sold, v.free, v.oversold, v.invariant_ok,"
        "  (SELECT count(*) FROM entries e WHERE e.drop_id = d.id) AS entries,"
        "  (SELECT count(*) FROM entries e WHERE e.drop_id = d.id"
        "     AND e.offered_at IS NOT NULL) AS offers,"
        "  (SELECT count(*) FROM entries e WHERE e.drop_id = d.id"
        "     AND e.status = 'ALLOCATED') AS allocated,"
        "  (SELECT count(*) FROM entries e WHERE e.drop_id = d.id AND e.risk_score > 0) AS flagged,"
        "  (SELECT count(*) FROM sessions s WHERE s.revoked_at IS NULL"
        "     AND s.created_at > now() - interval '24 hours') AS active_sessions"
        " FROM drops d JOIN v_drop_integrity v ON v.drop_id = d.id WHERE d.id = $1",
        drop_id,
    )
    if row is None:
        raise NotFound("Drop not found")
    out = dict(row)
    _DB_CACHE[drop_id] = (time.monotonic(), out)
    return out


def _percentile(buckets: dict[str, int], p: float) -> float:
    counts = [(float(b), buckets.get(f"lat:{b}", 0)) for b in LATENCY_BUCKETS_MS]
    counts.append((float(LATENCY_BUCKETS_MS[-1]) * 2, buckets.get("lat:inf", 0)))
    total = sum(c for _, c in counts)
    if total == 0:
        return 0.0
    target = max(1.0, p * total)
    seen = 0
    low = 0.0
    for bound, count in counts:
        if count and seen + count >= target:
            return round(low + (bound - low) * (target - seen) / count, 2)
        seen += count
        low = bound
    return float(counts[-1][0])


async def metrics(
    pool: asyncpg.Pool, cache: Cache, drop_id: uuid.UUID, window_s: int, dropped: int
) -> MetricsOut:
    counts = await _db_counts(pool, drop_id)
    now = int(time.time())
    seconds = list(range(now - window_s + 1, now + 1))
    per_sec: list[dict[str, str]] = [{} for _ in seconds]
    try:

        async def read(r: Any) -> list[dict[str, str]]:
            pipe = r.pipeline(transaction=False)
            for sec in seconds:
                pipe.hgetall(f"m:{sec}")
            result: list[dict[str, str]] = await pipe.execute()
            return result

        per_sec = await cache.run(read)
    except RedisUnavailableError:
        pass

    def series(name: str) -> list[int]:
        return [int(d.get(name, 0)) for d in per_sec]

    totals: dict[str, int] = {}
    layers: set[str] = set()
    for d in per_sec:
        for k, v in d.items():
            totals[k] = totals.get(k, 0) + int(v)
            if k.startswith("rate_limited:"):
                layers.add(k.split(":", 1)[1])
    req_total = totals.get("req_total", 0)
    lat = {k: v for k, v in totals.items() if k.startswith("lat:")}
    return MetricsOut(
        rps_series=[
            RpsPoint(t=sec, total=int(d.get("req_total", 0)))
            for sec, d in zip(seconds, per_sec, strict=True)
        ],
        outcomes_series={
            "accepted": series("accepted"),
            "rate_limited": series("rate_limited"),
            "token_rejected": series("token_rejected"),
            "duplicate": series("duplicate"),
        },
        latency={
            "p50": _percentile(lat, 0.5),
            "p95": _percentile(lat, 0.95),
            "p99": _percentile(lat, 0.99),
        },  # type: ignore[arg-type]
        error_rate=round(totals.get("errors_5xx", 0) / req_total, 6) if req_total else 0.0,
        active_sessions=counts["active_sessions"],
        entries=counts["entries"],
        offers=counts["offers"],
        allocated=counts["allocated"],
        remaining=counts["free"],
        flagged_entries=counts["flagged"],
        step_ups={  # type: ignore[arg-type]
            "issued": totals.get("step_up_issued", 0),
            "passed": totals.get("step_up_passed", 0),
            "failed": totals.get("step_up_failed", 0),
        },
        phase=counts["phase"],
        mode=counts["mode"],
        run_no=counts["run_no"],
        capacity=counts["capacity"],
        oversold=counts["oversold"],
        invariant_ok=counts["invariant_ok"],
        claims_ok=totals.get("claims_ok", 0),
        claims_sold_out=totals.get("claims_sold_out", 0),
        blocked_requests=totals.get("rate_limited", 0) + totals.get("token_rejected", 0),
        throttled_requests=totals.get("rate_limited", 0),
        duplicate_requests=totals.get("duplicate", 0),
        rate_limited_by_layer={layer: series(f"rate_limited:{layer}") for layer in sorted(layers)},
        window_s=window_s,
        metrics_dropped=dropped,
    )


# --- export (NDJSON) ---

EXPORT_SQL = """
SELECT u.public_id AS user_public_id, e.id AS entry_id, e.entered_at, e.risk_score, e.risk_flags,
       e.draw_rank AS rank, e.status, s.seat_no, e.run_no, e.offered_at, e.allocated_at,
       e.step_up_passed_at
FROM entries e
JOIN users u ON u.id = e.user_id
LEFT JOIN seats s ON s.entry_id = e.id AND s.drop_id = e.drop_id
WHERE e.drop_id = $1
ORDER BY e.entered_at, e.id
"""


def _iso(value: Any) -> str | None:
    return to_jsonable_python(value) if value is not None else None


async def export_rows(pool: asyncpg.Pool, drop_id: uuid.UUID) -> AsyncIterator[bytes]:
    """One JSON object per line, streamed through a server-side cursor (no full materialisation).
    No phone, phone hash, IP or device leaves this endpoint."""
    async with pool.acquire() as conn, conn.transaction():
        await conn.execute("SET LOCAL statement_timeout = 60000")
        async for r in conn.cursor(EXPORT_SQL, drop_id, prefetch=1000):
            row: dict[str, Any] = {
                "user_public_id": r["user_public_id"],
                "entry_id": str(r["entry_id"]),
                "entered_at": _iso(r["entered_at"]),
                "risk_score": r["risk_score"],
                "risk_flags": r["risk_flags"],
                "rank": r["rank"],
                "status": r["status"],
            }
            if r["seat_no"] is not None:
                row["seat_no"] = r["seat_no"]
            row.update(
                run_no=r["run_no"],
                offered_at=_iso(r["offered_at"]),
                allocated_at=_iso(r["allocated_at"]),
                step_up_passed_at=_iso(r["step_up_passed_at"]),
            )
            yield (json.dumps(row, separators=(",", ":")) + "\n").encode()


# --- simulator telemetry ('sim:*' lives ONLY here) ---

SIM_LATEST = "sim:latest"
SIM_SERIES = "sim:series"


async def record_telemetry(cache: Cache, body: TelemetryIn) -> None:
    payload = json.dumps({**body.model_dump(), "received_at": server_time()})

    async def write(r: Any) -> None:
        pipe = r.pipeline(transaction=False)
        pipe.set(SIM_LATEST, payload, ex=3600)
        pipe.lpush(SIM_SERIES, payload)
        pipe.ltrim(SIM_SERIES, 0, 599)
        pipe.expire(SIM_SERIES, 3600)
        await pipe.execute()

    try:
        await cache.run(write)
    except RedisUnavailableError:
        raise ServiceUnavailable("Telemetry store unavailable", retry_after_ms=1000) from None


async def latest_telemetry(cache: Cache) -> dict[str, Any] | None:
    try:
        raw = await cache.run(lambda r: r.get(SIM_LATEST))
    except RedisUnavailableError:
        return None
    return json.loads(raw) if raw else None


# --- abuse configuration (Saanvi's limiter reads it) ---

ABUSE_KEY = "abuse_config"
ABUSE_REDIS_KEY = "abuse:config"
LAYERS = {f"L{i}" for i in range(1, 9)}


async def put_abuse_config(pool: asyncpg.Pool, cache: Cache, body: AbuseConfigIn) -> dict[str, Any]:
    unknown = set(body.layers) - LAYERS
    if unknown:
        raise ValidationFailed(f"Unknown layers: {sorted(unknown)}")
    config = {"layers": body.layers, "thresholds": body.thresholds}
    await pool.execute(
        "INSERT INTO app_settings (key, value) VALUES ($1, $2)"
        " ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, updated_at = now()",
        ABUSE_KEY,
        config,
    )
    try:  # the limiter reads this key per request; Postgres stays the source of truth
        await cache.run(lambda r: r.set(ABUSE_REDIS_KEY, json.dumps(config)))
    except RedisUnavailableError:
        pass
    return config


async def get_abuse_config(pool: asyncpg.Pool) -> dict[str, Any]:
    value = await pool.fetchval("SELECT value FROM app_settings WHERE key = $1", ABUSE_KEY)
    return value or {"layers": {}, "thresholds": {}}


async def list_drops(pool: asyncpg.Pool) -> list[dict[str, Any]]:
    rows = await pool.fetch(
        "SELECT id, name, mode, phase, run_no, capacity FROM drops ORDER BY created_at"
    )
    return [{**dict(r), "id": str(r["id"])} for r in rows]
