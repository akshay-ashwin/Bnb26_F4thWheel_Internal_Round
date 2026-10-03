import asyncio
import hashlib
import os
import random
import time

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from app.config import get_settings
from app.db.session import SessionLocal
from app.utils import draw as D
from app.utils.admission import sign_token
from tests.conftest import ADMIN, make_drop, make_users

pytestmark = pytest.mark.asyncio


async def admin(client, drop, action, expect=200):
    r = await client.post(f"/api/admin/drops/{drop}/{action}", headers=ADMIN)
    assert r.status_code == expect, (action, r.status_code, r.text)
    return r.json()


async def to_drawn(client, seats, n_users, users=None):
    drop = await make_drop(seats, mode="fair")
    users = users or await make_users(n_users, drop)
    await admin(client, drop, "commit")
    await admin(client, drop, "freeze")
    await admin(client, drop, "reveal")
    await admin(client, drop, "draw")
    return drop, users


async def to_claimable(client, seats, n_users):
    drop, users = await to_drawn(client, seats, n_users)
    await admin(client, drop, "open-claims")
    return drop, users


async def results(client, drop):
    rows, off = [], 0
    while True:
        r = (await client.get(f"/api/drops/{drop}/fairness/results?offset={off}&limit=1000")).json()
        rows += r["results"]
        off += 1000
        if off >= r["total"]:
            return rows


async def winners_and_losers(client, drop, users):
    pub = {u["pub"]: u for u in users}
    rows = await results(client, drop)
    return [pub[r["user_public_id"]] for r in rows if r["is_winner"]], [pub[r["user_public_id"]] for r in rows if not r["is_winner"]]


async def token(client, drop, u, expect=200):
    r = await client.post(f"/api/drops/{drop}/admission-token", headers=u["headers"])
    assert r.status_code == expect, r.text
    return r.json().get("admission_token")


def claim(client, drop, u, tok, key=None):
    h = {**u["headers"], "X-Admission-Token": tok or ""}
    if key:
        h["Idempotency-Key"] = key
    return client.post(f"/api/drops/{drop}/claim", headers=h)


async def allocations(drop):
    async with SessionLocal() as db:
        return (await db.execute(text("SELECT count(*) FROM allocations WHERE drop_id=:d"), {"d": drop})).scalar_one()


# ---------------------------------------------------------------- ENTRY
async def test_one_entry_per_user_and_none_after_freeze(client):
    drop = await make_drop(5, mode="fair")
    (u,) = await make_users(1)
    rs = await asyncio.gather(*[client.post(f"/api/drops/{drop}/entries", headers=u["headers"]) for _ in range(8)])
    assert {r.status_code for r in rs} <= {200, 201}
    async with SessionLocal() as db:
        assert (await db.execute(text("SELECT count(*) FROM entries WHERE drop_id=:d"), {"d": drop})).scalar_one() == 1
    await admin(client, drop, "freeze")
    (v,) = await make_users(1)
    r = await client.post(f"/api/drops/{drop}/entries", headers=v["headers"])
    assert r.status_code == 409 and r.json()["error"]["code"] == "ENTRY_CLOSED"
    # the DB itself refuses late entries too (defence in depth, bypassing the API)
    async with SessionLocal() as db:
        with pytest.raises(DBAPIError):
            await db.execute(text("INSERT INTO entries (drop_id, user_id) VALUES (:d,:u)"), {"d": drop, "u": v["id"]})


async def test_entry_requires_auth(client):
    drop = await make_drop(5, mode="fair")
    assert (await client.post(f"/api/drops/{drop}/entries")).status_code == 401


async def test_freeze_races_with_entries(client):
    drop = await make_drop(5, mode="fair")
    users = await make_users(60)
    tasks = [client.post(f"/api/drops/{drop}/entries", headers=u["headers"]) for u in users]
    tasks.insert(30, client.post(f"/api/admin/drops/{drop}/freeze", headers=ADMIN))
    rs = await asyncio.gather(*tasks)
    frozen = next(r for r in rs if r.request.url.path.endswith("/freeze")).json()
    async with SessionLocal() as db:
        n = (await db.execute(text("SELECT count(*) FROM entries WHERE drop_id=:d"), {"d": drop})).scalar_one()
    assert n == frozen["frozen_entry_count"]  # nothing slipped in after the freeze


