"""Run one scenario against a backend that speaks the frozen contract.

  sim run scenarios/genuine_retry.toml --mode fair --base-url http://localhost:8001 --out runs/x

Writes to --out (the run folder):
  ground_truth.ndjson  one row per identity with its label and actor (SIMULATOR ONLY)
  humans.ndjson        per genuine user: what they tried and how each request went
  client_stats.json    per-label request outcomes, client-observed latency, achieved load
  export.ndjson        copy of GET /api/admin/drops/{id}/export
  integrity.json       copy of GET /api/admin/drops/{id}/integrity
  metrics.json         copy of GET /api/admin/drops/{id}/metrics
  run_meta.json        scenario, seeds, mode, timings
"""

from __future__ import annotations

import asyncio
import json
import random
import time
import tomllib
from pathlib import Path
from typing import Any

import aiohttp

from sim.api import Api, login, make_session
from sim.clients.bot import run_bot_client, run_replay_attacker
from sim.clients.human import run_human
from sim.model import Identity, Stats, percentiles

BROWSER_UAS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/128.0 Safari/537.36",
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_5 like Mac OS X) AppleWebKit/605.1.15 Mobile/15E148",
    "Mozilla/5.0 (Linux; Android 14; Pixel 8) AppleWebKit/537.36 Chrome/127.0 Mobile Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_5) AppleWebKit/605.1.15 Version/17.5",
    "Mozilla/5.0 (Linux; Android 13; SM-S911B) AppleWebKit/537.36 Chrome/126.0 Mobile Safari",
]


def load_scenario(path: str | Path) -> dict[str, Any]:
    with open(path, "rb") as f:
        return tomllib.load(f)


def _phone(rng: random.Random) -> str:
    return f"+91{rng.randrange(7_000_000_000, 9_999_999_999)}"


def _ip(rng: random.Random, first: tuple[int, int] = (11, 199)) -> str:
    return (
        f"{rng.randint(*first)}.{rng.randint(0, 255)}.{rng.randint(0, 255)}.{rng.randint(1, 254)}"
    )


def arrivals(n: int, arr: dict[str, Any], rng: random.Random) -> list[float]:
    spread = float(arr.get("spread_s", 30))
    if arr.get("type") == "burst":  # e.g. 80% in the first 5 s
        k = int(n * float(arr.get("burst_frac", 0.8)))
        out = [rng.uniform(0, float(arr.get("burst_s", 5))) for _ in range(k)]
        return sorted(out + [rng.uniform(0, spread) for _ in range(n - k)])
    return sorted(rng.uniform(0, spread) for _ in range(n))


