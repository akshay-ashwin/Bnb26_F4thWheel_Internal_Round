"""Repeatable end-to-end Fair Draw demo against a running API (creates a fresh drop each run).

    python scripts/demo_fair_flow.py [--base http://localhost:8000] [--seats 500] [--users 2000] [--admin-key KEY]
Requires SIM_MODE=true on the server (demo users are generated through the admin API).
"""
import argparse
import os
import sys
from concurrent.futures import ThreadPoolExecutor

import httpx

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from verify_draw import verify  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://localhost:8000")
    ap.add_argument("--seats", type=int, default=500)
    ap.add_argument("--users", type=int, default=2000)
    ap.add_argument("--admin-key", default=os.environ.get("ADMIN_API_KEY", "admin-dev-key"))
    a = ap.parse_args()
    c = httpx.Client(base_url=a.base, timeout=120)
    adm = {"X-Admin-Key": a.admin_key}

    def step(title, r):
        assert r.status_code < 300, (title, r.status_code, r.text)
        print(f"[{title}] {r.status_code}")
        return r.json()

    drop = step("create fair drop", c.post("/api/admin/drops", headers=adm, json={"name": "Fair Draw Demo", "total_seats": a.seats}))
    did = drop["id"]
    users = step("generate+enter users", c.post("/api/admin/demo/users", headers=adm, json={"count": a.users, "drop_id": did}))["users"]
    cm = step("commit seed", c.post(f"/api/admin/drops/{did}/commit", headers=adm))
    print("   commitment:", cm["seed_commitment"])
    fr = step("freeze", c.post(f"/api/admin/drops/{did}/freeze", headers=adm))
    print("   entry_set_hash:", fr["entry_set_hash"], "entries:", fr["frozen_entry_count"])
    rv = step("reveal", c.post(f"/api/admin/drops/{did}/reveal", headers=adm))
    print("   seed:", rv["seed"])
    dr = step("draw", c.post(f"/api/admin/drops/{did}/draw", headers=adm))
    print("   winners:", dr["winner_count"])
    step("open claims", c.post(f"/api/admin/drops/{did}/open-claims", headers=adm))

    res = c.get(f"/api/drops/{did}/fairness/results", params={"limit": 5}).json()["results"]
    print("   top ranks:", [(r["rank"], r["user_public_id"]) for r in res])
    by_pub = {u["user_public_id"]: u for u in users}
    winners = [by_pub[r["user_public_id"]] for r in c.get(f"/api/drops/{did}/fairness/results", params={"limit": a.seats}).json()["results"] if r["is_winner"]]
    losers_rows = c.get(f"/api/drops/{did}/fairness/results", params={"offset": a.seats, "limit": 1}).json()["results"]

    def claim(u):
        h = {"Authorization": "Bearer " + u["session_token"]}
        t = httpx.post(f"{a.base}/api/drops/{did}/admission-token", headers=h, timeout=120).json()["admission_token"]
        return httpx.post(f"{a.base}/api/drops/{did}/claim", headers={**h, "X-Admission-Token": t}, timeout=120)

    first = claim(winners[0])
    print("[winner claim]", first.status_code, first.json())
    if losers_rows:
        l = by_pub[losers_rows[0]["user_public_id"]]
        r = httpx.post(f"{a.base}/api/drops/{did}/admission-token", headers={"Authorization": "Bearer " + l["session_token"]})
        print("[non-winner token request]", r.status_code, r.json()["error"]["code"])
    with ThreadPoolExecutor(64) as ex:
        codes = [r.status_code for r in ex.map(claim, winners[1:])]
    print(f"[all other winners claim concurrently] ok={codes.count(200)} other={len(codes) - codes.count(200)}")
    integ = step("integrity", c.get(f"/api/admin/drops/{did}/integrity", headers=adm))
    print("   ", integ)
    print("--- public verification ---")
    return 0 if verify(a.base, did) and not integ["overselling_occurred"] else 1


if __name__ == "__main__":
    sys.exit(main())
