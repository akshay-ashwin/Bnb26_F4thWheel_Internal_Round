import asyncio

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from app.db.session import SessionLocal
from tests.conftest import ADMIN, make_drop, make_users


async def test_health(client):
    r = await client.get("/health")
    assert r.status_code == 200
    body = r.json()
    assert body["postgres"] == "ok" and "redis" in body


async def test_auth_flow(client, demo_drop_id):
    r = await client.post("/api/auth/otp/request", json={"phone": "+91 98765 43210"})
    assert r.status_code == 200
    otp = r.json()["demo_otp"]
    assert (await client.post("/api/auth/otp/verify", json={"phone": "+919876543210", "otp": "000000" if otp != "000000" else "111111"})).status_code == 401
    r = await client.post("/api/auth/otp/verify", json={"phone": "+919876543210", "otp": otp})
    assert r.status_code == 200
    token = r.json()["session_token"]
    # OTP is single-use
    assert (await client.post("/api/auth/otp/verify", json={"phone": "+919876543210", "otp": otp})).status_code == 401
    h = {"Authorization": f"Bearer {token}"}
    me = await client.get(f"/api/drops/{demo_drop_id}/me", headers=h)
    assert me.status_code == 200 and me.json()["entry"] is None
    assert "phone" not in me.text
    assert (await client.get(f"/api/drops/{demo_drop_id}/me")).status_code == 401
    assert (await client.get(f"/api/drops/{demo_drop_id}/me", headers={"Authorization": "Bearer nope"})).status_code == 401
    assert (await client.post("/api/auth/otp/request", json={"phone": "abc"})).status_code == 422


async def test_get_drop(client, demo_drop_id):
    r = await client.get(f"/api/drops/{demo_drop_id}")
    assert r.status_code == 200
    d = r.json()
    assert d["total_seats"] == 500 and d["remaining_seats"] == 500 and d["mode"] == "fifo"
    assert (await client.get("/api/drops/999999")).status_code == 404


async def test_one_entry_per_user(client, demo_drop_id):
    (u,) = await make_users(1)
    r1 = await client.post(f"/api/drops/{demo_drop_id}/entries", headers=u["headers"])
    r2 = await client.post(f"/api/drops/{demo_drop_id}/entries", headers=u["headers"])
    assert (r1.status_code, r2.status_code) == (201, 200)
    assert r1.json() == r2.json()
    # concurrent duplicates
    (v,) = await make_users(1)
    rs = await asyncio.gather(*[client.post(f"/api/drops/{demo_drop_id}/entries", headers=v["headers"]) for _ in range(10)])
    assert sorted({r.status_code for r in rs}) in ([200, 201], [201])
    async with SessionLocal() as db:
        n = (await db.execute(text("SELECT count(*) FROM entries WHERE user_id = :u"), {"u": v["id"]})).scalar_one()
        assert n == 1
        # DB constraint is the final guard
        with pytest.raises(IntegrityError):
            await db.execute(text("INSERT INTO entries (drop_id, user_id) VALUES (:d, :u)"), {"d": demo_drop_id, "u": v["id"]})


async def test_entry_requires_open_drop(client):
    closed = await make_drop(5, status="closed")
    (u,) = await make_users(1)
    assert (await client.post(f"/api/drops/{closed}/entries", headers=u["headers"])).status_code == 409


async def test_fifo_claim_order(client):
    drop = await make_drop(3)
    users = await make_users(3, drop)
    seats = []
    for u in users:  # sequential claims in entry order
        r = await client.post(f"/api/drops/{drop}/claim", headers=u["headers"])
        assert r.status_code == 200
        seats.append(r.json()["seat_number"])
    assert seats == [1, 2, 3]
    me = await client.get(f"/api/drops/{drop}/me", headers=users[1]["headers"])
    assert me.json()["allocation"]["seat_number"] == 2 and me.json()["entry"]["queue_position"] == 2


async def test_claim_requires_entry(client):
    drop = await make_drop(3)
    (u,) = await make_users(1)
    assert (await client.post(f"/api/drops/{drop}/claim", headers=u["headers"])).status_code == 403
    assert (await client.post(f"/api/drops/{drop}/claim")).status_code == 401


async def test_duplicate_claim(client):
    drop = await make_drop(5)
    (u,) = await make_users(1, drop)
    assert (await client.post(f"/api/drops/{drop}/claim", headers=u["headers"])).status_code == 200
    r = await client.post(f"/api/drops/{drop}/claim", headers=u["headers"])
    assert r.status_code == 409 and r.json()["error"]["code"] == "ALREADY_CLAIMED"
    assert (await client.get(f"/api/admin/drops/{drop}/integrity", headers=ADMIN)).json()["allocated_seats"] == 1


