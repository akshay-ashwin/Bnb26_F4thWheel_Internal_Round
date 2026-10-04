"""Ground-truth labels must never reach the backend.

Every request the simulator sends (headers + URL + body), for humans and every bot kind, is
captured by a local server and searched for labels, actor ids and kinds. The static check on the
backend side lives in api/tests/abuse/test_isolation.py.
"""

from __future__ import annotations

import json
import random

from aiohttp import web

from sim.api import Api, make_session
from sim.model import Identity, Stats
from sim.runner import make_population


async def test_no_label_or_actor_id_on_the_wire() -> None:
    seen: list[str] = []

    async def any_route(request: web.Request) -> web.Response:
        body = await request.text()
        seen.append(" ".join([str(request.rel_url), json.dumps(dict(request.headers)), body]))
        path = request.path
        if path.endswith("/otp/request"):
            return web.json_response({"request_id": "r1", "dev_otp": "123456"})
        if path.endswith("/otp/verify"):
            return web.json_response({"session_token": "s.t", "user_public_id": "u_1"})
        if path.endswith("/me"):
            return web.json_response({"entry": {"status": "OFFERED", "admission_token": "a.b.c"}})
        return web.json_response({"entry_id": "e1", "status": "REGISTERED"}, status=201)

    app = web.Application()
    app.router.add_route("*", "/{tail:.*}", any_route)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    port = site._server.sockets[0].getsockname()[1]  # type: ignore[union-attr]

    sc = {
        "humans": {"count": 3, "network_switch_p": 1.0, "campus_users": 2},
        "attackers": [
            {"actor_id": "ACTOR_SECRET_X", "kind": kind, "identities": 2}
            for kind in ("flood", "farm", "duplicate", "replay")
        ],
    }
    pop = make_population(sc, random.Random(1))  # noqa: S311 - test data
    try:
        async with make_session(f"http://127.0.0.1:{port}", 10, 5) as http:
            api = Api(http, Stats(), "drop1")
            for ident in pop:
                r = await api.otp_request(ident)
                await api.otp_verify(ident, str(r.body["request_id"]), "123456")
                await api.enter(ident)
                await api.me(ident)
                await api.claim(ident, "a.b.c")
                await api.claim(ident, "a.b.c", endpoint="claim_replay")
                await api.step_up(ident, "123456", "k1")
            ghost = Identity("g", "bot", "ACTOR_SECRET_X", "anon", "", "", "1.2.3.4", "ua")
            await api.enter(ghost)
    finally:
        await runner.cleanup()
    blob = "\n".join(seen).lower()
    assert seen
    for needle in (
        "human",
        '"bot"',
        "actor_secret_x",
        "label",
        "actor",
        "ground_truth",
        "flood",
        "farm",
        "duplicate",
        "replay",
        "is_bot",
    ):
        assert needle not in blob, needle


async def test_sign_in_429s_are_counted_for_genuine_users() -> None:
    """Regression: 429s during sign-in must reach the per-user log (they were missed once)."""
    calls = {"n": 0}

    async def route(request: web.Request) -> web.Response:
        calls["n"] += 1
        if request.path.endswith("/otp/request") and calls["n"] == 1:
            body = {"error": {"code": "RATE_LIMITED", "message": "x", "retry_after_ms": 50}}
            return web.json_response(body, status=429, headers={"Retry-After": "1"})
        if request.path.endswith("/otp/request"):
            return web.json_response({"request_id": "r1", "dev_otp": "123456"})
        return web.json_response({"session_token": "s.t", "user_public_id": "u_1"})

    from sim.api import login
    from sim.clients.human import _log

    app = web.Application()
    app.router.add_route("*", "/{tail:.*}", route)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    port = site._server.sockets[0].getsockname()[1]  # type: ignore[union-attr]
    ident = Identity("h", "human", "human", "human", "+917000000001", "d", "1.2.3.4", "ua")
    log = _log(ident)
    try:
        async with make_session(f"http://127.0.0.1:{port}", 4, 5) as http:
            ok = await login(
                Api(http, Stats(), "d"),
                ident,
                random.Random(1),  # noqa: S311
                typing_s=(0, 0),
                polite=True,
            )
    finally:
        await runner.cleanup()
    assert ok
    assert log["rate_limited"] == 1 and log["rate_limited_with_retry_after"] == 1
    assert log["retried_after_429_ok"] == 1 and log["requests"] == 3
