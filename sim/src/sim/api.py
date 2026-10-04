"""Thin client for the public contract endpoints (docs/contract). Labels never go on the wire:
requests are built only from the identity's session token, device id, IP and user agent.

aiohttp, not httpx: measured from the sim container against the dev stub, httpx's async client
took 5-16 ms per sequential request versus 0.8 ms for aiohttp, so with httpx the simulator was
mostly measuring its own client overhead.
"""

from __future__ import annotations

import asyncio
import random
import time
import uuid
from collections.abc import Mapping
from typing import Any

import aiohttp

from sim.model import Identity, Stats


def make_session(base_url: str, max_connections: int, timeout_s: float) -> aiohttp.ClientSession:
    """One HTTP session shared by many identities. The cookie jar is a dummy: the backend sets
    the `fd_session` cookie, and a real shared jar would send one user's session with another
    user's requests (cookies take precedence over the bearer token)."""
    return aiohttp.ClientSession(
        base_url=base_url,
        connector=aiohttp.TCPConnector(limit=max_connections, limit_per_host=max_connections),
        cookie_jar=aiohttp.DummyCookieJar(),
        timeout=aiohttp.ClientTimeout(total=timeout_s),
    )


class Result:
    __slots__ = ("body", "code", "headers", "status")

    def __init__(self, status: int, body: dict[str, Any], headers: Mapping[str, str]) -> None:
        self.status, self.body, self.headers = status, body, headers
        err = body.get("error")
        self.code: str | None = err.get("code") if isinstance(err, dict) else None

    @property
    def ok(self) -> bool:
        return 200 <= self.status < 300

    def retry_after_s(self, default: float = 1.0) -> float:
        err = self.body.get("error")
        if isinstance(err, dict) and isinstance(err.get("retry_after_ms"), int | float):
            return max(0.05, float(err["retry_after_ms"]) / 1000)
        ra = self.headers.get("Retry-After")
        return float(ra) if ra and ra.isdigit() else default


class Api:
    def __init__(self, session: aiohttp.ClientSession, stats: Stats, drop_id: str) -> None:
        self.http, self.stats, self.drop_id = session, stats, drop_id

    async def call(
        self,
        ident: Identity,
        method: str,
        path: str,
        endpoint: str,
        *,
        json: dict[str, Any] | None = None,
        session: bool = True,
        idem: str | None = None,
        token: str | None = None,
    ) -> Result:
        headers = {"X-Sim-Client-IP": ident.ip, "User-Agent": ident.user_agent}
        tok = token if token is not None else (ident.session_token if session else None)
        if tok:
            headers["Authorization"] = f"Bearer {tok}"
        if idem:
            headers["Idempotency-Key"] = idem
        t0 = time.perf_counter()
        rheaders: Mapping[str, str] = {}
        body: Any = {}
        try:
            async with self.http.request(method, path, json=json, headers=headers) as r:
                status, rheaders = r.status, r.headers
                try:
                    body = await r.json(content_type=None)
                except (ValueError, aiohttp.ContentTypeError):
                    body = {}
        except (aiohttp.ClientError, TimeoutError):
            status = 0
        ms = (time.perf_counter() - t0) * 1000
        res = Result(status, body if isinstance(body, dict) else {}, rheaders)
        self.stats.record(ident.label, endpoint, status, res.code, ms, "Retry-After" in rheaders)
        return res

    # ---- contract endpoints
    async def otp_request(self, ident: Identity) -> Result:
        return await self.call(
            ident,
            "POST",
            "/api/auth/otp/request",
            "otp_request",
            json={"phone": ident.phone, "device_id": ident.device_id},
            session=False,
        )

    async def otp_verify(self, ident: Identity, request_id: str, otp: str) -> Result:
        r = await self.call(
            ident,
            "POST",
            "/api/auth/otp/verify",
            "otp_verify",
            json={"request_id": request_id, "otp": otp, "device_id": ident.device_id},
            session=False,
        )
        if r.ok:
            ident.session_token = r.body.get("session_token")
            ident.public_id = r.body.get("user_public_id")
        return r

    async def enter(self, ident: Identity) -> Result:
        r = await self.call(ident, "POST", f"/api/drops/{self.drop_id}/entries", "entries", json={})
        if r.status in (200, 201):
            ident.entered = True
        return r

    async def me(self, ident: Identity) -> Result:
        return await self.call(ident, "GET", f"/api/drops/{self.drop_id}/me", "me")

    async def claim(
        self, ident: Identity, admission_token: str, key: str | None = None, endpoint: str = "claim"
    ) -> Result:
        r = await self.call(
            ident,
            "POST",
            f"/api/drops/{self.drop_id}/claim",
            endpoint,
            json={"admission_token": admission_token},
            idem=key or str(uuid.uuid4()),
        )
        if r.ok and endpoint == "claim":
            ident.seat_no = r.body.get("seat_no")
        return r

    async def step_up(self, ident: Identity, otp: str, key: str) -> Result:
        return await self.call(
            ident,
            "POST",
            f"/api/drops/{self.drop_id}/step-up",
            "step_up",
            json={"otp": otp},
            idem=key,
        )


async def login(
    api: Api,
    ident: Identity,
    rng: random.Random,
    *,
    typing_s: tuple[float, float],
    polite: bool,
    attempts: int = 6,
) -> bool:
    """Request an OTP, 'type' it, verify. Honours Retry-After when polite.

    If `ident.log` holds per-user counters (genuine users), sign-in requests, 429s and retries
    are recorded there too, so genuine-user metrics include the sign-in step."""
    log = ident.log if "requests" in ident.log else None
    saw_429 = False

    def note(res: Result) -> None:
        nonlocal saw_429
        if log is None:
            return
        log["requests"] += 1
        if res.status == 429:
            saw_429 = True
            log["rate_limited"] += 1
            if res.headers.get("Retry-After") is not None and res.code:
                log["rate_limited_with_retry_after"] += 1
        elif res.status == 0 or res.status >= 500:
            log["server_or_transport_errors"] += 1
        else:
            log["first_try_ok"] += 0 if saw_429 else 1

    for _ in range(attempts):
        r = await api.otp_request(ident)
        note(r)
        if r.ok:
            if typing_s[1] > 0:
                await asyncio.sleep(rng.uniform(*typing_s))
            v = await api.otp_verify(
                ident, str(r.body.get("request_id")), str(r.body.get("dev_otp", ""))
            )
            note(v)
            if v.ok:
                if log is not None and saw_429:
                    log["retried_after_429_ok"] += 1
                return True
            r = v
        await asyncio.sleep(r.retry_after_s(1.0) if polite else 0.05)
    if log is not None and saw_429:
        log["retried_after_429_failed"] += 1
    return False
