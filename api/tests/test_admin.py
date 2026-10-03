"""Chunk 8: admin lifecycle (incl. reset), integrity, metrics, export, telemetry, abuse config."""

from __future__ import annotations

import asyncio
import dataclasses
import json
import pathlib
import re
import time
import uuid

import asyncpg
import httpx
import pytest
from fastapi import FastAPI

import app.abuse as abuse_pkg
from app.abuse import Decision
from app.config import Settings
from app.main import create_app
from tests.app_helpers import (
    admin_create,
    admin_headers,
    admin_phase,
    claim_with,
    enter_all,
    fair_drawn_drop,
    make_users,
    token_of,
    tokens_of,
)


async def test_every_admin_route_requires_the_key(client: httpx.AsyncClient) -> None:
    d = uuid.uuid4()
    calls = [
        ("GET", "/api/admin/drops"),
        ("POST", "/api/admin/drops"),
        ("POST", f"/api/admin/drops/{d}/phase"),
        ("GET", f"/api/admin/drops/{d}/metrics"),
        ("GET", f"/api/admin/drops/{d}/integrity"),
        ("GET", f"/api/admin/drops/{d}/export"),
        ("GET", f"/api/admin/drops/{d}/draw-proof"),
        ("GET", f"/api/admin/drops/{d}/sim"),
        ("PUT", "/api/admin/abuse/config"),
        ("GET", "/api/admin/abuse/config"),
    ]
    for method, path in calls:
        for headers in ({}, {"X-Admin-Key": "wrong"}):
            r = await client.request(method, path, headers=headers, json={})
            assert r.status_code == 401, (method, path, r.text)
            assert r.json()["error"]["code"] == "UNAUTHENTICATED"


async def test_create_validates_input(client: httpx.AsyncClient, settings: Settings) -> None:
    h = admin_headers(settings)
    for body in (
        {"name": "x", "mode": "lottery", "window_s": 10, "capacity": 5},
        {"name": "x", "mode": "fair", "window_s": 0, "capacity": 5},
        {"name": "x", "mode": "fair", "window_s": 10, "capacity": 0},
        {"name": "x", "mode": "fair", "window_s": 10, "capacity": 10_001},
        {"mode": "fair", "window_s": 10},
    ):
        r = await client.post("/api/admin/drops", json=body, headers=h)
        assert r.status_code == 400 and r.json()["error"]["code"] == "VALIDATION_ERROR", body


async def test_drop_list(client: httpx.AsyncClient, settings: Settings) -> None:
    a = await admin_create(client, settings, capacity=5)
    b = await admin_create(client, settings, mode="fifo", capacity=7)
    drops = (await client.get("/api/admin/drops", headers=admin_headers(settings))).json()["drops"]
    assert [(d["id"], d["mode"], d["phase"], d["run_no"], d["capacity"]) for d in drops] == [
        (a, "fair", "SCHEDULED", 1, 5),
        (b, "fifo", "SCHEDULED", 1, 7),
    ]


# --- lifecycle ---


async def test_the_demo_sequence_fifo_then_reset_to_fair_then_draw(
    client: httpx.AsyncClient, db: asyncpg.Connection, settings: Settings
) -> None:
    drop = await admin_create(client, settings, mode="fifo", capacity=3)
    commit_1 = (await client.get(f"/api/drops/{drop}")).json()["seed_commit"]
    assert (await admin_phase(client, settings, drop, "open")).json()["phase"] == "OPEN"
    users = await make_users(db, settings, 5)
    await enter_all(client, users, drop)
    tokens = await tokens_of(client, users, drop)
    for u, t in zip(users, tokens, strict=True):
        await claim_with(client, u, drop, t)
    await asyncio.sleep(0.3)
    assert await db.fetchval("SELECT phase FROM drops") == "DONE"
    assert await db.fetchval("SELECT count(*) FROM allocations") == 3

    reset = await admin_phase(client, settings, drop, "reset", mode="fair")
    assert reset.status_code == 200 and reset.json()["phase"] == "SCHEDULED"
    row = await db.fetchrow(
        "SELECT run_no, mode, phase, seed_commit, reg_opens_at, entry_set_hash FROM drops"
    )
    assert (row["run_no"], row["mode"], row["phase"]) == (2, "fair", "SCHEDULED")
    assert (
        row["seed_commit"] != commit_1
        and row["reg_opens_at"] is None
        and row["entry_set_hash"] is None
    )
    assert await db.fetchval("SELECT count(*) FROM entries") == 0
    assert await db.fetchval("SELECT count(*) FROM allocations") == 0
    assert await db.fetchval("SELECT count(*) FROM seats WHERE status = 'free'") == 3
    assert (
        await db.fetchval("SELECT count(*) FROM users") == 5
        and await db.fetchval("SELECT count(*) FROM sessions") == 5
    )
    run = await db.fetchrow("SELECT run_no, mode, summary::text AS summary FROM drop_runs")
    assert (run["run_no"], run["mode"]) == (1, "fifo")
    assert json.loads(run["summary"])["entries_by_status"] == {"ALLOCATED": 3, "NOT_SELECTED": 2}
    # the same drop and the same users now run Fair
    assert (await admin_phase(client, settings, drop, "open")).status_code == 200
    assert {r.status_code for r in await enter_all(client, users, drop)} == {201}
    await admin_phase(client, settings, drop, "close")
    assert (await admin_phase(client, settings, drop, "draw")).json()["phase"] == "CLAIMING"
    proof = (await client.get(f"/api/drops/{drop}/draw-proof")).json()
    assert proof["run_no"] == 2 and len(proof["ranked_public_ids"]) == 5
    await asyncio.sleep(0)
    # tokens from before the reset are dead (run mismatch)
    stale = await claim_with(client, users[0], drop, tokens[0])
    assert stale.status_code == 401 and stale.json()["error"]["code"] == "TOKEN_INVALID"