def make_population(sc: dict[str, Any], rng: random.Random) -> list[Identity]:
    out: list[Identity] = []
    h = sc.get("humans", {})
    n = int(h.get("count", 0))
    campus_n = int(h.get("campus_users", 0))
    campus_ip = _ip(rng, (128, 128))
    # Carrier-grade NAT: `carrier_nat_users` genuine users share `carrier_nat_ips` public IPs
    # in 100.64.0.0/10. Shared IP never means same person.
    cgnat_n = int(h.get("carrier_nat_users", 0))
    cgnat_ips = [
        f"100.{64 + rng.randint(0, 63)}.{rng.randint(0, 255)}.{rng.randint(1, 254)}"
        for _ in range(max(1, int(h.get("carrier_nat_ips", 1))) if cgnat_n else 0)
    ]
    switch_p = float(h.get("network_switch_p", 0.0))
    for i, t in enumerate(arrivals(n, h.get("arrival", {}), rng)):
        if i < campus_n:
            ip, network = campus_ip, "campus"
        elif i < campus_n + cgnat_n:
            ip, network = cgnat_ips[(i - campus_n) % len(cgnat_ips)], "cgnat"
        else:
            ip, network = _ip(rng), "home"
        alt = (
            f"100.{64 + rng.randint(0, 63)}.{rng.randint(0, 255)}.{rng.randint(1, 254)}"
            if rng.random() < switch_p
            else None
        )
        out.append(
            Identity(
                f"h{i}",
                "human",
                "human",
                "human",
                _phone(rng),
                f"dev-{rng.getrandbits(48):012x}",
                ip,
                rng.choice(BROWSER_UAS),
                arrival_s=t,
                alt_ip=alt,
                network=network,
            )
        )
    for a in sc.get("attackers", []):
        subnets = [
            f"{rng.randint(31, 99)}.{rng.randint(0, 255)}.{rng.randint(0, 255)}"
            for _ in range(int(a.get("subnets", 1)))
        ]
        ips = [
            f"{s}.{rng.randint(1, 254)}"
            for s in subnets
            for _ in range(int(a.get("ips_per_subnet", 1)))
        ]
        devices = [f"dev-{rng.getrandbits(48):012x}" for _ in range(int(a.get("devices", 1)))]
        uas = a.get("user_agents") or [a.get("user_agent", "python-httpx/0.27")]
        clean_frac = float(a.get("clean_frac", 0.0))  # 'good' proxies: unique device + IP
        base = rng.randrange(7_000_000_000, 9_000_000_000)
        for j in range(int(a.get("identities", 1))):
            clean = rng.random() < clean_frac
            phone = f"+91{base + j}" if a.get("phone_mode") == "sequential" else _phone(rng)
            out.append(
                Identity(
                    f"{a['actor_id']}-{j}",
                    "bot",
                    a["actor_id"],
                    a.get("kind", "flood"),
                    phone,
                    f"dev-{rng.getrandbits(48):012x}" if clean else devices[j % len(devices)],
                    _ip(rng) if clean else ips[j % len(ips)],
                    rng.choice(BROWSER_UAS) if clean else uas[j % len(uas)],
                    clients=int(a.get("clients_per_identity", 1)),
                )
            )
    return out


async def admin(
    http: aiohttp.ClientSession,
    method: str,
    path: str,
    key: str,
    body: dict[str, Any] | None = None,
) -> Any:
    """Admin call. Under attack the server queues every request, so control calls run on their
    own long-timeout session and get up to 3 tries instead of crashing the run."""
    for attempt in range(3):
        try:
            async with http.request(method, path, json=body, headers={"X-Admin-Key": key}) as r:
                if r.status >= 500 and attempt < 2:
                    continue
                r.raise_for_status()
                if path.endswith("/export"):
                    return await r.text()
                return await r.json(content_type=None)
        except (TimeoutError, aiohttp.ServerDisconnectedError, aiohttp.ClientOSError):
            if attempt == 2:
                raise
    raise RuntimeError(f"admin call failed: {method} {path}")