# ---------------------------------------------------------------- COMMITMENT
async def test_commitment_reveal_and_immutability(client):
    drop = await make_drop(5, mode="fair")
    await make_users(3, drop)
    c = await admin(client, drop, "commit")
    pub = (await client.get(f"/api/drops/{drop}/fairness")).json()
    assert pub["seed"] is None and pub["seed_revealed"] is False and pub["seed_commitment"] == c["seed_commitment"]
    assert "draw_seed" not in str(c)
    # cannot replace the seed
    r = await client.post(f"/api/admin/drops/{drop}/commit", headers=ADMIN)
    assert r.status_code == 409 and r.json()["error"]["code"] == "SEED_ALREADY_COMMITTED"
    # cannot reveal/draw before freeze
    await admin(client, drop, "reveal", 409)
    await admin(client, drop, "draw", 409)
    await admin(client, drop, "freeze")
    await admin(client, drop, "draw", 409)  # not revealed yet
    rv = await admin(client, drop, "reveal")
    assert hashlib.sha256(rv["seed"].encode()).hexdigest() == c["seed_commitment"]
    pub = (await client.get(f"/api/drops/{drop}/fairness")).json()
    assert pub["seed"] == rv["seed"] and hashlib.sha256(pub["seed"].encode()).hexdigest() == pub["seed_commitment"]
    # DB triggers/constraints: seed, commitment and entry hash are write-once, and seed must hash to commitment
    async with SessionLocal() as db:
        for sql in ("UPDATE drops SET draw_seed = repeat('a',64) WHERE id=:d",
                    "UPDATE drops SET seed_commitment = repeat('b',64) WHERE id=:d",
                    "UPDATE drops SET entry_set_hash = repeat('c',64) WHERE id=:d"):
            with pytest.raises(DBAPIError):
                await db.execute(text(sql), {"d": drop})
            await db.rollback()


async def test_seed_commitment_requires_fair_drop(client, demo_drop_id):
    await admin(client, demo_drop_id, "commit", 409)


async def test_admin_endpoints_need_key(client):
    drop = await make_drop(5, mode="fair")
    for a in ("commit", "freeze", "reveal", "draw", "open-claims", "close"):
        assert (await client.post(f"/api/admin/drops/{drop}/{a}")).status_code == 403


# ---------------------------------------------------------------- ENTRY HASH
async def test_entry_set_hash_is_deterministic_and_public(client):
    ids = [f"u_{i:04d}" for i in range(50)]
    shuffled = ids[:]
    random.shuffle(shuffled)
    assert D.entry_set_hash(7, ids) == D.entry_set_hash(7, shuffled)
    assert D.entry_set_hash(7, ids) != D.entry_set_hash(8, ids)
    assert D.entry_set_hash(7, ids) != D.entry_set_hash(7, ids[:-1])

    drop = await make_drop(5, mode="fair")
    users = await make_users(20, drop)
    f = await admin(client, drop, "freeze")
    listing = (await client.get(f"/api/drops/{drop}/fairness/entries")).json()
    assert listing["user_public_ids"] == sorted(u["pub"] for u in users)
    assert D.entry_set_hash(drop, listing["user_public_ids"]) == f["entry_set_hash"]
    # explicit known-answer for the canonical encoding
    canon = f"fairdrop:entryset:v1\ndrop_id=1\ncount=2\nu_a\nu_b"
    assert D.entry_set_hash(1, ["u_b", "u_a"]) == hashlib.sha256(canon.encode()).hexdigest()
    # no phone data in public output
    assert "phone" not in (await client.get(f"/api/drops/{drop}/fairness/entries")).text


async def test_freeze_is_not_repeatable(client):
    drop = await make_drop(5, mode="fair")
    await admin(client, drop, "freeze")
    await admin(client, drop, "freeze", 409)


