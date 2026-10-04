"""ASGI middleware that runs the limiter before routing.

A reject becomes the contract error envelope `429 RATE_LIMITED` with `retry_after_ms`, a
`Retry-After` header in whole seconds (rounded up, so honouring either value is enough), and
`server_time`. Nothing on this path touches Postgres.
"""

from __future__ import annotations

import json
import math
import os
from collections import Counter
from http.cookies import SimpleCookie
from typing import Any

from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.abuse.config import ConfigStore
from app.abuse.limiter import (
    Decision,
    Limiter,
    RequestKey,
    endpoint_group,
    net24_of,
    session_id_from_token,
)
from app.clock import server_time

SESSION_COOKIE = "fd_session"


def sim_mode() -> bool:
    return os.environ.get("SIM_MODE", "false").strip().lower() in ("1", "true", "yes")


def _headers(scope: Scope) -> dict[str, str]:
    return {k.decode("latin-1"): v.decode("latin-1") for k, v in scope.get("headers", [])}


def session_token(headers: dict[str, str]) -> str | None:
    """Cookie first, then `Authorization: Bearer` (contract conventions)."""
    raw = headers.get("cookie")
    if raw and SESSION_COOKIE in raw:
        c: SimpleCookie = SimpleCookie()
        try:
            c.load(raw)
        except Exception:  # noqa: BLE001 - a malformed cookie is simply no session
            c = SimpleCookie()
        if SESSION_COOKIE in c:
            return c[SESSION_COOKIE].value
    auth = headers.get("authorization", "")
    if auth[:7].lower() == "bearer ":
        return auth[7:].strip() or None
    return None


def client_ip(scope: Scope, headers: dict[str, str]) -> str:
    """`X-Sim-Client-IP` is honoured only when SIM_MODE=true (test-harness source IP)."""
    if sim_mode():
        sim_ip = headers.get("x-sim-client-ip")
        if sim_ip:
            return sim_ip.strip()
    client = scope.get("client")
    return str(client[0]) if client else "0.0.0.0"  # noqa: S104 - a placeholder, not a bind address


def reject_response(d: Decision) -> tuple[int, list[tuple[bytes, bytes]], bytes]:
    ms = int(d.retry_after_ms or 1000)
    body = json.dumps(
        {
            "error": {
                "code": d.code or "RATE_LIMITED",
                "message": "Slow down",
                "retry_after_ms": ms,
            },
            "server_time": server_time(),
        }
    ).encode()
    headers = [
        (b"content-type", b"application/json"),
        (b"retry-after", str(max(1, math.ceil(ms / 1000))).encode()),
        (b"x-ratelimit-layer", (d.layer or "").encode()),
        (b"content-length", str(len(body)).encode()),
    ]
    return 429, headers, body


class AbuseMiddleware:
    def __init__(
        self,
        app: ASGIApp,
        *,
        limiter: Limiter,
        config: ConfigStore,
        outcomes: Counter[str] | None = None,
    ) -> None:
        self.app, self.limiter, self.config = app, limiter, config
        self.outcomes: Counter[str] = outcomes if outcomes is not None else Counter()

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        group = endpoint_group(scope["method"], scope["path"])
        if group is None:
            await self.app(scope, receive, send)
            return
        if self.config.due():
            await self.config.refresh()
        cfg = self.config.current()
        headers = _headers(scope)
        ip = client_ip(scope, headers)
        tok = session_token(headers)
        sid = session_id_from_token(tok, self.limiter.session_secret) if tok else None
        uid = None
        if sid is not None and self.limiter.user_resolver is not None:
            uid = await self.limiter.user_resolver(sid)
        decision = await self.limiter.check(RequestKey(group, ip, net24_of(ip), sid, uid), cfg)
        if decision.outcome == "allow":
            self.outcomes["accepted"] += 1
            scope.setdefault("state", {})["abuse_session_id"] = sid
            await self.app(scope, receive, send)
            return
        self.outcomes["rate_limited"] += 1
        self.outcomes[f"rate_limited:{decision.layer}"] += 1
        status, hdrs, body = reject_response(decision)
        start: Message = {"type": "http.response.start", "status": status, "headers": hdrs}
        await send(start)
        await send({"type": "http.response.body", "body": body})


def install_abuse(app: Any, limiter: Limiter, config: ConfigStore) -> Counter[str]:
    """Add the middleware to a Starlette/FastAPI app; returns its live outcome counters."""
    outcomes: Counter[str] = Counter()
    app.add_middleware(AbuseMiddleware, limiter=limiter, config=config, outcomes=outcomes)
    return outcomes
