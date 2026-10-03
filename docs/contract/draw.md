# Fair Draw: byte-exact definition

The draw is a pure function of **(seed, drop id, the frozen set of user public ids)**. Arrival time,
request counts, IP and device are never inputs. This is what `api/app/draw_math.py` implements and
what a browser or evaluator script must reproduce.

| Item | Definition |
| --- | --- |
| `seed` | 32 random bytes from the OS CSPRNG, written as 64 lowercase hex characters |
| `seed_commit` | `hex(SHA-256(seed_bytes))` (hash the 32 raw bytes, not the hex text). Published at drop creation, before registration opens |
| `user_public_id` | 16 random bytes, base32, lowercase, no padding (26 characters). Random; reveals nothing personal |
| Eligible entries | entries of the drop that are `REGISTERED` when the entry set is frozen |
| `entry_set_hash` | `hex(SHA-256(UTF-8("\n".join(sorted(eligible_public_ids)))))`: plain byte order, no trailing newline. Published when the draw starts, before the ranks are written |
| Rank key | `hex(HMAC-SHA256(key = seed_bytes, message = UTF-8(drop_id + "\|" + user_public_id)))`; `drop_id` is the lowercase canonical UUID string |
| Ranking | ascending by rank key (rank 1 = smallest); ties (practically impossible) by `user_public_id` |
| Offers | ranks `1..capacity` get an offer (`OFFERED`, or `STEP_UP_REQUIRED` if their risk score is at least the step-up threshold); everyone else is `WAITLISTED` in rank order |

`algorithm` in the proof is the string `HMAC_SHA256(seed, drop_id|user_public_id) asc; ties by
user_public_id`. (The design document writes `drop_id‖user_public_id`; the explicit `|` separator
removes any ambiguity about how the two are joined.)

## Where to get the proof

* `GET /api/admin/drops/{id}/draw-proof` (admin) returns `{seed_commit, seed, entry_set_hash, algorithm, drop_id, run_no}`.
* `GET /api/drops/{id}/draw-proof` (public, only after the draw) adds `eligible_public_ids` (sorted)
  and `ranked_public_ids` (rank 1 first). Before the draw both answer `409 INVALID_TRANSITION`.

## Verifying (any language)

1. `SHA-256(hex-decode(seed)) == seed_commit`
2. `SHA-256(join("\n", eligible_public_ids)) == entry_set_hash` (the list is already sorted)
3. Recompute every rank key as above, sort ascending, compare with `ranked_public_ids`.
4. The first `capacity` ids hold offers; the rest are the waitlist in that order.

Reference implementation: `reference_ranking()` in `api/tests/test_fair_draw.py` (hashlib and hmac only).