# ---------------------------------------------------------------- DRAW
async def test_draw_is_pure_function_of_seed_and_set():
    seed = D.new_seed()
    ids = [f"u_{i:05d}" for i in range(300)]
    base = D.rank_entries(seed, 1, ids, 10)
    for _ in range(3):
        sh = ids[:]
        random.shuffle(sh)
        assert D.rank_entries(seed, 1, sh, 10) == base
    other = D.rank_entries(D.new_seed(), 1, ids, 10)
    assert [r.user_public_id for r in other] != [r.user_public_id for r in base]
    assert len({r.user_public_id for r in base if r.is_winner}) == sum(r.is_winner for r in base) == 10
    # known-answer vector for the documented scoring encoding
    import hmac
    assert D.score("ab" * 32, 3, "u_x") == hmac.new(("ab" * 32).encode(), b"fairdrop:draw:v1|3|u_x", hashlib.sha256).hexdigest()


async def test_draw_matches_independent_recomputation_and_ignores_timing(client):
    """Users enter in random order, with retries/extra tabs and wildly different request counts. The ranking must
    still equal HMAC ranking recomputed from only (seed, drop_id, public ids)."""
    drop = await make_drop(20, mode="fair")
    users = await make_users(120)
    spam = {u["pub"]: random.choice([1, 1, 2, 5, 15]) for u in users}
    random.shuffle(users)
    reqs = [u for u in users for _ in range(spam[u["pub"]])]
    random.shuffle(reqs)
    await asyncio.gather(*[client.post(f"/api/drops/{drop}/entries", headers=u["headers"]) for u in reqs])
    await admin(client, drop, "commit")
    await admin(client, drop, "freeze")
    seed = (await admin(client, drop, "reveal"))["seed"]
    await admin(client, drop, "draw")
    got = await results(client, drop)
    expected = D.rank_entries(seed, drop, [u["pub"] for u in users], 20)
    assert [(r["rank"], r["user_public_id"], r["score"], r["is_winner"]) for r in got] == \
           [(e.rank, e.user_public_id, e.score, e.is_winner) for e in expected]
    # shuffling arrival timestamps cannot change anything: scores don't use them
    assert sum(r["is_winner"] for r in got) == 20 and len({r["user_public_id"] for r in got}) == 120
    pub = (await client.get(f"/api/drops/{drop}/fairness")).json()
    assert pub["draw_status"] == "complete" and pub["winner_count"] == 20


async def test_fewer_entries_than_seats_everyone_wins(client):
    drop, users = await to_drawn(client, 10, 4)
    assert len((await winners_and_losers(client, drop, users))[0]) == 4


async def test_draw_refuses_tampered_entry_set(client):
    drop = await make_drop(5, mode="fair")
    await make_users(10, drop)
    await admin(client, drop, "commit")
    await admin(client, drop, "freeze")
    await admin(client, drop, "reveal")
    async with SessionLocal() as db:  # bypass API+trigger by dropping one frozen entry
        await db.execute(text("DELETE FROM entries WHERE id = (SELECT min(id) FROM entries WHERE drop_id=:d)"), {"d": drop})
        await db.commit()
    r = await client.post(f"/api/admin/drops/{drop}/draw", headers=ADMIN)
    assert r.status_code == 409 and r.json()["error"]["code"] == "ENTRY_SET_MISMATCH"


async def test_results_hidden_until_drawn_and_no_pii(client):
    drop = await make_drop(5, mode="fair")
    users = await make_users(8, drop)
    assert (await client.get(f"/api/drops/{drop}/fairness/results")).status_code == 409
    assert (await client.get(f"/api/drops/{drop}/fairness/entries")).status_code == 409
    await admin(client, drop, "commit"); await admin(client, drop, "freeze"); await admin(client, drop, "reveal"); await admin(client, drop, "draw")
    body = (await client.get(f"/api/drops/{drop}/fairness/results")).text + (await client.get(f"/api/drops/{drop}/fairness")).text
    assert "phone" not in body and "otp" not in body.lower()
    me = (await client.get(f"/api/drops/{drop}/me", headers=users[0]["headers"])).json()
    assert me["draw"]["rank"] >= 1 and isinstance(me["draw"]["is_winner"], bool)


