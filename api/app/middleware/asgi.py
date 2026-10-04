"""Pure ASGI middleware (no BaseHTTPMiddleware: it costs latency and breaks error handling).

Order, outermost first:
  1. RequestContextMiddleware: X-Request-ID, timing, access log, the 500 safety net.
  2. AbuseLayersMiddleware: L1-L3 slot (no-op until Plan 12; must never touch Postgres).
  3. Starlette routing -> dependencies (session, admin, idempotency key) -> handler.

The safety net lives inside (1) because Starlette's own 500 handler runs outside user
middleware, so a 500 produced there would not carry the request id.
"""

import logging
import random
import re
import time
import uuid

from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.errors import internal_response
from app.observability import metrics

log = logging.getLogger("fairdrop.access")
_VALID_ID = re.compile(r"^[A-Za-z0-9._-]{8,64}$")


def route_template(scope: Scope) -> str:
    """`/api/drops/{drop_id}/me`, never the raw path (ids stay out of logs and metric labels).

    FastAPI nests included routers, so `route.path` lacks the `/api` prefix; rebuild the template
    from the real path by swapping each matched path parameter back to its name."""
    if scope.get("route") is None:
        return "(unmatched)"
    path: str = scope["path"]
    for name, value in scope.get("path_params", {}).items():
        path = path.replace(str(value), "{" + name + "}", 1)
    return path


class RequestContextMiddleware:
    def __init__(self, app: ASGIApp, *, sample_rate: float = 0.01, slow_ms: int = 250) -> None:
        self.app = app
        self.sample_rate = sample_rate
        self.slow_ms = slow_ms

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        incoming = dict(scope["headers"]).get(b"x-request-id", b"").decode("latin-1")
        request_id = incoming if _VALID_ID.match(incoming) else uuid.uuid4().hex
        scope.setdefault("state", {})["request_id"] = request_id
        started = time.perf_counter()
        status = 500
        response_started = False

        async def send_wrapper(message: Message) -> None:
            nonlocal status, response_started
            if message["type"] == "http.response.start":
                response_started = True
                status = message["status"]
                headers = list(message.get("headers", []))
                headers.append((b"x-request-id", request_id.encode()))
                message = {**message, "headers": headers}
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        except Exception:
            log.exception("unhandled error", extra={"request_id": request_id})
            status = 500
            if response_started:
                raise
            await internal_response(request_id)(scope, receive, send_wrapper)
        finally:
            latency_ms = (time.perf_counter() - started) * 1000
            route = route_template(scope)
            method = scope["method"]
            for hook in metrics.request_hooks:
                try:
                    hook(route, method, status, latency_ms)
                except Exception:  # a metrics bug must never break a request
                    log.exception("metrics hook failed")
            if status >= 400 or latency_ms >= self.slow_ms or random.random() < self.sample_rate:  # noqa: S311
                log.info(
                    "request",
                    extra={
                        "request_id": request_id,
                        "method": method,
                        "route": route,
                        "status": status,
                        "latency_ms": round(latency_ms, 1),
                        "user_public_id": scope.get("state", {}).get("user_public_id"),
                    },
                )


class AbuseLayersMiddleware:
    """Slot for L1-L3 (Plan 12). Pass-through now."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        await self.app(scope, receive, send)