async def test_reset_works_in_every_phase_and_is_repeatable(
    client: httpx.AsyncClient, db: asyncpg.Connection, settings: Settings
) -> None:
    drop, users = await fair_drawn_drop(client, db, settings, capacity=4, entrants=6)
    tokens = await tokens_of(client, users, drop)
    for u, t in zip(users, tokens, strict=True):
        if t:
            await claim_with(client, u, drop, t)
    assert await db.fetchval("SELECT count(*) FROM allocations") == 4
    assert (await admin_phase(client, settings, drop, "reset")).json()["phase"] == "SCHEDULED"
    assert (await admin_phase(client, settings, drop, "reset")).json()["phase"] == "SCHEDULED"
    assert await db.fetchval("SELECT run_no FROM drops") == 3
    integrity = (
        await client.get(f"/api/admin/drops/{drop}/integrity", headers=admin_headers(settings))
    ).json()
    assert integrity["invariant_ok"] is True and integrity["sold"] == 0 and integrity["free"] == 4
    assert await db.fetchval("SELECT count(*) FROM drop_runs") == 2


async def test_the_application_role_still_cannot_touch_the_ledger_directly(
    app: FastAPI, client: httpx.AsyncClient, db: asyncpg.Connection, settings: Settings
) -> None:
    drop, users = await fair_drawn_drop(client, db, settings, capacity=2, entrants=2)
    await claim_with(client, users[0], drop, await token_of(client, users[0], drop))
    async with app.state.pool.acquire() as conn:
        with pytest.raises(asyncpg.InsufficientPrivilegeError):
            await conn.execute("DELETE FROM allocations")


# --- integrity ---


async def test_integrity_endpoint_reflects_the_database(
    client: httpx.AsyncClient, db: asyncpg.Connection, settings: Settings
) -> None:
    drop, users = await fair_drawn_drop(client, db, settings, capacity=5, entrants=5)
    url = f"/api/admin/drops/{drop}/integrity"
    empty = (await client.get(url, headers=admin_headers(settings))).json()
    assert empty["seats_total"] == 5 and empty["sold"] == 0 and empty["free"] == 5
    assert (
        empty["oversold"] == 0
        and empty["duplicate_entries_with_seats"] == 0
        and empty["invariant_ok"] is True
    )
    assert set(empty["extra"]) >= {
        "allocations_count",
        "sold_without_allocation",
        "entries_allocated_mismatch",
    }
    for u in users[:3]:
        await claim_with(client, u, drop, await token_of(client, u, drop))
    after = (await client.get(url, headers=admin_headers(settings))).json()
    assert (after["sold"], after["free"], after["invariant_ok"]) == (3, 2, True)
    assert (
        after["extra"]["allocations_count"] == 3 and after["extra"]["entries_allocated_count"] == 3
    )
    # an inconsistency inserted behind the application's back is reported
    await db.execute("SET session_replication_role = replica")
    await db.execute(
        "UPDATE entries SET status = 'NOT_SELECTED' WHERE id ="
        " (SELECT id FROM entries WHERE status = 'ALLOCATED' LIMIT 1)"
    )
    await db.execute("SET session_replication_role = DEFAULT")
    broken = (await client.get(url, headers=admin_headers(settings))).json()
    assert broken["invariant_ok"] is False
    unknown = await client.get(
        f"/api/admin/drops/{uuid.uuid4()}/integrity", headers=admin_headers(settings)
    )
    assert unknown.status_code == 404


# --- metrics ---