# ---------------------------------------------------------------- TOKENS + CLAIMS
async def test_winner_claims_non_winner_cannot(client):
    drop, users = await to_claimable(client, 5, 12)
    win, lose = await winners_and_losers(client, drop, users)
    assert len(win) == 5 and len(lose) == 7
    tok = await token(client, drop, win[0])
    r = await claim(client, drop, win[0], tok)
    assert r.status_code == 200 and r.json()["seat_number"] >= 1
    me = (await client.get(f"/api/drops/{drop}/me", headers=win[0]["headers"])).json()
    assert me["allocation"]["seat_number"] == r.json()["seat_number"]
    # non-winner: no token available, and claim is refused even with a winner's token
    assert (await client.post(f"/api/drops/{drop}/admission-token", headers=lose[0]["headers"])).status_code == 403
    wtok = await token(client, drop, win[1])
    for t in (None, wtok):
        r = await claim(client, drop, lose[0], t)
        assert r.status_code == 403 and r.json()["error"]["code"] == "NOT_A_WINNER"
    assert await allocations(drop) == 1


async def test_token_requires_claimable_and_auth(client):
    drop, users = await to_drawn(client, 5, 8)
    win, _ = await winners_and_losers(client, drop, users)
    r = await client.post(f"/api/drops/{drop}/admission-token", headers=win[0]["headers"])
    assert r.status_code == 409 and r.json()["error"]["code"] == "DROP_NOT_CLAIMABLE"
    r = await claim(client, drop, win[0], "x")
    assert r.status_code == 409 and r.json()["error"]["code"] == "DROP_NOT_CLAIMABLE"
    assert (await client.post(f"/api/drops/{drop}/admission-token")).status_code == 401
    await admin(client, drop, "open-claims")
    assert (await client.post(f"/api/drops/{drop}/claim", headers=win[0]["headers"])).json()["error"]["code"] == "ADMISSION_TOKEN_REQUIRED"


async def test_invalid_and_tampered_tokens_rejected(client):
    drop, users = await to_claimable(client, 5, 8)
    win, _ = await winners_and_losers(client, drop, users)
    u = win[0]
    tok = await token(client, drop, u)
    payload, sig = tok.split(".")
    flipped = payload[:-2] + ("AA" if payload[-2:] != "AA" else "BB")
    for bad in ("garbage", "a.b", flipped + "." + sig, payload + "." + sig[::-1], payload):
        r = await claim(client, drop, u, bad)
        assert r.status_code == 401 and r.json()["error"]["code"] == "ADMISSION_TOKEN_INVALID", bad
    assert await allocations(drop) == 0
    assert (await claim(client, drop, u, tok)).status_code == 200


async def test_forged_claims_for_other_user_or_session_rejected(client):
    drop, users = await to_claimable(client, 5, 10)
    win, _ = await winners_and_losers(client, drop, users)
    a, b = win[0], win[1]
    ta = await token(client, drop, a)
    r = await claim(client, drop, b, ta)  # other user presents A's token
    assert r.status_code == 403 and r.json()["error"]["code"] == "ADMISSION_TOKEN_WRONG_USER"
    # same user, different session
    async with SessionLocal() as db:
        await db.execute(text("INSERT INTO sessions (user_id, token_hash, expires_at) VALUES (:u, :h, now() + interval '1 hour')"),
                         {"u": a["id"], "h": __import__("app.utils.security", fromlist=["x"]).keyed_hash("sess:second")})
        await db.commit()
    r = await claim(client, drop, {**a, "headers": {"Authorization": "Bearer second"}}, ta)
    assert r.status_code == 403 and r.json()["error"]["code"] == "ADMISSION_TOKEN_WRONG_SESSION"
    # token for another drop
    other = await make_drop(5, mode="fair")
    forged = sign_token(jti="x" * 32, drop_id=other, user_public_id=a["pub"], session_id=1, exp=int(time.time()) + 60)
    r = await claim(client, drop, a, forged)
    assert r.status_code == 403 and r.json()["error"]["code"] == "ADMISSION_TOKEN_WRONG_DROP"
    assert await allocations(drop) == 0
    assert (await claim(client, drop, a, ta)).status_code == 200


