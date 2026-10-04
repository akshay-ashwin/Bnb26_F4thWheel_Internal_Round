"""L1-L3 request limiter interface. DEFAULT: allow everything. Saanvi replaces `check`."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from starlette.requests import Request


@dataclass(frozen=True)
class Decision:
    """`outcome` is "allow" or "reject". A reject names the `layer` ("L1".."L8") that said no, the
    contract error `code` (normally RATE_LIMITED) and an optional `retry_after_ms`."""

    outcome: Literal["allow", "reject"] = "allow"
    layer: str | None = None
    code: str | None = None
    retry_after_ms: int | None = None


ALLOW = Decision()

# Paths that never go through the limiter (docs/contract: health, admin, simulator telemetry).
SKIPPED_PREFIXES = ("/api/healthz", "/api/readyz", "/api/admin/", "/api/sim/telemetry")


def is_skipped(path: str) -> bool:
    return path.startswith(SKIPPED_PREFIXES)


async def check(request: Request) -> Decision:
    """Decide whether this request may proceed. `request.app.state` exposes `settings`, `cache`
    (Redis with circuit breaker) and `metrics`. Default implementation allows everything."""
    return ALLOW