async def test_metrics_counts_outcomes_latency_and_database_figures(
    client: httpx.AsyncClient,
    db: asyncpg.Connection,
    settings: Settings,
    app: FastAPI,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    drop, users = await fair_drawn_drop(client, db, settings, capacity=3, entrants=6)
    tokens = await tokens_of(client, users, drop)
    winners = [(u, t) for u, t in zip(users, tokens, strict=True) if t]
    for u, t in winners:
        await claim_with(client, u, drop, t)
    await claim_with(client, winners[0][0], drop, winners[0][1])  # a replay -> duplicate
    await claim_with(client, users[0], drop, "garbage-token-value")  # token_rejected

    async def reject(request: object) -> Decision:
        return Decision("reject", "L2", "RATE_LIMITED", 1000)

    with monkeypatch.context() as m:
        m.setattr(abuse_pkg, "check", reject)
        for _ in range(4):
            await client.get(f"/api/drops/{drop}")
    await app.state.metrics.flush()
    r = await client.get(
        f"/api/admin/drops/{drop}/metrics?window_s=30", headers=admin_headers(settings)
    )
    assert r.status_code == 200, r.text
    m = r.json()
    assert len(m["rps_series"]) == 30 and all(set(p) == {"t", "total"} for p in m["rps_series"])
    assert all(len(v) == 30 for v in m["outcomes_series"].values())
    assert sum(m["outcomes_series"]["accepted"]) >= 6 + 3  # entries + claims
    assert (
        sum(m["outcomes_series"]["rate_limited"]) == 4
        and m["rate_limited_by_layer"]["L2"]
        and m["throttled_requests"] == 4
    )
    assert (
        sum(m["outcomes_series"]["token_rejected"]) == 1
        and sum(m["outcomes_series"]["duplicate"]) >= 1
    )
    assert (
        m["latency"]["p50"] > 0
        and m["latency"]["p50"] <= m["latency"]["p95"] <= m["latency"]["p99"]
    )
    assert m["error_rate"] == 0.0
    assert (m["entries"], m["allocated"], m["remaining"], m["offers"]) == (6, 3, 0, 3)
    assert (m["phase"], m["mode"], m["run_no"], m["capacity"]) in {
        ("DONE", "fair", 1, 3),
        ("CLAIMING", "fair", 1, 3),
    }
    assert (m["oversold"], m["invariant_ok"], m["claims_ok"]) == (0, True, 3)
    assert m["blocked_requests"] == 5 and m["step_ups"] == {"issued": 0, "passed": 0, "failed": 0}
    assert (
        await client.get(
            f"/api/admin/drops/{uuid.uuid4()}/metrics", headers=admin_headers(settings)
        )
    ).status_code == 404
    bad = await client.get(
        f"/api/admin/drops/{drop}/metrics?window_s=0", headers=admin_headers(settings)
    )
    assert bad.status_code == 400


# --- export ---


async def test_export_rows_contract_privacy_and_headers(
    client: httpx.AsyncClient, db: asyncpg.Connection, settings: Settings
) -> None:
    drop, users = await fair_drawn_drop(client, db, settings, capacity=3, entrants=8)
    winner = next(u for u, t in zip(users, await tokens_of(client, users, drop), strict=True) if t)
    await claim_with(client, winner, drop, await token_of(client, winner, drop))
    r = await client.get(f"/api/admin/drops/{drop}/export", headers=admin_headers(settings))
    assert r.status_code == 200 and r.headers["content-type"].startswith("application/x-ndjson")
    assert "x-server-time" in r.headers
    rows = [json.loads(line) for line in r.text.splitlines()]
    assert len(rows) == 8 and {x["user_public_id"] for x in rows} == {u.public_id for u in users}
    for x in rows:
        assert {
            "user_public_id",
            "entry_id",
            "entered_at",
            "risk_score",
            "risk_flags",
            "rank",
            "status",
        } <= set(x)
        assert not {"phone", "phone_hash", "client_ip", "device_id", "session", "user_id"} & set(x)
    seated = [x for x in rows if "seat_no" in x]
    assert [x["user_public_id"] for x in seated] == [winner.public_id] and seated[0][
        "status"
    ] == "ALLOCATED"
    assert sorted(x["rank"] for x in rows) == list(range(1, 9))
    assert (
        await client.get(f"/api/admin/drops/{uuid.uuid4()}/export", headers=admin_headers(settings))
    ).status_code == 404


async def test_export_of_52k_entries_streams_quickly(
    client: httpx.AsyncClient, db: asyncpg.Connection, settings: Settings
) -> None:
    drop = await admin_create(client, settings, capacity=500)
    await admin_phase(client, settings, drop, "open")
    await make_users(db, settings, 52_000)
    await db.execute(
        "INSERT INTO entries (drop_id, user_id, status, run_no)"
        " SELECT $1, id, 'REGISTERED', 1 FROM users",
        uuid.UUID(drop),
    )
    started = time.perf_counter()
    count = 0
    async with client.stream(
        "GET", f"/api/admin/drops/{drop}/export", headers=admin_headers(settings)
    ) as r:
        async for line in r.aiter_lines():
            if line:
                json.loads(line)
                count += 1
    elapsed = time.perf_counter() - started
    assert count == 52_000 and elapsed < 15
    print(f"\n[export of 52,000 rows: {elapsed:.2f}s]")


# --- simulator telemetry, abuse config, ground-truth isolation ---


async def test_telemetry_is_authenticated_stored_and_display_only(
    client: httpx.AsyncClient, settings: Settings, db: asyncpg.Connection
) -> None:
    body = {
        "run_id": "run_x",
        "attack_phase": "flood",
        "clients_by_label": {"human": 50000, "bot": 10000},
        "identities_by_label": {"human": 50000, "bot": 2000},
        "requests_by_label": {"human": 1, "bot": 2},
    }
    assert (await client.post("/api/sim/telemetry", json=body)).status_code == 401
    assert (
        await client.post("/api/sim/telemetry", json=body, headers={"X-Sim-Key": "nope"})
    ).status_code == 401
    ok = await client.post(
        "/api/sim/telemetry", json=body, headers={"X-Sim-Key": settings.sim_telemetry_key}
    )
    assert ok.status_code == 200 and "server_time" in ok.json()
    drop = await admin_create(client, settings, capacity=2)
    latest = (
        await client.get(f"/api/admin/drops/{drop}/sim", headers=admin_headers(settings))
    ).json()["latest"]
    assert latest["attack_phase"] == "flood" and latest["clients_by_label"]["bot"] == 10000
    assert (
        await client.post(
            "/api/sim/telemetry",
            json={"attack_phase": "x"},
            headers={"X-Sim-Key": settings.sim_telemetry_key},
        )
    ).status_code == 400


async def test_telemetry_is_404_when_sim_mode_is_off(settings: Settings, clean: None) -> None:
    prod_like = create_app(dataclasses.replace(settings, sim_mode=False))
    async with prod_like.router.lifespan_context(prod_like):
        transport = httpx.ASGITransport(app=prod_like)
        async with httpx.AsyncClient(transport=transport, base_url="http://t") as c:
            r = await c.post(
                "/api/sim/telemetry",
                json={"run_id": "r", "attack_phase": "a"},
                headers={"X-Sim-Key": settings.sim_telemetry_key},
            )
            assert r.status_code == 404 and r.json()["error"]["code"] == "NOT_FOUND"


async def test_abuse_config_round_trip(
    client: httpx.AsyncClient, settings: Settings, db: asyncpg.Connection, app: FastAPI
) -> None:
    h = admin_headers(settings)
    cfg = {"layers": {"L1": True, "L2": False, "L8": True}, "thresholds": {"ip_per_s": 20}}
    r = await client.put("/api/admin/abuse/config", json=cfg, headers=h)
    assert (
        r.status_code == 200
        and r.json()["layers"] == cfg["layers"]
        and r.json()["thresholds"] == cfg["thresholds"]
    )
    assert (await client.get("/api/admin/abuse/config", headers=h)).json()["layers"] == cfg[
        "layers"
    ]
    assert (
        json.loads(
            await db.fetchval("SELECT value::text FROM app_settings WHERE key = 'abuse_config'")
        )
        == cfg
    )
    assert json.loads(await app.state.cache.client.get("abuse:config")) == cfg
    bad = await client.put("/api/admin/abuse/config", json={"layers": {"L9": True}}, headers=h)
    assert bad.status_code == 400 and bad.json()["error"]["code"] == "VALIDATION_ERROR"


def test_simulator_labels_never_reach_a_decision_module() -> None:
    root = pathlib.Path(__file__).resolve().parents[1] / "app"
    decision = [
        "services/auth.py",
        "services/entries.py",
        "services/draw.py",
        "services/claim.py",
        "services/tokens.py",
        "services/sweeper.py",
        "services/stepup.py",
        "services/lifecycle.py",
        "services/idempotency.py",
        "abuse/__init__.py",
        "abuse/limiter.py",
        "abuse/risk.py",
        "jobs.py",
        "draw_math.py",
        "security.py",
        "deps.py",
        "middleware.py",
    ]
    for rel in decision:
        text = (root / rel).read_text()
        assert "sim:" not in text, rel
        assert not re.search(
            r"from app\.(services\.admin|routers\.sim)|import app\.(services\.admin|routers\.sim)",
            text,
        ), rel
        assert not re.search(r"ground_truth|\bis_bot\b|\bbot_label\b", text), rel
    owners = [
        p.relative_to(root).as_posix() for p in root.rglob("*.py") if '"sim:' in p.read_text()
    ]
    assert owners == ["services/admin.py"]