async def test_expired_token_rejected_and_can_be_refreshed(client, monkeypatch):
    drop, users = await to_claimable(client, 5, 8)
    win, _ = await winners_and_losers(client, drop, users)
    u = win[0]
    monkeypatch.setattr(get_settings(), "admission_token_ttl_seconds", -5)
    old = await token(client, drop, u)
    r = await claim(client, drop, u, old)
    assert r.status_code == 401 and r.json()["error"]["code"] == "ADMISSION_TOKEN_EXPIRED"
    assert await allocations(drop) == 0
    monkeypatch.undo()
    fresh = await token(client, drop, u)  # unclaimed expired offer can be refreshed
    assert (await claim(client, drop, u, fresh)).status_code == 200
    assert await allocations(drop) == 1


async def test_replay_and_superseded_rejected(client):
    drop, users = await to_claimable(client, 5, 8)
    win, _ = await winners_and_losers(client, drop, users)
    u = win[0]
    t1 = await token(client, drop, u)
    t2 = await token(client, drop, u)  # reissue invalidates t1
    r = await claim(client, drop, u, t1)
    assert r.status_code == 409 and r.json()["error"]["code"] == "ADMISSION_TOKEN_SUPERSEDED"
    assert (await claim(client, drop, u, t2)).status_code == 200
    r = await claim(client, drop, u, t2)  # replay
    assert r.status_code == 409 and r.json()["error"]["code"] == "ADMISSION_TOKEN_USED"
    assert (await client.post(f"/api/drops/{drop}/admission-token", headers=u["headers"])).status_code == 409
    assert await allocations(drop) == 1


async def test_idempotent_claim_replay(client):
    drop, users = await to_claimable(client, 5, 8)
    win, _ = await winners_and_losers(client, drop, users)
    u = win[0]
    tok = await token(client, drop, u)
    r1 = await claim(client, drop, u, tok, key="k1")
    r2 = await claim(client, drop, u, tok, key="k1")
    assert r1.status_code == r2.status_code == 200 and r1.json() == r2.json()
    t1 = await token(client, drop, win[1])
    rs = await asyncio.gather(*[claim(client, drop, win[1], t1, key="k2") for _ in range(10)])
    assert {r.status_code for r in rs} == {200} and len({r.json()["seat_number"] for r in rs}) == 1
    assert await allocations(drop) == 2
    # a failed attempt with a key is not cached: retry with the right token works
    w3 = win[2]
    assert (await claim(client, drop, w3, "bad", key="k3")).status_code == 401
    assert (await claim(client, drop, w3, await token(client, drop, w3), key="k3")).status_code == 200


async def test_concurrent_same_user_claims_one_seat(client):
    drop, users = await to_claimable(client, 5, 8)
    win, _ = await winners_and_losers(client, drop, users)
    u = win[0]
    tok = await token(client, drop, u)
    rs = await asyncio.gather(*[claim(client, drop, u, tok, key=f"k{i}") for i in range(15)])
    assert sum(r.status_code == 200 for r in rs) == 1
    assert {r.json()["error"]["code"] for r in rs if r.status_code != 200} == {"ADMISSION_TOKEN_USED"}
    assert await allocations(drop) == 1


async def test_claims_closed_after_close(client):
    drop, users = await to_claimable(client, 5, 8)
    win, _ = await winners_and_losers(client, drop, users)
    tok = await token(client, drop, win[0])
    await admin(client, drop, "close")
    r = await claim(client, drop, win[0], tok)
    assert r.status_code == 409 and r.json()["error"]["code"] == "DROP_NOT_CLAIMABLE"
    assert await allocations(drop) == 0
    integ = (await client.get(f"/api/admin/drops/{drop}/integrity", headers=ADMIN)).json()
    assert integ["remaining_seats"] == 5 and not integ["overselling_occurred"]  # unclaimed seats stay clean


async def test_fair_drop_rejects_fifo_semantics_and_fifo_unaffected(client):
    # fifo claim path keeps working with no token / on a fifo drop
    fifo = await make_drop(3)
    (u,) = await make_users(1, fifo)
    assert (await client.post(f"/api/drops/{fifo}/claim", headers=u["headers"])).status_code == 200
    fair = await make_drop(3, mode="fair")
    (v,) = await make_users(1, fair)
    r = await client.post(f"/api/drops/{fair}/claim", headers=v["headers"])
    assert r.status_code == 409 and r.json()["error"]["code"] == "DROP_NOT_CLAIMABLE"


