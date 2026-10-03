"""Claim stampede: every eligible user claims at the same instant, many times over.

  sim stampede --mode fair --users 400 --capacity 100 --base-url URL --out out/stampede_fair

Per eligible user, all fired together in one burst:
  3 claims with the SAME Idempotency-Key   (double / triple click)
  2 claims with NEW keys, same token       (the response was lost, the app retried)
  1 claim with this user's token from ANOTHER user's session (replay)
Then it checks, from the backend's own export and integrity endpoint:
  oversold == 0, duplicate seats == 0, at most one seat per user, every successful response
  for one user names the same seat, and no replay ever succeeded.
Uses only contract endpoints, so it runs unchanged against the real backend.
"""

from __future__ import annotations

import asyncio
import json
import random
import time
import uuid
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from sim.api import Api, Result, login, make_session
from sim.model import Identity, Stats
from sim.runner import admin


async def stampede(
    base_url: str, admin_key: str, mode: str, users: int, capacity: int, out: Path, seed: int = 7
) -> dict[str, Any]:
    rng = random.Random(seed)  # noqa: S311 - simulation randomness, recorded seed
    stats = Stats()
    async with make_session(base_url, 2000, 30) as http:
        d = await admin(
            http,
            "POST",
            "/api/admin/drops",
            admin_key,
            {
                "name": "stampede",
                "capacity": capacity,
                "mode": mode,
                "window_s": 600,
                "claim_window_s": 120,
            },
        )
        drop = d["drop_id"]
        api = Api(http, stats, drop)
        await admin(http, "POST", f"/api/admin/drops/{drop}/phase", admin_key, {"action": "open"})
        people = [
            Identity(
                f"s{i}",
                "human",
                "human",
                "human",
                f"+91{rng.randrange(7_000_000_000, 9_999_999_999)}",
                f"dev-{rng.getrandbits(48):012x}",
                f"{rng.randint(11, 199)}.{rng.randint(0, 255)}.{rng.randint(0, 255)}.9",
                "Mozilla/5.0 stampede",
            )
            for i in range(users)
        ]
        sem = asyncio.Semaphore(100)

        async def join(p: Identity) -> None:
            async with sem:
                if await login(api, p, rng, typing_s=(0, 0), polite=True):
                    await api.enter(p)

        await asyncio.gather(*(join(p) for p in people))
        if mode == "fair":
            for action in ("close", "draw"):
                await admin(
                    http, "POST", f"/api/admin/drops/{drop}/phase", admin_key, {"action": action}
                )
        tokens: dict[str, str] = {}
        for p in people:
            r = await api.me(p)
            tok = ((r.body.get("entry") or {}).get("admission_token")) if r.ok else None
            if tok:
                tokens[p.identity_id] = str(tok)
        eligible = [p for p in people if p.identity_id in tokens]

        results: dict[str, list[Result]] = defaultdict(list)
        replays: list[Result] = []

        async def tagged(p: Identity, coro: Any) -> None:
            results[p.identity_id].append(await coro)

        async def replay(thief: Identity, tok: str) -> None:
            replays.append(await api.claim(thief, tok, endpoint="claim_replay"))

        burst = []
        for i, p in enumerate(eligible):
            tok, key = tokens[p.identity_id], str(uuid.uuid4())
            burst += [tagged(p, api.claim(p, tok, key)) for _ in range(3)]
            burst += [tagged(p, api.claim(p, tok)) for _ in range(2)]
            thief = eligible[(i + 1) % len(eligible)] if len(eligible) > 1 else None
            if thief is not None:
                burst.append(replay(thief, tok))
        t0 = time.perf_counter()
        await asyncio.gather(*burst)
        burst_s = time.perf_counter() - t0

        integrity = await admin(http, "GET", f"/api/admin/drops/{drop}/integrity", admin_key)
        export = [
            json.loads(line)
            for line in (
                await admin(http, "GET", f"/api/admin/drops/{drop}/export", admin_key)
            ).splitlines()
            if line.strip()
        ]

    seats_per_user = Counter(r["user_public_id"] for r in export if r.get("seat_no"))
    seat_nos = [r["seat_no"] for r in export if r.get("seat_no")]
    inconsistent = 0
    for rs in results.values():
        seen = {r.body.get("seat_no") for r in rs if r.ok}
        if len(seen) > 1:
            inconsistent += 1
    codes = Counter(r.status for rs in results.values() for r in rs)
    report = {
        "mode": mode,
        "users": users,
        "capacity": capacity,
        "eligible_claimers": len(eligible),
        "claim_requests": sum(len(v) for v in results.values()),
        "replay_requests": len(replays),
        "burst_seconds": round(burst_s, 3),
        "response_codes": dict(codes),
        "replay_successes": sum(1 for r in replays if r.ok),
        "replay_codes": dict(Counter(r.code or str(r.status) for r in replays)),
        "sold": integrity.get("sold"),
        "oversold": integrity.get("oversold"),
        "duplicate_seats_integrity": integrity.get("duplicate_entries_with_seats"),
        "duplicate_seat_numbers_in_export": len(seat_nos) - len(set(seat_nos)),
        "users_with_more_than_one_seat": sum(1 for v in seats_per_user.values() if v > 1),
        "users_with_inconsistent_seat_responses": inconsistent,
        "invariant_ok": integrity.get("invariant_ok"),
    }
    report["pass"] = bool(
        report["oversold"] == 0
        and report["duplicate_seats_integrity"] == 0
        and report["duplicate_seat_numbers_in_export"] == 0
        and report["users_with_more_than_one_seat"] == 0
        and report["users_with_inconsistent_seat_responses"] == 0
        and report["replay_successes"] == 0
        and (report["sold"] or 0) <= capacity
    )
    out.mkdir(parents=True, exist_ok=True)
    (out / "stampede.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report, indent=2))
    return report