async def run(
    sc: dict[str, Any],
    mode: str,
    base_url: str,
    out: Path,
    admin_key: str,
    telemetry_key: str | None = None,
) -> dict[str, Any]:
    seed = int(sc.get("seed", 42))
    rng = random.Random(seed)  # noqa: S311 - simulation randomness, recorded seed
    stats = Stats()
    pop = make_population(sc, rng)
    humans = [i for i in pop if i.label == "human"]
    bots = [i for i in pop if i.label == "bot"]
    timeout = float(sc.get("timeout_s", 15))
    hclient = make_session(base_url, max(50, min(2000, len(humans) * 2)), timeout)
    bclient = make_session(base_url, int(sc.get("bot_connections", 1000)), timeout)
    aclient = make_session(base_url, 4, 120)  # admin and control calls only
    async with hclient, bclient, aclient:
        d = await admin(
            aclient,
            "POST",
            "/api/admin/drops",
            admin_key,
            {
                "name": sc["name"],
                "capacity": sc.get("capacity", 500),
                "mode": mode,
                "window_s": sc.get("registration_s", 60),
                "claim_window_s": sc.get("claim_window_s", 120),
            },
        )
        drop_id = d["drop_id"]
        hapi, bapi = Api(hclient, stats, drop_id), Api(bclient, stats, drop_id)
        ctx: dict[str, Any] = {
            "mode": mode,
            "phase": "SCHEDULED",
            "done": asyncio.Event(),
            "t_open": 0.0,
            "replay_successes": 0,
            "forged_successes": 0,
        }
        acfg = {a["actor_id"]: a for a in sc.get("attackers", [])}

        # Attackers sign their identities in before the window opens. A farm that bought its
        # accounts days earlier is modelled with `prestage_l6_off` (L6 windows long expired).
        prestage_off = any(a.get("prestage_l6_off") for a in acfg.values())
        if prestage_off:
            await admin(
                aclient, "PUT", "/api/admin/abuse/config", admin_key, {"layers": {"L6": False}}
            )
        sem = asyncio.Semaphore(200)
        t_login = time.time()

        async def bot_login(b: Identity) -> None:
            async with sem:
                # Bots do not wait out an OTP_THROTTLED Retry-After; a throttled identity
                # simply never gets an account (counted as an L6 denial).
                b.log["login_ok"] = await login(
                    bapi, b, rng, typing_s=(0.1, 0.4), polite=False, attempts=3
                )

        await asyncio.gather(*(bot_login(b) for b in bots))
        if prestage_off:
            await admin(
                aclient, "PUT", "/api/admin/abuse/config", admin_key, {"layers": {"L6": True}}
            )
        login_s = time.time() - t_login

        ctx["t_open"] = time.time() + 1.0
        hcfg = sc.get("humans", {})
        tasks = [
            asyncio.create_task(run_human(hapi, h, ctx, hcfg, random.Random(rng.random())))
            for h in humans
        ]
        by_actor: dict[str, list[Identity]] = {}
        for b in bots:
            if not b.session_token:
                continue
            by_actor.setdefault(b.actor_id, []).append(b)
        for actor, idents in by_actor.items():
            cfg = acfg[actor]
            if cfg.get("kind") == "replay":
                tasks.append(
                    asyncio.create_task(
                        run_replay_attacker(bapi, idents, cfg, ctx, random.Random(rng.random()))
                    )
                )
                continue
            for b in idents:
                for _ in range(b.clients):
                    tasks.append(
                        asyncio.create_task(
                            run_bot_client(bapi, b, cfg, ctx, random.Random(rng.random()))
                        )
                    )
        for a in acfg.values():  # anonymous junk needs no identity
            if a.get("kind") == "anon":
                for k in range(int(a.get("clients", 100))):
                    ghost = Identity(
                        f"{a['actor_id']}-anon{k}",
                        "bot",
                        a["actor_id"],
                        "anon",
                        "",
                        "",
                        _ip(rng),
                        "python-httpx/0.27",
                    )
                    tasks.append(
                        asyncio.create_task(
                            run_bot_client(bapi, ghost, a, ctx, random.Random(rng.random()))
                        )
                    )

        tel = (
            asyncio.create_task(_telemetry(aclient, telemetry_key, stats, pop, ctx))
            if telemetry_key
            else None
        )
        await asyncio.sleep(max(0.0, ctx["t_open"] - time.time()))
        await admin(
            aclient, "POST", f"/api/admin/drops/{drop_id}/phase", admin_key, {"action": "open"}
        )
        ctx["phase"] = "OPEN"
        await asyncio.sleep(float(sc.get("registration_s", 60)))
        try:
            await admin(
                aclient, "POST", f"/api/admin/drops/{drop_id}/phase", admin_key, {"action": "close"}
            )
        except aiohttp.ClientResponseError as exc:
            if not (mode == "fifo" and exc.status == 409):  # FIFO may already be sold out (DONE)
                raise
        ctx["phase"] = "CLOSED"
        if mode == "fair":
            await admin(
                aclient, "POST", f"/api/admin/drops/{drop_id}/phase", admin_key, {"action": "draw"}
            )
            ctx["phase"] = "CLAIMING"
            await asyncio.sleep(float(sc.get("claim_window_s", 120)) + 2)
        ctx["done"].set()
        await asyncio.wait(tasks, timeout=timeout + 5)
        for t in tasks:
            t.cancel()
        # let cancelled clients unwind before their HTTP sessions close
        await asyncio.gather(*tasks, return_exceptions=True)
        if tel is not None:
            tel.cancel()
        t_end = time.time()
        export_text = await admin(aclient, "GET", f"/api/admin/drops/{drop_id}/export", admin_key)
        integrity = await admin(aclient, "GET", f"/api/admin/drops/{drop_id}/integrity", admin_key)
        metrics = await admin(aclient, "GET", f"/api/admin/drops/{drop_id}/metrics", admin_key)

    out.mkdir(parents=True, exist_ok=True)
    (out / "export.ndjson").write_text(export_text, encoding="utf-8")
    with open(out / "ground_truth.ndjson", "w", encoding="utf-8", newline="\n") as f:
        for i in pop:
            f.write(
                json.dumps(
                    {
                        "identity_id": i.identity_id,
                        "label": i.label,
                        "actor_id": i.actor_id,
                        "kind": i.kind,
                        "user_public_id": i.public_id,
                        "device_id": i.device_id,
                        "ip": i.ip,
                        "alt_ip": i.alt_ip,
                        "network": i.network,
                        "arrival_s": round(i.arrival_s, 3),
                        "clients": i.clients,
                        "entered": i.entered,
                        "seat_no": i.seat_no,
                    }
                )
                + "\n"
            )
    with open(out / "humans.ndjson", "w", encoding="utf-8", newline="\n") as f:
        for h in humans:
            f.write(
                json.dumps(
                    {
                        "identity_id": h.identity_id,
                        "user_public_id": h.public_id,
                        "seat_no": h.seat_no,
                        **h.log,
                    }
                )
                + "\n"
            )
    dur = t_end - ctx["t_open"]
    total = sum(c["requests"] for c in stats.c.values())
    secs = [v for k, v in stats.per_second.items() if ctx["t_open"] <= k <= t_end]
    client_stats = {
        "by_label": {k: dict(v) for k, v in stats.c.items()},
        "latency_ms": {k: percentiles(v) for k, v in stats.lat.items()},
        "retry_after_missing": dict(stats.retry_after_missing),
        "replay_successes": ctx["replay_successes"],
        "forged_successes": ctx["forged_successes"],
        "bot_identities": len(bots),
        "bot_identities_with_account": sum(1 for b in bots if b.session_token),
        "achieved": {
            "duration_s": round(dur, 1),
            "total_requests": total,
            "avg_rps": round(total / max(dur, 1e-9), 1),
            "peak_rps": max(secs) if secs else 0,
            "concurrent_clients": len(tasks),
            "bot_login_s": round(login_s, 1),
        },
    }
    for name, obj in (
        ("client_stats", client_stats),
        ("integrity", integrity),
        ("metrics", metrics),
    ):
        (out / f"{name}.json").write_text(json.dumps(obj, indent=2), encoding="utf-8")
    if stats.trace is not None:
        with open(out / "trace.ndjson", "w", encoding="utf-8", newline="\n") as f:
            for row in stats.trace:
                f.write(json.dumps(row) + "\n")
    meta = {
        "scenario": sc,
        "mode": mode,
        "sim_seed": seed,
        "drop_id": drop_id,
        "base_url": base_url,
        "t_open": ctx["t_open"],
        "t_end": t_end,
    }
    (out / "run_meta.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")
    print(
        f"[sim] {sc['name']} mode={mode} requests={total} avg_rps={total / max(dur, 1e-9):.0f}"
        f" peak_rps={client_stats['achieved']['peak_rps']} -> {out}"
    )
    return client_stats


async def _telemetry(
    http: aiohttp.ClientSession, key: str, stats: Stats, pop: list[Identity], ctx: dict[str, Any]
) -> None:
    """Presentation-only counts for the dashboard (contract interface 6, `sim:*`)."""
    clients = {
        "human": sum(1 for i in pop if i.label == "human"),
        "bot": sum(i.clients for i in pop if i.label == "bot"),
    }
    idents = {"human": clients["human"], "bot": sum(1 for i in pop if i.label == "bot")}
    while not ctx["done"].is_set():
        body = {
            "run_id": f"run_{int(ctx['t_open'])}_{ctx['mode']}",
            "attack_phase": ctx["phase"],
            "clients_by_label": clients,
            "identities_by_label": idents,
            "requests_by_label": {k: v["requests"] for k, v in stats.c.items()},
        }
        try:
            async with http.post("/api/sim/telemetry", json=body, headers={"X-Sim-Key": key}):
                pass
        except (aiohttp.ClientError, TimeoutError):
            pass
        await asyncio.sleep(1.0)
