"""Independent judge-side verification of a Fair Draw using ONLY public endpoints.

    python scripts/verify_draw.py [BASE_URL] DROP_ID
"""
import hashlib
import os
import sys

import httpx

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from app.utils import draw as D  # noqa: E402  (pure functions, no DB)


def paged(c, url, key, total_key="total", page=5000):
    out, off = [], 0
    while True:
        j = c.get(url, params={"offset": off, "limit": page}).json()
        out += j[key]
        off += page
        if off >= j[total_key]:
            return out


def verify(base: str, drop_id: int) -> bool:
    c = httpx.Client(base_url=base, timeout=60)
    f = c.get(f"/api/drops/{drop_id}/fairness").json()
    checks = {}
    checks["seed revealed"] = f["seed_revealed"]
    seed = f["seed"]
    checks["sha256(seed) == commitment"] = bool(seed) and hashlib.sha256(seed.encode()).hexdigest() == f["seed_commitment"]
    ids = paged(c, f"/api/drops/{drop_id}/fairness/entries", "user_public_ids")
    checks["entry-set hash recomputed"] = D.entry_set_hash(drop_id, ids) == f["entry_set_hash"]
    published = paged(c, f"/api/drops/{drop_id}/fairness/results", "results")
    expected = D.rank_entries(seed, drop_id, ids, f["total_seats"])
    checks["ranking reproduced exactly"] = [(r["rank"], r["user_public_id"], r["score"], r["is_winner"]) for r in published] == \
        [(e.rank, e.user_public_id, e.score, e.is_winner) for e in expected]
    checks["winners == min(seats, entries)"] = sum(r["is_winner"] for r in published) == min(f["total_seats"], len(ids))
    for k, v in checks.items():
        print(("PASS " if v else "FAIL ") + k)
    return all(checks.values())


if __name__ == "__main__":
    args = sys.argv[1:]
    base = args[0] if len(args) == 2 else "http://localhost:8000"
    sys.exit(0 if verify(base, int(args[-1])) else 1)
