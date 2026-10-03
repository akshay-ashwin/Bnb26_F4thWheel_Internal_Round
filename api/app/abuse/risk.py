"""L6/L7 hooks. DEFAULTS are no-ops (score 0, no flags). Saanvi replaces the bodies."""

from __future__ import annotations

import asyncpg


async def otp_request_guard(
    *, phone_hash: str, phone_prefix: str, device_id: str, client_ip: str
) -> None:
    """Raise AppError("OTP_THROTTLED", retry_after_ms=...) to refuse an OTP request."""
    return None


async def on_identity_verified(
    *, user_id: str, device_id: str, client_ip: str, ua_hash: str, verify_latency_ms: int | None
) -> None:
    """Called after a successful verify (feeds cluster windows)."""
    return None


async def score_entry(
    *, drop_id: str, user_id: str, device_id: str | None, client_ip: str, ua_hash: str
) -> tuple[int, list[str]]:
    """Return (risk_score 0-100, risk_flags). Must be fast and Redis-only; never block entry."""
    return 0, []


async def rescore_eligible(conn: asyncpg.Connection, drop_id: str) -> None:
    """Recompute risk_score/risk_flags of every REGISTERED entry from final cluster sizes. Runs
    inside the draw transaction before scores are read."""
    return None
