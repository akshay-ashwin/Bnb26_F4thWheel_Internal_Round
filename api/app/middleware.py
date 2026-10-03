"""Gateway middleware (pure ASGI, outermost app middleware).

Order for every HTTP request: request id and timer -> abuse slot (limiter `check`, skipped for
health/admin/telemetry; fails open) -> routing -> handler. A reject becomes the contract error
envelope with Retry-After. The abuse slot never touches Postgres.
"""

from __future__ import annotations

import logging
import time
import uuid

from starlette.requests import Request
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app import abuse
from app.abuse.limiter import is_skipped
from app.errors import envelope
from app.metrics import Metrics

log = logging.getLogger("fairdrop.gateway")


class GatewayMiddleware:
    def __init__(self, app: ASGIApp, metrics: Metrics) -> None:
        self.app = app
        self.metrics = metrics

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        started = time.perf_counter()
        request_id = uuid.uuid4().hex[:16]
        scope["fairdrop.request_id"] = request_id
        status = 500

        async def send_wrapper(message: Message) -> None:
            nonlocal status
            if message["type"] == "http.response.start":
                status = message["status"]
                headers = list(message.get("headers", []))
                headers.append((b"x-request-id", request_id.encode()))
                message["headers"] = headers
            await send(message)

        path: str = scope["path"]
        counted = path.startswith("/api/") and not path.startswith("/api/healthz")
        try:
            if not is_skipped(path):
                request = Request(scope, receive)
                try:
                    decision = await abuse.check(request)
                except Exception:
                    log.exception("limiter failed; failing open")
                    decision = abuse.Decision()
                if decision.outcome == "reject":
                    self.metrics.incr("rate_limited")
                    if decision.layer:
                        self.metrics.incr(f"rate_limited:{decision.layer}")
                    response = envelope(
                        decision.code or "RATE_LIMITED", "Slow down", decision.retry_after_ms
                    )
                    status = response.status_code
                    await response(scope, receive, send_wrapper)
                    return
            await self.app(scope, receive, send_wrapper)
        except Exception:
            log.exception("unhandled error request_id=%s", request_id)
            response = envelope("INTERNAL", "Internal error", request_id=request_id)
            status = response.status_code
            await response(scope, receive, send_wrapper)
        finally:
            if counted:
                elapsed_ms = (time.perf_counter() - started) * 1000
                self.metrics.incr("req_total")
                self.metrics.observe_latency(elapsed_ms)
                if status >= 500:
                    self.metrics.incr("errors_5xx")