# ---------------------------------------------------------------- 500-SEAT CRITICAL REGRESSION
async def test_500_seat_fair_drop_concurrent_claims_exactly_500(client):
    """800 entrants -> 500 winners. All 800 claim at once (each winner twice, non-winners with stolen tokens)."""
    drop, users = await to_claimable(client, 500, 800)
    win, lose = await winners_and_losers(client, drop, users)
    assert len(win) == 500 and len(lose) == 300
    toks = {u["pub"]: t for u, t in zip(win, await asyncio.gather(*[token(client, drop, u) for u in win]))}
    jobs = [claim(client, drop, u, toks[u["pub"]]) for u in win for _ in range(2)]
    jobs += [claim(client, drop, u, toks[win[i % 500]["pub"]]) for i, u in enumerate(lose)]
    rs = await asyncio.gather(*jobs)
    assert sum(r.status_code == 200 for r in rs) == 500
    assert await allocations(drop) == 500
    integ = (await client.get(f"/api/admin/drops/{drop}/integrity", headers=ADMIN)).json()
    assert integ["allocated_seats"] == 500 and integ["remaining_seats"] == 0
    assert integ["duplicate_allocation_count"] == 0 and integ["unique_allocated_users"] == 500
    assert integ["overselling_occurred"] is False
    assert {u["id"] for u in lose}.isdisjoint(await _allocated_users(drop))


async def _allocated_users(drop):
    async with SessionLocal() as db:
        return set((await db.execute(text("SELECT user_id FROM allocations WHERE drop_id=:d"), {"d": drop})).scalars())


async def test_500_seats_never_oversold_even_with_more_than_500_valid_winners(client):
    """Defence in depth: force 650 'winners' with valid tokens (bypassing the draw's N-winner cap)."""
    drop, users = await to_claimable(client, 500, 700)
    async with SessionLocal() as db:
        await db.execute(text("UPDATE draw_results SET is_winner = true WHERE drop_id=:d AND rank <= 650"), {"d": drop})
        await db.commit()
    pub = {u["pub"]: u for u in users}
    rows = [r for r in await results(client, drop) if r["rank"] <= 650]
    win = [pub[r["user_public_id"]] for r in rows]
    toks = await asyncio.gather(*[token(client, drop, u) for u in win])
    rs = await asyncio.gather(*[claim(client, drop, u, t) for u, t in zip(win, toks)])
    ok = sum(r.status_code == 200 for r in rs)
    assert ok == 500 and await allocations(drop) == 500
    assert {r.json()["error"]["code"] for r in rs if r.status_code != 200} == {"SOLD_OUT"}
    # the 150 who lost the race keep their (unconsumed) tokens and still hold no seat
    integ = (await client.get(f"/api/admin/drops/{drop}/integrity", headers=ADMIN)).json()
    assert integ["allocated_seats"] == 500 and integ["overselling_occurred"] is False


# ---------------------------------------------------------------- DEMO HELPERS
async def test_demo_helpers_end_to_end(client):
    r = await client.post("/api/admin/drops", headers=ADMIN, json={"name": "t-demo", "total_seats": 7})
    assert r.status_code == 201 and r.json()["mode"] == "fair" and r.json()["remaining_seats"] == 7
    drop = r.json()["id"]
    assert (await client.post("/api/admin/demo/users", json={"count": 3})).status_code == 403
    r = await client.post("/api/admin/demo/users", headers=ADMIN, json={"count": 30, "drop_id": drop})
    assert r.status_code == 201 and len(r.json()["users"]) == 30
    for a in ("commit", "freeze", "reveal", "draw", "open-claims"):
        await admin(client, drop, a)
    top = (await client.get(f"/api/drops/{drop}/fairness/results?limit=1")).json()["results"][0]
    u = next(x for x in r.json()["users"] if x["user_public_id"] == top["user_public_id"])
    h = {"Authorization": "Bearer " + u["session_token"]}
    tok = (await client.post(f"/api/drops/{drop}/admission-token", headers=h)).json()["admission_token"]
    c = await client.post(f"/api/drops/{drop}/claim", headers={**h, "X-Admission-Token": tok})
    assert c.status_code == 200
