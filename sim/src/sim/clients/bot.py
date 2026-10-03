"""Simulated attackers. Zero think time, fixed request rate, ignore Retry-After unless 'polite'.

kinds:
  flood      many clients share one identity and hammer entries, /me and claim
  burst      like flood but every client fires inside the first 200 ms after OPEN
  duplicate  each identity repeats entries and claims many times
  replay     an attacker with several identities replays its winners' tokens from other
             sessions, reuses used tokens and sends forged ones
  farm       one client per farmed identity: enters at once, polls fast, claims if offered;
             passes step-up only with probability `step_up_success_p`
  anon       no session at all: junk traffic at session endpoints from many IPs
"""

from __future__ import annotations

import asyncio
import base64
import json
import random
import time
import uuid
from typing import Any

from sim.api import Api
from sim.model import Identity


async def _until(ctx: dict[str, Any], offset_s: float) -> None:
    await asyncio.sleep(max(0.0, ctx["t_open"] + offset_s - time.time()))


async def run_bot_client(
    api: Api, ident: Identity, cfg: dict[str, Any], ctx: dict[str, Any], rng: random.Random
) -> None:
    kind = cfg.get("kind", "flood")
    if kind == "burst":
        await _until(ctx, rng.uniform(0, 0.2))
    else:
        await _until(ctx, cfg.get("start_s", 0) + rng.uniform(0, 0.05))
    end = ctx["t_open"] + cfg.get("start_s", 0) + cfg.get("duration_s", 1e9)
    gap = 1.0 / max(float(cfg.get("rps_per_client", 10)), 0.01)
    polite = bool(cfg.get("polite", False))
    token: str | None = None
    repeats = 0
    while not ctx["done"].is_set() and time.time() < end:
        if kind == "anon":
            r = await (api.enter(ident) if rng.random() < 0.5 else api.me(ident))
        elif ident.seat_no is not None and kind != "duplicate":
            return
        elif ctx["mode"] == "fifo" or ctx["phase"] == "OPEN" and not ident.entered:
            if not ident.entered or kind == "duplicate" and repeats < 50:
                r = await api.enter(ident)
                repeats += 1
            else:
                r = await api.me(ident)
                token = ((r.body.get("entry") or {}).get("admission_token")) if r.ok else None
                if token:
                    r = await api.claim(ident, token)
        else:
            if kind == "duplicate" and ctx["phase"] == "OPEN" and repeats < 50:
                r = await api.enter(ident)
                repeats += 1
            elif token is None:
                r = await api.me(ident)
                entry = (r.body.get("entry") or {}) if r.ok else {}
                token = entry.get("admission_token")
                if entry.get("status") == "STEP_UP_REQUIRED" and entry.get("dev_otp"):
                    if rng.random() < float(cfg.get("step_up_success_p", 0.0)):
                        await api.step_up(ident, str(entry["dev_otp"]), str(uuid.uuid4()))
                    else:
                        return  # the farm cannot receive a fresh OTP on demand
                if entry.get("status") in (
                    "NOT_SELECTED",
                    "WAITLISTED",
                    "OFFER_EXPIRED",
                    "ALLOCATED",
                ):
                    if entry.get("status") != "WAITLISTED":
                        return
            else:
                r = await api.claim(ident, token)
                if kind == "duplicate":
                    for _ in range(3):
                        await api.claim(ident, token)
                token = None
        wait = gap
        if polite and r.status == 429:
            wait = max(gap, r.retry_after_s(gap))
        await asyncio.sleep(wait)


def forge(token: str) -> str:
    """Change one claim and keep the old signature (must fail signature check)."""
    try:
        head, body, sig = token.split(".")
        claims = json.loads(base64.urlsafe_b64decode(body + "=" * (-len(body) % 4)))
        claims["entry_id"] = uuid.uuid4().hex
        new = base64.urlsafe_b64encode(json.dumps(claims).encode()).rstrip(b"=").decode()
        return f"{head}.{new}.{sig}"
    except (ValueError, json.JSONDecodeError):
        return "forged.token.sig"


async def run_replay_attacker(
    api: Api, idents: list[Identity], cfg: dict[str, Any], ctx: dict[str, Any], rng: random.Random
) -> None:
    """Every identity enters; whenever one sees a token, the others try to use it, and the
    winner retries it after use; forged variants are sent too. Successes are recorded."""
    await _until(ctx, cfg.get("start_s", 0))
    for ident in idents:
        await api.enter(ident)
    seen: set[str] = set()
    while not ctx["done"].is_set():
        for owner in idents:
            r = await api.me(owner)
            tok = ((r.body.get("entry") or {}).get("admission_token")) if r.ok else None
            if not tok or tok in seen:
                continue
            seen.add(tok)
            for thief in idents:
                if thief is not owner:
                    t = await api.claim(thief, tok, endpoint="claim_replay")
                    if t.ok:
                        ctx["replay_successes"] += 1
            f = await api.claim(owner, forge(tok), endpoint="claim_forged")
            if f.ok:
                ctx["forged_successes"] += 1
        await asyncio.sleep(float(cfg.get("poll_s", 1.0)))
