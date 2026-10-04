"""Simulated genuine user. Imperfect on purpose: think time, OTP typing, impatient repeated entry
clicks, a refresh, a second tab, an optional Wi-Fi -> mobile-data switch, and it honours
`Retry-After` and `poll_after_ms`. Every request outcome is logged so genuine-user success is
measured, not assumed."""

from __future__ import annotations

import asyncio
import random
import time
import uuid
from collections.abc import Awaitable, Callable
from functools import partial
from typing import Any

from sim.api import Api, Result, login
from sim.model import Identity

TERMINAL = {"ALLOCATED", "NOT_SELECTED", "OFFER_EXPIRED", "DISQUALIFIED"}


def _log(ident: Identity) -> dict[str, Any]:
    if not ident.log:
        ident.log.update(
            {
                "requests": 0,
                "first_try_ok": 0,
                "rate_limited": 0,
                "rate_limited_with_retry_after": 0,
                "retried_after_429_ok": 0,
                "retried_after_429_failed": 0,
                "server_or_transport_errors": 0,
                "entry_attempts": 0,
                "entry_codes": [],
                "logged_in": False,
                "offered": False,
                "step_up": None,
                "claimed": False,
                "claim_inside_window": None,
                "final_status": None,
                "network_switched": False,
                "tabs": 1,
            }
        )
    return ident.log


async def persist(
    ident: Identity, fn: Callable[[], Awaitable[Result]], *, tries: int = 6
) -> Result:
    """Make one logical request like a browser would: retry 429 / 5xx / network errors after
    the server's Retry-After (or a short backoff), up to `tries` times."""
    log = _log(ident)
    r = await fn()
    log["requests"] += 1
    if r.status not in (429, 0) and r.status < 500:
        log["first_try_ok"] += 1
        return r
    for _ in range(tries - 1):
        if r.status == 429:
            log["rate_limited"] += 1
            if r.headers.get("Retry-After") is not None and r.code:
                log["rate_limited_with_retry_after"] += 1
        else:
            log["server_or_transport_errors"] += 1
        was_429 = r.status == 429
        await asyncio.sleep(r.retry_after_s(1.0) * random.uniform(1.0, 1.2))  # noqa: S311
        r = await fn()
        log["requests"] += 1
        if r.status not in (429, 0) and r.status < 500:
            if was_429:
                log["retried_after_429_ok"] += 1
            return r
    if r.status == 429:
        log["rate_limited"] += 1
        log["retried_after_429_failed"] += 1
    return r


async def run_human(
    api: Api, ident: Identity, ctx: dict[str, Any], cfg: dict[str, Any], rng: random.Random
) -> None:
    log = _log(ident)
    await asyncio.sleep(max(0.0, ctx["t_open"] + ident.arrival_s - time.time()))
    await asyncio.sleep(rng.uniform(*cfg.get("think_s", [1, 4])))
    if not await login(api, ident, rng, typing_s=tuple(cfg.get("typing_s", [2, 6])), polite=True):
        return
    log["logged_in"] = True
    if ident.alt_ip:  # walked out of Wi-Fi range: same session, new network
        ident.ip = ident.alt_ip
        log["network_switched"] = True

    lo, hi = cfg.get("entry_attempts", [1, 1])
    for _ in range(rng.randint(lo, hi)):
        if ctx["done"].is_set():
            return
        r = await persist(ident, lambda: api.enter(ident))
        log["entry_attempts"] += 1
        log["entry_codes"].append(r.status)
        if r.status == 403:
            break
        await asyncio.sleep(rng.uniform(0.2, 1.0))

    tabs = [asyncio.create_task(_tab(api, ident, ctx, cfg, rng, refresh=True))]
    if rng.random() < cfg.get("second_tab_p", 0.05):
        log["tabs"] = 2
        tabs.append(
            asyncio.create_task(
                _tab(api, ident, ctx, cfg, random.Random(rng.random()), refresh=False)
            )
        )
    await asyncio.gather(*tabs, return_exceptions=True)


async def _tab(
    api: Api,
    ident: Identity,
    ctx: dict[str, Any],
    cfg: dict[str, Any],
    rng: random.Random,
    *,
    refresh: bool,
) -> None:
    log = _log(ident)
    refresh_left = 1 if refresh and rng.random() < cfg.get("refresh_p", 0.05) else 0
    while not ctx["done"].is_set():
        r = await persist(ident, lambda: api.me(ident))
        if refresh_left and r.ok:
            refresh_left = 0
            r = await persist(ident, lambda: api.me(ident))  # the user hits refresh
        wait = 2.0
        if r.ok:
            entry = r.body.get("entry") or {}
            status = entry.get("status")
            log["final_status"] = status
            if status in TERMINAL or ident.seat_no is not None:
                return
            if status == "STEP_UP_REQUIRED" and entry.get("dev_otp") and log["step_up"] is None:
                log["step_up"] = "pending"
                await asyncio.sleep(rng.uniform(3, 6))  # reads the SMS, types the code
                key = str(uuid.uuid4())
                otp = str(entry["dev_otp"])
                s = await persist(ident, partial(api.step_up, ident, otp, key))
                log["step_up"] = "passed" if s.ok else f"failed:{s.code}"
                continue
            tok = entry.get("admission_token")
            if tok and (status == "OFFERED" or ctx["mode"] == "fifo"):
                log["offered"] = True
                await asyncio.sleep(rng.uniform(*cfg.get("tap_s", [2, 8])))
                key = str(uuid.uuid4())  # one key per tap, reused across retries
                c = await persist(ident, partial(api.claim, ident, str(tok), key))
                if c.ok:
                    log["claimed"] = True
                    exp = entry.get("offer_expires_at")
                    log["claim_inside_window"] = True if exp else None
                    log["final_status"] = "ALLOCATED"
                    return
                if c.code in ("SOLD_OUT", "OFFER_EXPIRED", "NOT_OFFERED"):
                    log["final_status"] = c.code
                    if c.code != "NOT_OFFERED":
                        return
                continue
            wait = float(r.body.get("poll_after_ms") or 2000) / 1000
        await asyncio.sleep(wait * rng.uniform(0.9, 1.1))