async def test_idempotent_claim(client):
    drop = await make_drop(5)
    (u,) = await make_users(1, drop)
    h = {**u["headers"], "Idempotency-Key": "k1"}
    r1 = await client.post(f"/api/drops/{drop}/claim", headers=h)
    r2 = await client.post(f"/api/drops/{drop}/claim", headers=h)
    assert r1.status_code == r2.status_code == 200 and r1.json() == r2.json()
    # concurrent repeats with a fresh key
    (v,) = await make_users(1, drop)
    hv = {**v["headers"], "Idempotency-Key": "k2"}
    rs = await asyncio.gather(*[client.post(f"/api/drops/{drop}/claim", headers=hv) for _ in range(20)])
    assert {r.status_code for r in rs} == {200} and len({r.json()["seat_number"] for r in rs}) == 1
    integ = (await client.get(f"/api/admin/drops/{drop}/integrity", headers=ADMIN)).json()
    assert integ["allocated_seats"] == 2 and integ["duplicate_allocation_count"] == 0


async def test_two_users_never_share_seat(client):
    drop = await make_drop(10)
    users = await make_users(10, drop)
    rs = await asyncio.gather(*[client.post(f"/api/drops/{drop}/claim", headers=u["headers"]) for u in users])
    seats = [r.json()["seat_number"] for r in rs if r.status_code == 200]
    assert len(seats) == len(set(seats)) == 10


async def test_more_claims_than_seats(client):
    drop = await make_drop(5)
    users = await make_users(20, drop)
    rs = await asyncio.gather(*[client.post(f"/api/drops/{drop}/claim", headers=u["headers"]) for u in users])
    ok = [r for r in rs if r.status_code == 200]
    assert len(ok) == 5
    assert all(r.json()["error"]["code"] == "SOLD_OUT" for r in rs if r.status_code != 200)
    integ = (await client.get(f"/api/admin/drops/{drop}/integrity", headers=ADMIN)).json()
    assert integ["allocated_seats"] == 5 and integ["remaining_seats"] == 0 and not integ["overselling_occurred"]


async def test_500_seat_drop_never_exceeds_500(client, demo_drop_id):
    users = await make_users(800, demo_drop_id)
    rs = await asyncio.gather(*[client.post(f"/api/drops/{demo_drop_id}/claim", headers=u["headers"]) for u in users])
    assert sum(r.status_code == 200 for r in rs) == 500
    async with SessionLocal() as db:
        assert (await db.execute(text("SELECT count(*) FROM allocations WHERE drop_id = :d"), {"d": demo_drop_id})).scalar_one() == 500
        # The DB itself rejects a 501st allocation: every seat is taken and every user constraint holds.
        extra_seat = (await db.execute(text("INSERT INTO seats (drop_id, seat_number) VALUES (:d, 501) RETURNING id"), {"d": demo_drop_id})).scalar_one()
        await db.commit()
        unallocated = (await db.execute(text("SELECT u.id FROM users u WHERE NOT EXISTS (SELECT 1 FROM allocations a WHERE a.user_id = u.id) LIMIT 1"))).scalar_one()
        taken_seat = (await db.execute(text("SELECT seat_id FROM allocations LIMIT 1"))).scalar_one()
        with pytest.raises(IntegrityError):  # same seat twice
            await db.execute(text("INSERT INTO allocations (drop_id, seat_id, user_id) VALUES (:d, :s, :u)"), {"d": demo_drop_id, "s": taken_seat, "u": unallocated})
        await db.rollback()
        # clean up the extra seat so the physical-seat count stays 500
        await db.execute(text("DELETE FROM seats WHERE id = :s"), {"s": extra_seat})
        await db.commit()
    integ = (await client.get(f"/api/admin/drops/{demo_drop_id}/integrity", headers=ADMIN)).json()
    assert integ["total_seats"] == integ["physical_seats"] == 500
    assert integ["allocated_seats"] == 500 and integ["remaining_seats"] == 0
    assert integ["duplicate_allocation_count"] == 0 and integ["unique_allocated_users"] == 500
    assert integ["overselling_occurred"] is False


async def test_integrity_endpoint(client, demo_drop_id):
    assert (await client.get(f"/api/admin/drops/{demo_drop_id}/integrity")).status_code == 403
    assert (await client.get(f"/api/admin/drops/{demo_drop_id}/integrity", headers={"X-Admin-Key": "x"})).status_code == 403
    d = (await client.get(f"/api/admin/drops/{demo_drop_id}/integrity", headers=ADMIN)).json()
    assert d["total_seats"] == 500 and d["allocated_seats"] == 0 and d["remaining_seats"] == 500
    assert d["overselling_occurred"] is False and d["invariant_allocated_lte_total"] is True
