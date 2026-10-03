# Fair Drop — Architecture, Fairness Design & Build Plan

Oct 3, 2026 · @saanvi

## 1. What the PS actually requires

The PS is not asking for a bot detector; it asks for a system where speed, volume and retries stop buying seats, and for proof of that. Every requirement reduces to one testable claim plus five supporting ones.

**Core claim:** a client's chance of a seat depends only on how many *distinct verified identities* it controls, never on how fast or how often it sends requests.

| PS area | What it really means | How we prove it |
| --- | --- | --- |
| High concurrency | Survive a 50,000-client burst without timeouts deciding outcomes | P95/P99 latency and error rate at peak, from the simulator |
| Abuse handling | Cheap requests are shed early; suspicious identities face friction, not silent wins | Blocked/throttled counts per layer, detection precision/recall against ground truth |
| Allocation integrity | 500 seats never become 501; one person never holds two | Integrity endpoint: oversold = 0, duplicates = 0, under concurrent claim storms |
| Session reliability | Refresh, reconnect, multi-tab and lost responses never lose or duplicate a seat | Chaos scenarios: kill responses, retry, open 5 tabs; state stays consistent |
| Adversarial testing | Attacks hit the same public API as real users | Simulator scenarios with labels the backend never sees |
| Fairness measurement | A number that shows bots gained no per-identity advantage | Advantage ratio ≈ 1.0 in Fair mode vs ≫ 1 in FIFO mode, same attack |

The winning move for the demo is a **before/after on the same backend**: one switch flips the drop between FIFO mode and Fair mode, the same attack runs against both, and the scorecard shows the difference.

## 2. Hardest engineering problems

The genuinely hard problem is Sybil resistance: no amount of rate limiting stops one attacker holding many identities, so the design must make extra identities the *only* lever and then make that lever expensive and visible.

| Problem | Why it is hard | Our answer in one line |
| --- | --- | --- |
| Scalability | 50,000 arrivals in seconds, plus bot floods 10–100× larger | Shed junk in Redis before it touches Postgres; make arrival time irrelevant so load can be spread over a window |
| Admission fairness | Any first-come rule rewards latency and automation | Registration window, then one random draw over entries |
| Bot / abuse resistance | Detection is probabilistic and bypassable | Detection adds friction and evidence; fairness does not depend on it |
| Identity farming (Sybil) | 10,000 clients can become 10,000 accounts | One entry per verified identity, identity cost, cluster flags with step-up at claim |
| Allocation integrity | Concurrent claims on the last seat; retries after timeouts | 500 physical seat rows, one atomic conditional update, unique constraints as backstop |
| Session reliability | Client state is lost on refresh, network drop, or lost response | Server holds all state; idempotent writes; one status endpoint rebuilds the UI |
| Fault tolerance | Redis or Postgres can fail mid-drop | Postgres is the only source of truth; failures cost availability, never integrity |
| Fairness measurement | "Fair" is easy to claim and hard to quantify | Compare each group's win rate to its share of valid entries, with a chance-only confidence band |

### Why FIFO is insufficient

- **It rewards latency.** A bot in the same cloud region reacts in \~5 ms; a human on mobile takes 1–3 s to tap. The first 500 slots fill with whoever is closest and fastest.
- **It rewards volume.** More connections means more lottery tickets in the race to be first, even with one account.
- **It rewards retries.** Every timeout a human hits is a slot a retrying bot takes.
- **It concentrates load.** Everyone must hit the server at the same millisecond, which is exactly when it is weakest, so outcomes are decided by which requests the overloaded server happens to accept.
- **Detection becomes load-bearing.** Under FIFO, every bot you miss wins. Under a lottery, a bot you miss gets one ticket, like everyone else.

FIFO stays in the build only as the baseline mode, so we can show it failing.

## 3. Fairness mechanism options

Only options that decouple *timing* from *outcome* and bind *one entry to one identity* survive the 10,000-client question; everything else is a variant of FIFO.

| Option | Speed advantage | Volume / retry advantage | Identity farming | Complexity | Demo value |
| --- | --- | --- | --- | --- | --- |
| 1. FIFO queue | Total: fastest wins | High: more connections, more chances to be first | Unaddressed | Low | Good only as the "before" |
| 2. Randomized allocation per request | None | High: each request is a lottery ticket | Unaddressed | Low | Weak: bots spam to win |
| 3. Admission window + lottery | None inside the window | None if entries are unique per identity | Each fake identity is a ticket | Medium | Strong: visible, explainable |
| 4. Signed one-entry admission tokens | None by itself | Stops replay and multi-tab duplication | Each fake identity gets a token | Medium | Supporting piece, not a mechanism |
| 5. Queue + randomized order ("randomized waiting room") | Small: must arrive before T0 | Low if one slot per identity | Each fake identity is a slot | Medium-high (live queue drain) | Strong but harder to build |
| 6. Window + identity-bound lottery + signed tokens + risk-gated claim (recommended) | None | None | Bounded: proportional to *verified, unflagged* identities, and flagged clusters must step up | Medium | Strongest: before/after plus provable draw |

**Why not 5.** A randomized waiting room (shuffle everyone present at T0, then drain) is what large ticket sites run. It is fair in the same way, but you must keep 50,000 live connections and drain them at a controlled rate during the demo. The window + draw gives the same fairness property with a batch job instead of a live drain, which is far safer in 12–18 hours.

**Why not weight the lottery by risk score.** Silent down-weighting punishes humans who look odd (shared college Wi-Fi, VPN) with no way to appeal, and a judge will ask about it. Visible friction (step-up verification at claim time) is defensible; invisible penalties are not.

## 4. Recommended mechanism: Verified Entry Window + Provable Draw

We run a four-phase drop in which every verified identity gets exactly one entry, the draw is a commit-reveal random ranking, and winners confirm with a single-use signed token inside a claim window.

1. **Verify (identity).** Phone number + OTP (simulated SMS provider in the hackathon; the OTP is returned in the response only when `SIM_MODE=true`). We store `phone_hash = HMAC(server_pepper, normalized_phone)`. One phone hash = one user, enforced by a unique index.
2. **Register (window, e.g. 60 s in the demo, 10 min in reality).** `POST /entries` creates one entry per `(drop_id, user_id)`, enforced by a unique index. Arriving at second 1 or second 59 makes no difference. The backend publishes `seed_commit = SHA256(seed)` *before* the window opens.
3. **Draw (once, at close).** The backend freezes the entry set, publishes `entry_set_hash`, reveals `seed`, and ranks every eligible entry by `HMAC_SHA256(seed, drop_id ‖ user_public_id)`. Ranks 1–500 receive offers; the rest form an ordered waitlist. Anyone can re-run the ranking and check it.
4. **Claim (window, e.g. 120 s in the demo).** A winner presents a signed, session-bound, single-use admission token. The allocation engine atomically assigns a seat. Expired offers return to the pool and the next waitlisted entry gets an offer.

### If one attacker controls 10,000 clients

We break the attack into what the attacker actually controls and close each lever separately.

| Attacker lever | What it buys under FIFO | What it buys here | Enforced by |
| --- | --- | --- | --- |
| Speed (lowest latency) | First slots | Nothing: arrival time is not an input to the draw | Window + draw |
| Volume (10,000 connections, one identity) | More chances to be first | Nothing: 10,000 POSTs collapse into 1 entry | `UNIQUE(drop_id, user_id)` + idempotency |
| Retries | Recovers from timeouts faster than humans | Nothing: retries return the same entry | Idempotency keys |
| Token replay / sharing | n/a | Nothing: token is single-use and bound to the issuing session | Signed token with `jti`, session binding |
| 10,000 *identities* (Sybil) | 10,000 independent fast bots | At most 10,000 tickets out of N, minus what clustering flags, and flagged winners must pass step-up before their seat confirms | Identity cost + cluster flags + step-up at claim |

The last row is the honest one. **We cannot make 10,000 real verified identities worth less than 10,000 tickets; nobody can.** What we do is:

- **Convert the attack from a speed problem to an identity-cost problem.** Each extra ticket now costs a verified phone number (real world: a SIM, roughly ₹100–300 each plus KYC friction), not a free HTTP request.
- **Make farmed identities visible.** Registration velocity per device ID, per IP /24, per ASN, OTP-request bursts, and near-identical user agents produce cluster flags.
- **Gate rather than block.** A flagged entry stays in the draw, but if it wins it must pass step-up re-verification (fresh OTP to the same phone within the claim window). Farms that reuse or rent numbers fail at scale; a flagged human loses about 20 seconds.
- **Make winnings non-transferable.** Seat is bound to the verified identity (name on ticket, ID check at venue, transfer disabled). This removes the resale motive, which is why real drops attract bots.
- **Bound the damage mathematically.** Wins follow a hypergeometric distribution over valid entries. With k bot identities among N entries, expected bot wins = 500 × k / N. 2,000 surviving fake identities among 52,000 entries gives about 19 seats (3.8%), and the dashboard shows that number sitting inside the chance-only band.

This is the sentence to say to judges: *"Bots get exactly the share of seats that matches their share of verified identities, no more; we made identities the only lever, made it expensive, and made it visible."*

## 5. Complete architecture

One FastAPI service with layered middleware sits in front of Postgres and Redis; the simulator is just another client, and its ground-truth labels reach only the dashboard and evaluator.

&#91;embedded content: Fair Drop architecture · request path top to bottom\]

A request travels down the left column; floods stop at the abuse layer, and only the allocation engine's transaction can turn a free seat into a sold one.

| Component | Lives in | Owner | Why there |
| --- | --- | --- | --- |
| Gateway concerns (auth, idempotency, errors) | FastAPI middleware | Akshay | One process, no separate gateway to deploy |
| Abuse L1–L5 | FastAPI middleware module + Redis Lua scripts | Saanvi | Runs before any handler, so rejects never touch Postgres |
| Risk flags L6–L7 | Called from the entries handler, state in Redis sorted sets | Saanvi (rules), Akshay (call site) | Needs the identity, so it runs after auth |
| Admission + draw | FastAPI handlers + one admin-triggered draw job | Akshay | Draw is a single transaction, no worker queue needed |
| Allocation engine + sweeper | Claim handler + background task in the API process | Akshay | Postgres transaction is the whole engine |
| Metrics | Redis counters written by middleware; `/admin/metrics` reads them | Saanvi (counters), Akshay (endpoint) | No Prometheus to configure |
| Dashboard | React app polling admin endpoints | Ameya | Same build as the user app, different route |
| Simulator + evaluator | Separate container, ideally a second laptop | Saanvi | Load generator must not share CPU with the API if avoidable |

## 6. Technology stack

Pick the boring stack the team already knows; the novelty is in the mechanism and the evidence, not the tools.

| Layer | Choice | Why | Rejected |
| --- | --- | --- | --- |
| Backend API | FastAPI + uvicorn (4 workers), asyncpg, redis-py | Async I/O, Pydantic models generate the OpenAPI spec that *is* the contract | Go (faster, but slower to write under pressure); Node (fine, but no free typed contract) |
| Source of truth | PostgreSQL 16 | Transactions, unique constraints and `SKIP LOCKED` give integrity for free | Redis-only inventory (durability and audit are weak) |
| Fast state | Redis 7 | Atomic Lua token buckets, counters, single-use token set, status cache | Memcached (no atomic scripts) |
| Frontend | React + Vite + Tailwind + Recharts | No SSR needed; fast to build; Recharts is enough for live lines and bars | Next.js (SSR adds nothing here) |
| Live updates | Polling `GET /me` with server-chosen `poll_after_ms`; dashboard polls metrics every 1 s | Survives reconnects trivially; server can slow polling under load | WebSockets (50k sockets is its own scaling problem) |
| Attack simulator | Python asyncio + httpx, multi-process, labelled clients | Full scenario logic, ground-truth labels, same public API | Locust (heavy at 50k users), raw k6 (awkward for multi-step stateful flows) |
| Raw throughput check | k6 (optional) | One script to report peak RPS honestly | — |
| Metrics | Redis per-second counters + `/admin/metrics` JSON, rendered by Ameya | One less system to configure | Prometheus + Grafana (stretch only) |
| Packaging | Docker Compose: `api`, `postgres`, `redis`, `web`, `sim` | One command brings the demo up on any laptop | Kubernetes (no judging value) |

**Throughput reality check.** A single laptop will not hold 50,000 simultaneous TCP connections from a simulator on the same machine. We model 50,000 *logical* clients arriving in a 30 s burst, with a few thousand concurrent sockets, and report achieved requests/second honestly. If a second laptop is available, run the simulator there.

## 7. Backend + allocation design (Akshay)

Postgres owns every fact that matters (users, entries, seats, allocations); Redis owns only things that are safe to lose (rate-limit buckets, counters, caches, the fast replay check).

### What lives where

| Data | Postgres | Redis | If Redis is lost |
| --- | --- | --- | --- |
| Users, phone hash | Yes (unique) | — | Nothing lost |
| Entries, draw rank, offer expiry | Yes (unique per drop+user) | Status cache, 2 s TTL | Cache rebuilds |
| Seats + allocations | Yes (500 physical rows) | `remaining` counter for display | Recomputed from `seats` |
| Idempotency records | Yes (claim) | Yes (registration, 10 min TTL) | Registration falls back to unique index |
| Token `jti` single-use | Backstop via `UNIQUE(entry_id)` on allocations | `SET NX` with TTL | PG uniqueness still blocks double use |
| Rate-limit buckets | — | Yes | Per-worker in-memory buckets take over |
| Abuse events | Sampled + all blocks of identities | Per-second counters | Lose counts, not decisions |

### Allocation integrity: 500 can never become 501

The guarantee comes from structure, not from code being careful: there are exactly 500 rows in `seats`, an allocation *is* a seat row changing from `free` to `sold`, and `UNIQUE(drop_id, entry_id)` on seats means one entry can hold at most one row.

**The last-seat race.** Entries A and B both try to claim when one seat is free.

| Approach | Last-seat race | Verdict |
| --- | --- | --- |
| Read count, then insert | Both read 1 free, both insert: oversell | Broken |
| Postgres `SELECT … FOR UPDATE` on a counter row | Correct, but every claim serializes on one row | Works, slow under storms |
| Atomic `UPDATE drops SET sold = sold + 1 WHERE sold < capacity` | Correct counter, but counter and seat assignment can drift unless in the same tx | Good, needs care |
| Redis `DECR` / Lua | Fast and atomic, but Redis is not durable and must be reconciled with PG | Good as a pre-filter only |
| Distributed lock (Redlock) | Adds failure modes (lock expiry, clock skew) for no gain on a single PG | Rejected |
| **Row-per-seat + `FOR UPDATE SKIP LOCKED` in one PG transaction** | A locks seat 500; B skips it, finds none, gets `SOLD_OUT` | **Recommended** |

`SKIP LOCKED` lets concurrent claims grab *different* free seats in parallel instead of queueing on one row, so it is both correct and fast. The unique constraints are a second, independent wall: even a buggy code path cannot write a second seat for the same entry.

### Critical claim operation

```python
async def claim(entry_id, session, token, idem_key):
    # 0. Cheap checks outside the transaction
    claims = verify_signature(token)              # HMAC/Ed25519, exp, drop_id
    assert claims.entry_id == entry_id and claims.sid_hash == hash(session.id)
    if cached := await idem_get(session.user_id, idem_key):
        return cached                             # replay of an earlier success

    async with pg.transaction():                 # READ COMMITTED is enough
        e = await pg.fetchrow("""
            SELECT id, status, offer_expires_at FROM entries
            WHERE id = $1 AND user_id = $2 FOR UPDATE""", entry_id, session.user_id)
        if e.status == 'ALLOCATED':               # already done: idempotent success
            return await existing_allocation(entry_id)
        if e.status == 'STEP_UP_REQUIRED':
            raise StepUpRequired()
        if drop.mode == 'fair' and (e.status != 'OFFERED' or e.offer_expires_at < now()):
            raise NotOfferedOrExpired()

        seat = await pg.fetchrow("""
            UPDATE seats SET status = 'sold', entry_id = $1, sold_at = now()
            WHERE id = (SELECT id FROM seats
                        WHERE drop_id = $2 AND status = 'free'
                        ORDER BY seat_no
                        FOR UPDATE SKIP LOCKED LIMIT 1)
            RETURNING id, seat_no""", entry_id, drop.id)
        if seat is None:
            raise SoldOut()                       # tx rolls back, nothing written

        await pg.execute("UPDATE entries SET status='ALLOCATED' WHERE id=$1", entry_id)
        alloc = await pg.fetchrow("""
            INSERT INTO allocations (drop_id, entry_id, seat_id, idempotency_key)
            VALUES ($1,$2,$3,$4) RETURNING id, created_at""", drop.id, entry_id, seat.id, idem_key)
        await idem_put_pg(session.user_id, idem_key, response := build(alloc, seat))
    # after commit only
    await redis.set(f"jti:{claims.jti}", 1, nx=True, ex=3600)
    await redis.decr(f"drop:{drop.id}:remaining")  # display only
    return response
```

The entry row lock (`FOR UPDATE` on entries) serializes *the same user's* concurrent claims, so two tabs cannot both reach the seat update. The seat sub-select serializes nothing across *different* users. In FIFO mode the same function runs with the offer check skipped, so the baseline is equally oversell-proof; it is only unfair.

### Idempotency: three identical `POST /claim`

Every write carries an `Idempotency-Key` header (UUID generated once per user action and stored in `sessionStorage`). Duplicates are handled at three levels.

| Arrival | What happens | Response |
| --- | --- | --- |
| #1 | Takes entry row lock, allocates seat 137, stores response under key, commits | `200 {allocation_id, seat_no:137}` |
| #2 (concurrent with #1) | Blocks on entry row lock; after #1 commits, sees `ALLOCATED`, returns existing allocation | Same `200` body |
| #3 (later, same key) | Idempotency record hit before any lock | Same `200` body |
| #4 (new key, same user) | Entry already `ALLOCATED` → returns existing allocation | Same `200` body; never a second seat |
| Same key, different body | Request hash mismatch | `422 IDEMPOTENCY_KEY_REUSED` |

Natural idempotency (one allocation per entry) is the real guarantee; the key only makes retries cheap and responses identical.

### Session reliability

| Event | Behaviour |
| --- | --- |
| Refresh | Session cookie survives; UI calls `GET /drops/{id}/me` and re-renders from server state |
| Reconnect after drop | Same as refresh; client retries pending write with the *same* idempotency key |
| Multiple tabs | All tabs share one session and one entry; all show the same state; a claim from tab 2 returns tab 1's allocation |
| Duplicate requests | Collapse via unique indexes and idempotency |
| Lost response after success | Retry returns stored success; or `GET /me` shows `ALLOCATED` with seat number |
| Expired admission token | `GET /me` re-issues a fresh token while the offer is still valid; offer expiry, not token expiry, is what loses the seat |
| Expired offer | Sweeper (every 5 s) marks `OFFER_EXPIRED`, promotes the next waitlisted entry in one transaction |

The user state machine is drawn in section 13.

## 8. Abuse + attack architecture (Saanvi)

The abuse system has two jobs that must stay separate: protect *capacity* (shed junk requests cheaply) and protect *eligibility* (flag farmed identities for step-up), and neither job is allowed to decide who wins.

### Layers, in request order

| # | Layer | Catches | Action | How an attacker bypasses it | Why that's fine |
| --- | --- | --- | --- | --- | --- |
| L1 | Global + per-endpoint token bucket (Redis Lua) | Raw floods that would melt the API | `429` with `Retry-After`, no DB touch | Can't; it's capacity protection | Flooding gains nothing anyway |
| L2 | Per-IP and per-/24 buckets | One host or subnet hammering | `429`, IP cooldown after repeated violations | Residential proxies, IP rotation | L3–L5 key on things IPs can't fake |
| L3 | Per-session and per-user buckets | Multi-tab storms, retry loops, status polling abuse | `429`; server raises `poll_after_ms` | New sessions need new OTPs | Pushes attacker to identity farming |
| L4 | Token validation (signature, `exp`, `jti`, session binding) | Replay, stolen tokens, forged tokens, token sharing across clients | `401 TOKEN_INVALID`; event logged | Steal the session cookie too | Session is httpOnly; one stolen session = one entry |
| L5 | Duplicate / idempotency collapse | Same identity entering or claiming twice | Return existing result; count as duplicate | Can't; it's a DB constraint | — |
| L6 | OTP abuse controls | Bulk OTP requests, sequential numbers, one device requesting many numbers | Throttle OTP per device/IP/prefix; cooldown | Spread across devices and IPs | Each identity now costs real infrastructure |
| L7 | Cluster / velocity scoring | Identity farms: many users from one device ID, /24, ASN or UA in a short window; machine-regular timing | Set `risk_flags` on entry; **no block** | Randomize everything per identity | Cost rises; residual bots get one fair ticket each |
| L8 | Step-up at claim (flagged winners only) | Farms that can't receive a fresh OTP on demand at scale | Seat held until re-verified within claim window; fail → waitlist | Real SIMs pass | Then they're real paying identities: proportional share |

**Design rules.**

- L1–L3 never touch Postgres. A rejected request must cost under 1 ms of CPU.
- Only L4, L5 and L8 change outcomes, and all three are *deterministic and explainable* (bad signature, duplicate, failed re-verification).
- L7 produces evidence and friction, never silent exclusion. This is what lets us answer "what if you flag a real user?" in one line.
- Every layer has an on/off switch in `/admin/abuse/config` so the before/after demo can show each layer's contribution.

### Risk scoring (L7), kept simple

Score each new entry 0–100 from additive rules computed in Redis sliding windows; `>= 60` sets `STEP_UP_REQUIRED` if the entry wins.

| Signal | Window | Points |
| --- | --- | --- |
| Same `device_id` across > 3 users | Drop lifetime | +40 |
| Same /24 across > 20 new users | 60 s | +25 |
| Inter-request timing variance near zero (scripted) | Per session | +20 |
| OTP verified < 2 s after request | Per user | +15 |
| User agent seen on > 50 users in 60 s | 60 s | +10 |

Thresholds are tuned on the simulator's *normal* scenario so false positives on simulated humans stay under 2%, and that rate is shown on the scorecard.

## 9. Fairness metrics

The headline fairness number is the **advantage ratio**: a bot identity's win probability divided by a human identity's win probability, which should be about 1.0 in Fair mode and far above 1 in FIFO.

```latex
A = \frac{W_{bot} / E_{bot}}{W_{human} / E_{human}}
```

W is seats won and E is valid entries (verified identities that entered). A = 1 means no per-identity advantage. A second ratio, per *request*, shows that volume buys nothing: bots send 1,000× more requests for the same per-identity odds.

**Chance band.** Under a fair draw, bot wins follow a hypergeometric distribution with mean 500 × E\_bot / E and variance 500 × p × (1 − p) × (E − 500) / (E − 1), where p = E\_bot / E. Showing measured bot wins *inside* the 95% band is the most convincing single picture: "bots did exactly as well as luck predicts." Example: 2,000 bot identities in 52,000 entries → mean ≈ 19, 95% band ≈ 11–27.

**Speed independence.** Spearman correlation between arrival order and winning. FIFO ≈ strongly negative (earlier wins); Fair ≈ 0.

### Useful vs misleading

| Metric | Keep? | Why |
| --- | --- | --- |
| Advantage ratio A (per identity) | Headline | Directly answers the PS |
| Bot seat share vs bot entry share | Headline | Same idea, easier for non-technical judges |
| Arrival-order vs win correlation | Headline | Proves speed doesn't matter |
| Requests per successful allocation (bot vs human) | Keep | Shows volume is wasted |
| Oversold count, duplicate allocations | Headline (must be 0) | Integrity proof |
| P50/P95/P99 latency, error rate at peak | Keep | Concurrency proof; P99 matters more than average |
| Throughput (req/s handled, claims/s) | Keep | Concurrency proof |
| Detection precision / recall vs ground truth | Keep | Honest view of the detector |
| False positive rate on simulated humans | Keep | Answers the "wrongly flagged user" question |
| Blocked request count | Supporting only | **Misleading alone**: you can block 99% of bot requests and bots still win under FIFO |
| "Bot win rate = 0%" | Avoid | Not credible if bots hold verified identities; claim proportionality instead |
| Average latency | Avoid | Hides tail timeouts that decide FIFO outcomes |
| Jain's index over all users | Avoid | Binary win/lose per user makes it meaningless; use it per *actor* (attacker group vs humans), normalized by identities, if at all |
| Gini over wins per actor | Optional | Good for "attacker concentration"; needs actor labels from the simulator |
| Queue wait time | Low value here | Arrival time no longer matters; report time-to-result instead |

### Judge scorecard

Same attack (50,000 humans + 1 attacker with 10,000 clients and 2,000 farmed identities), run twice. Values are filled from the evaluator after each run; the right-hand column states the target.

| Metric | FIFO mode | Fair mode | Target |
| --- | --- | --- | --- |
| Bot seat share | measured | measured | ≈ bot entry share |
| Advantage ratio A | measured | measured | 0.7–1.3 (within chance band) |
| Bot wins inside 95% chance band | no / yes | no / yes | Yes |
| Arrival-order vs win correlation | measured | measured | ≈ 0 |
| Bot requests per win | measured | measured | Orders of magnitude above human |
| Oversold | measured | measured | 0 |
| Duplicate allocations | measured | measured | 0 |
| P95 / P99 latency at peak | measured | measured | < 300 ms / < 1 s |
| Error rate at peak (5xx) | measured | measured | < 1% |
| Human false positive rate (step-up) | measured | measured | < 2% |
| Detection recall on farmed identities | measured | measured | Shown, not required |

FIFO is expected to show a large bot share, a high A and a strong arrival correlation; both modes should show 0 oversold, which makes the point that integrity and fairness are separate properties.

## 10. Dashboard design (Ameya)

Both screens render purely from two endpoints (`GET /drops/{id}/me` for users, `GET /admin/drops/{id}/metrics` for judges), so Ameya can build against mocked JSON from hour 1.

### User view: one screen, driven by `entry_status`

| State | What the user sees | Key UI rule |
| --- | --- | --- |
| Before window | Event card, 500 seats, countdown, "Verify your phone" | Show `seed_commit` small, with "how the draw works" link |
| Verified, window open | "Enter the draw" button; after tap: "You're in. Arriving early doesn't help." | Button disables after first tap; same idempotency key on retries |
| Waiting for draw | Countdown to draw; entries count rising | Copy reassures: "You can close this tab." |
| Offered | Big "You won a seat. Confirm within 1:58" + confirm button | Timer from server `offer_expires_at` and `server_time`, not client clock |
| Step-up required | "Quick check: enter the code we just sent" | Never says "you look like a bot" |
| Allocated | Seat number, confirmation ID, QR placeholder | Survives refresh, shown in every tab |
| Waitlisted | "You're #37 on the waitlist; seats free up as offers expire" | Live position |
| Not selected / sold out | Clear final state | No retry button that implies retrying helps |
| Connection lost | Banner: "Reconnecting… your place is safe" with backoff | Pending action retried with same key; state re-read on reconnect |
| Rate-limited | "Slow down a moment" honoring `Retry-After` | Never shown to normal-pace humans |

### Judge / admin view: tells the story top to bottom

1. **Story strip** across the top: NORMAL → BOT ATTACK → REQUEST SPIKE → MITIGATION → FAIR DRAW, with the current phase lit (driven by `attack_phase` from the simulator telemetry and `phase` from the drop).
2. **Live traffic chart** (requests/s, stacked by outcome: accepted / rate-limited / token-rejected / duplicate). The spike and the red band of rejections are the visual moment.
3. **Ground truth vs detected** side by side: "simulator says 10,000 bot clients, 2,000 bot identities" vs "backend flagged N clusters" (with a label making clear the backend never sees ground truth).
4. **Inventory panel:** 500 seat grid that fills as claims land; big counters `allocated`, `remaining`, `oversold: 0`, `duplicates: 0` in green.
5. **Fairness panel:** bot seat share vs bot entry share bars; bot wins plotted inside the chance band; advantage ratio gauge; FIFO run shown ghosted beside Fair run.
6. **Performance panel:** P50/P95/P99 lines, error rate, claims/s.
7. **Controls (admin only):** mode FIFO/Fair, abuse layers on/off, open/close/draw/reset, "verify draw" button that re-computes the ranking client-side from the revealed seed.

The ghosted FIFO-vs-Fair comparison is the single most important visual; build it before polishing anything else.

## 11. API contract

Fifteen endpoints cover everything; the browser and the simulator use the exact same public ones, and Akshay publishes the FastAPI OpenAPI spec at `/docs` by hour 2 so mocks match reality.

**Conventions.** Base path `/api`. JSON only. Auth = `fd_session` httpOnly cookie (browser) or `Authorization: Bearer <session_token>` (simulator); both carry the same session. Every POST that changes state requires `Idempotency-Key: <uuid>`. Every response includes `server_time`. Errors always look like:

```json
{ "error": { "code": "RATE_LIMITED", "message": "Slow down", "retry_after_ms": 2000 } }
```

### Public endpoints (Ameya → Akshay, Saanvi's simulator → Akshay)

| Method + URL | Auth | Request | Success | Errors | Idempotency |
| --- | --- | --- | --- | --- | --- |
| `GET /drops/{id}` | None | — | `200 {id, name, capacity, mode, phase, reg_opens_at, reg_closes_at, claim_window_s, seats_remaining, seed_commit, seed?, entry_set_hash?}` | `404` | Read-only, cacheable 1 s |
| `POST /auth/otp/request` | None | `{phone, device_id}` | `200 {request_id, expires_in_s, dev_otp?}` (`dev_otp` only in `SIM_MODE`) | `429 OTP_THROTTLED`, `400 INVALID_PHONE` | Same phone within 30 s returns same `request_id` |
| `POST /auth/otp/verify` | None | `{request_id, otp, device_id}` | `200 {session_token, user_public_id}` + sets cookie | `401 OTP_INVALID`, `410 OTP_EXPIRED`, `429` | Re-verify returns existing session for same device |
| `POST /drops/{id}/entries` | Session | `{}` | `201 {entry_id, status:"REGISTERED"}`; repeat → `200` same body | `403 WINDOW_CLOSED`, `403 WINDOW_NOT_OPEN`, `429` | `UNIQUE(drop,user)`; key optional |
| `GET /drops/{id}/me` | Session | — | `200 {phase, entry: {entry_id, status, rank?, waitlist_pos?, offer_expires_at?, step_up_required, admission_token?}, allocation?: {allocation_id, seat_no, confirmed_at}, poll_after_ms}` | `401`, `429` | Read-only; the UI's single source of truth |
| `POST /drops/{id}/claim` | Session + admission token | `{admission_token}` | `200 {allocation_id, seat_no, confirmed_at}` | `401 TOKEN_INVALID`, `403 NOT_OFFERED`, `409 OFFER_EXPIRED`, `409 SOLD_OUT`, `423 STEP_UP_REQUIRED`, `422 IDEMPOTENCY_KEY_REUSED`, `429` | Required key; same key or same entry → same `200` |
| `POST /drops/{id}/step-up` | Session | `{otp}` | `200 {status:"OFFERED"}` | `401 OTP_INVALID`, `409 OFFER_EXPIRED` | Repeat after success → `200` |

In FIFO mode, `entries` and `claim` are both open at once and `claim` skips the offer check; the request/response shapes are identical, so neither the UI nor the simulator needs a second code path.

**Admission token** (returned inside `/me` once an offer exists): compact HMAC-SHA256 JWT with claims `{drop_id, entry_id, sid_hash, jti, iat, exp}`; `exp` ≤ offer expiry; `sid_hash` binds it to the session that fetched it.

### Admin endpoints (Ameya dashboard and Saanvi's harness → Akshay)

Auth: `X-Admin-Key` header from `.env`. All idempotent by nature.

| Method + URL | Request | Response |
| --- | --- | --- |
| `POST /admin/drops` | `{name, capacity:500, mode:"fair"\|"fifo", window_s, claim_window_s}` | `201 {drop_id, seed_commit}` |
| `POST /admin/drops/{id}/phase` | `{action:"open"\|"close"\|"draw"\|"reset"}` | `200 {phase}`; `409 INVALID_TRANSITION` |
| `GET /admin/drops/{id}/metrics` | `?window_s=60` | `200 {rps_series[], outcomes_series{accepted,rate_limited,token_rejected,duplicate}, latency{p50,p95,p99}, error_rate, active_sessions, entries, offers, allocated, remaining, flagged_entries, step_ups{issued,passed,failed}}` |
| `GET /admin/drops/{id}/integrity` | — | `200 {seats_total:500, sold, free, oversold, duplicate_entries_with_seats, invariant_ok}` (computed by SQL, not counters) |
| `GET /admin/drops/{id}/export` | — | `200` NDJSON rows `{user_public_id, entry_id, entered_at, risk_score, risk_flags, rank, status, seat_no?}` |
| `GET /admin/drops/{id}/draw-proof` | — | `200 {seed_commit, seed, entry_set_hash, algorithm:"HMAC_SHA256(seed, drop_id‖user_public_id) asc"}` |
| `PUT /admin/abuse/config` | `{layers:{L1:true,…,L8:true}, thresholds:{…}}` | `200` config echo |

### Simulator telemetry (Saanvi → dashboard, never read by decision code)

`POST /sim/telemetry` `{run_id, attack_phase, clients_by_label{human,bot}, identities_by_label, requests_by_label}` writes to a separate Redis namespace `sim:*`. The dashboard shows it as "ground truth". A grep check in CI confirms no backend decision module imports `sim:` keys.

## 12. Database schema

Eight tables; the three constraints that carry integrity are `UNIQUE(drop_id, user_id)` on entries, `UNIQUE(drop_id, entry_id)` on seats, and `UNIQUE(entry_id)` on allocations.

```sql
CREATE TABLE drops (
  id               uuid PRIMARY KEY,
  name             text NOT NULL,
  capacity         int  NOT NULL CHECK (capacity > 0),
  mode             text NOT NULL CHECK (mode IN ('fair','fifo')),
  phase            text NOT NULL CHECK (phase IN ('SCHEDULED','OPEN','CLOSED','DRAWN','CLAIMING','DONE')),
  reg_opens_at     timestamptz, reg_closes_at timestamptz,
  claim_window_s   int  NOT NULL DEFAULT 120,
  seed_commit      text NOT NULL,          -- sha256(seed), published before OPEN
  seed             text,                   -- revealed at draw
  entry_set_hash   text,                   -- sha256 of sorted user_public_ids at close
  created_at       timestamptz DEFAULT now()
);

CREATE TABLE users (
  id               uuid PRIMARY KEY,
  public_id        text UNIQUE NOT NULL,   -- random, used in draw + exports
  phone_hash       text UNIQUE NOT NULL,   -- HMAC(pepper, phone): one phone = one user
  first_device_id  text, first_ip inet,
  created_at       timestamptz DEFAULT now()
);

CREATE TABLE sessions (
  id               uuid PRIMARY KEY,
  user_id          uuid NOT NULL REFERENCES users(id),
  device_id        text, ip inet, ua_hash text,
  created_at       timestamptz DEFAULT now(), revoked_at timestamptz
);
CREATE INDEX ON sessions (user_id);

CREATE TABLE entries (
  id               uuid PRIMARY KEY,
  drop_id          uuid NOT NULL REFERENCES drops(id),
  user_id          uuid NOT NULL REFERENCES users(id),
  status           text NOT NULL CHECK (status IN ('REGISTERED','OFFERED','STEP_UP_REQUIRED',
                     'WAITLISTED','NOT_SELECTED','OFFER_EXPIRED','ALLOCATED','DISQUALIFIED')),
  entered_at       timestamptz NOT NULL DEFAULT now(),
  client_ip inet, device_id text,
  risk_score       int NOT NULL DEFAULT 0,
  risk_flags       jsonb NOT NULL DEFAULT '[]',
  draw_rank        int,                    -- 1..N after draw
  offer_expires_at timestamptz,
  UNIQUE (drop_id, user_id),               -- one entry per identity
  UNIQUE (drop_id, draw_rank)
);
CREATE INDEX ON entries (drop_id, status, draw_rank);
CREATE INDEX ON entries (drop_id, offer_expires_at) WHERE status = 'OFFERED';

CREATE TABLE seats (
  id        bigserial PRIMARY KEY,
  drop_id   uuid NOT NULL REFERENCES drops(id),
  seat_no   int  NOT NULL,
  status    text NOT NULL CHECK (status IN ('free','sold')),
  entry_id  uuid REFERENCES entries(id),
  sold_at   timestamptz,
  UNIQUE (drop_id, seat_no),
  UNIQUE (drop_id, entry_id),              -- one seat per entry
  CHECK ((status = 'free') = (entry_id IS NULL))
);
CREATE INDEX ON seats (drop_id) WHERE status = 'free';
-- created once per drop: INSERT ... SELECT generate_series(1, capacity)

CREATE TABLE allocations (                 -- append-only ledger / audit
  id               uuid PRIMARY KEY,
  drop_id          uuid NOT NULL,
  entry_id         uuid NOT NULL UNIQUE REFERENCES entries(id),
  seat_id          bigint NOT NULL UNIQUE REFERENCES seats(id),
  idempotency_key  uuid NOT NULL,
  created_at       timestamptz DEFAULT now()
);

CREATE TABLE idempotency_records (
  user_id       uuid NOT NULL,
  key           uuid NOT NULL,
  endpoint      text NOT NULL,
  request_hash  text NOT NULL,
  response      jsonb NOT NULL,
  status_code   int  NOT NULL,
  created_at    timestamptz DEFAULT now(),
  PRIMARY KEY (user_id, key)
);

CREATE TABLE abuse_events (                -- sampled; full counts live in Redis
  id bigserial PRIMARY KEY, ts timestamptz DEFAULT now(),
  drop_id uuid, layer text, action text, key_type text, key_value text,
  user_id uuid, detail jsonb
);
CREATE INDEX ON abuse_events (drop_id, ts);
```

**Redis keys**

| Key | Type | Purpose | TTL |
| --- | --- | --- | --- |
| `rl:{scope}:{id}` | Hash (tokens, ts) via Lua | Token buckets for global / IP / /24 / session / user / OTP | 2× refill window |
| `jti:{jti}` | String, `SET NX` | Single-use token fast check | Token lifetime |
| `idem:reg:{user}:{key}` | String (JSON response) | Registration replay cache | 10 min |
| `me:{entry_id}` | String (JSON) | Status cache for polling storms | 2 s |
| `drop:{id}:remaining` | Integer | Display only; recomputed from `seats` on mismatch | None |
| `m:{drop}:{epoch_s}` | Hash of counters (`HINCRBY`) | Per-second metrics for the dashboard | 1 h |
| `cl:{kind}:{value}` | Sorted set of user ids by time | Cluster windows for risk rules (device, /24, UA) | Drop lifetime |
| `sim:*` | Various | Simulator ground truth, dashboard only | 1 h |

## 13. State machine

Each user's state lives in one `entries.status` value that only the server changes; every screen in Ameya's app maps one-to-one onto a state below.

&#91;embedded content: User lifecycle · 10 states, Fair mode\]

In FIFO mode the middle row is skipped: REGISTERED goes straight to ALLOCATED on a successful claim, or to NOT\_SELECTED when seats run out. Every transition is a single Postgres update guarded by the current status (`UPDATE entries SET status = $new WHERE id = $1 AND status = $expected`), so two workers can never move the same entry twice.

## 14. Failure handling

Every failure mode costs availability or latency, never integrity, because Postgres constraints are the last word on who holds a seat.

| Failure | System behaviour | User sees | Integrity |
| --- | --- | --- | --- |
| Redis down | Rate limits fall back to per-worker in-memory buckets (coarser); `jti` check falls back to PG uniqueness; status reads go straight to PG with a lower `poll_after_ms` cap | Slightly slower polling | Safe |
| Postgres down | Writes return `503` with `Retry-After`; no entries or claims accepted; offer timers are *extended* by the outage length when PG returns (sweeper uses a pause log) | "Reconnecting… your place is safe" | Safe: nothing can be written |
| Backend restart | API is stateless; in-flight transactions roll back; clients retry with same idempotency key | Brief reconnect banner | Safe |
| User disconnects | Server state unchanged; on return `GET /me` restores screen | Same screen as before | Safe |
| Request times out | Client retries with same key; if first attempt committed, retry returns stored response | Success after retry | Safe |
| Allocation succeeds, response lost | Idempotency record or `ALLOCATED` status returns the same seat | Seat shown on retry or refresh | Safe |
| Browser refresh | Cookie session + `GET /me` | Same state | Safe |
| Queue / admission token replayed | Different session → `sid_hash` mismatch `401`; same session after use → returns existing allocation; forged → signature fails | Attacker gets `401` | Safe |
| Multiple tabs | One session, one entry, entry row lock serializes claims | Same seat in all tabs | Safe |
| Draw job crashes midway | Draw runs in one transaction and is deterministic from the seed; re-run yields identical ranks | Draw a few seconds later | Safe |
| Sweeper crashes | Offers past expiry are treated as expired by the claim check itself; sweeper only promotes the waitlist | Waitlist moves later | Safe |

### MUST / SHOULD / STRETCH

| Priority | Feature | Owner |
| --- | --- | --- |
| MUST | OTP identity (simulated), session cookie + bearer | Akshay |
| MUST | One entry per identity, registration window, draw with commit-reveal seed | Akshay |
| MUST | Atomic claim with `SKIP LOCKED`, unique constraints, idempotency keys | Akshay |
| MUST | FIFO/Fair mode switch on same endpoints | Akshay |
| MUST | L1–L5 abuse layers (rate limits, token validation, duplicate collapse) | Saanvi |
| MUST | Simulator: normal, flash crowd, bot flood, repeated attempts, multi-tab, token replay, identity farming | Saanvi |
| MUST | Evaluator: advantage ratio, share vs entry share, chance band, oversold, duplicates, P95/P99 | Saanvi |
| MUST | User flow screens incl. reconnect state; judge dashboard with traffic, inventory, fairness panels | Ameya |
| SHOULD | Offer expiry + waitlist promotion sweeper | Akshay |
| SHOULD | L6 OTP throttles, L7 cluster flags, L8 step-up at claim | Saanvi (rules) + Akshay (step-up endpoint) |
| SHOULD | Story strip, ghosted FIFO-vs-Fair overlay, ground truth vs detected panel | Ameya |
| SHOULD | Draw verification button (client re-computes ranking) | Ameya |
| STRETCH | Public randomness (drand round after close) mixed into the seed | Akshay |
| STRETCH | Proof-of-work on registration as a cost bump | Saanvi |
| STRETCH | Chaos toggles: kill Redis / restart API live during demo | Saanvi |
| STRETCH | Prometheus + Grafana | Anyone, only if bored |

## 15. Attack simulator

The simulator is a population of labelled client state machines that use only the public API; labels and actor groupings are written to a local ground-truth file and the `sim:*` telemetry, never sent on decision paths.

### Structure

```text
sim/
  clients/human.py      # think time 1-4 s, one tab, polls per poll_after_ms, taps confirm in 2-8 s
  clients/bot.py        # 0 think time, N parallel requests, ignores Retry-After unless 'polite'
  scenarios/*.yaml      # population mix, arrival curve, attacker config
  runner.py             # asyncio + httpx, multiprocess shards, writes ground_truth.ndjson
  evaluator.py          # joins /admin/export with ground truth -> scorecard.json + charts
```

Each simulated client carries `{client_id, label: human|bot, actor_id, identity_id, device_id, sim_ip}`. Bots of one attacker share `actor_id` so per-actor Gini and advantage can be computed. `sim_ip` is sent as `X-Sim-Client-IP`, honored only when `SIM_MODE=true` (documented as a test-harness substitute for real source IPs).

### Scenarios and expected behaviour

| Scenario | Configuration | Expected behaviour (Fair mode) | Expected in FIFO mode |
| --- | --- | --- | --- |
| Normal | 2,000 humans over 60 s | All entries accepted; 0 rate-limited humans; P95 low | Similar |
| Flash crowd | 50,000 humans, 80% arriving in first 5 s | Latency rises but no 5xx above 1%; every human gets an entry because window ≫ burst | Early fast arrivals take all seats; late humans see `SOLD_OUT` in seconds |
| Bot flood | 1 attacker, 10,000 clients, 1 identity, 200 req/s each | L1–L3 reject > 99% with `429`; attacker gets exactly 1 entry | Same single account still races; high chance of a seat |
| Repeated attempts | 500 identities each sending `POST /entries` and `/claim` 50× | One entry and at most one seat each; duplicates counted | Same integrity, but faster retries win more seats |
| Multi-tab / concurrent | 300 users × 5 tabs firing `claim` simultaneously | Exactly one seat per user; other tabs get identical `200` | Same |
| Token replay | Reuse a winner's token from another session; reuse expired token; forge token | `401` for cross-session and forged; expired → fresh token via `/me` only for the owner | Same |
| Identity farming | 1 attacker, 5,000 identities over 20 devices and 10 /24s; 40% via "good" proxies with unique devices | Clustered 60% flagged; flagged winners face step-up; surviving bot wins land inside chance band | Bots take most seats |
| Burst attack at launch | 10,000 bot clients fire in the first 200 ms after OPEN | Rejected by L1/L2, no effect on outcome; humans arriving later are equally likely to win | Bots take most seats in the first second |
| Combined (demo) | 50,000 humans + identity-farming attacker + bot flood + replay attempts | Scorecard targets met | Scorecard shows the failure |

### Making the numbers honest

- **Labels never reach the backend.** Ground truth exists only in `ground_truth.ndjson` and `sim:*`.
- **Humans are not perfect.** Human clients randomly refresh, drop connections, open a second tab and retry, so false positives are measured, not assumed away.
- **Seeds are recorded.** Every run logs its scenario file, simulator RNG seed and drop seed, so a judge can ask for a re-run.
- **Report achieved load.** The scorecard prints actual peak requests/s and concurrent sockets next to the "50,000 clients" label.

## 16. Team-specific tasks

Ownership stays exactly as fixed; the only shared artefact is the API contract in section 11, frozen at hour 2 and changed only by agreement of all three.

|  | Ameya (Frontend + Dashboard) | Akshay (Backend + Allocation) | Saanvi (Abuse + Simulator + Evidence) |
| --- | --- | --- | --- |
| First deliverable (by hour 6) | User flow (verify → enter → wait → offered → allocated, plus reconnect banner) running on mock JSON from the contract | Compose stack up; `drops`, `otp`, `entries`, `me`, `claim` working in FIFO mode with `SKIP LOCKED` and idempotency | Redis Lua rate limiter middleware (L1–L3) merged into Akshay's app; simulator runs normal + bot flood against FIFO mode and writes ground truth |
| Second deliverable (by hour 10) | Judge dashboard: live traffic chart, inventory grid with oversold/duplicates counters, fairness panel fed by `/metrics` and `sim:*` | Fair mode: window, commit-reveal draw, offers, admission tokens, `/integrity`, `/export`, `/draw-proof` | All MUST scenarios + evaluator producing `scorecard.json`; token replay + multi-tab tests passing |
| Third deliverable (by hour 14) | Story strip, FIFO-vs-Fair ghost overlay, draw-verify button, polish | Sweeper + waitlist; step-up endpoint; metrics counters | L6–L7 rules + step-up wiring; tuned thresholds; final FIFO vs Fair runs recorded |
| Depends on | Contract (hour 2), real `/me` (hour 6), `/metrics` (hour 9), Saanvi's telemetry format (hour 4) | Saanvi's middleware interface (hour 3), Ameya's needs for `/metrics` fields (hour 2) | Akshay's endpoints in FIFO (hour 6) and Fair (hour 10); `/export` schema (hour 2) |
| Final demo responsibility | Drives the screens; narrates the user journey and the visual story | Explains the draw, the claim transaction and why 501 is impossible; answers integrity questions | Launches attacks live; reads the scorecard; answers fairness, Sybil and measurement questions |

**Interfaces to agree at hour 2 (write them in the repo README):** the error envelope, the `/me` JSON, the `/metrics` JSON, the `/export` NDJSON row, the rate-limiter middleware signature (`async def check(request) -> Decision`), and the `sim:*` telemetry shape.

## 17. 12–18 hour build plan

Build FIFO mode end to end first, because it is simpler, it proves integrity, and it becomes the "before" half of the demo; Fair mode is then an additive change to the same endpoints.

| Hours | Ameya | Akshay | Saanvi | Gate at end of block |
| --- | --- | --- | --- | --- |
| 0–2 | Vite app skeleton, routes, mock server from contract JSON | Compose (api, pg, redis), schema migration, FastAPI skeleton, OpenAPI stub | Repo README with contract + interfaces; simulator skeleton; Lua token bucket | Contract frozen; `docker compose up` works for all |
| 2–6 | User flow on mocks; reconnect + error states | OTP, sessions, entries, `/me`, FIFO `claim` with `SKIP LOCKED` + idempotency | Rate-limit middleware (L1–L3) in app; human + bot clients; normal + bot flood scenarios | FIFO drop works end to end in browser and simulator; 0 oversold under 1,000 concurrent claims |
| 6–10 | Switch to real API; dashboard traffic + inventory panels | Fair mode: window, seed commit, draw, offers, admission tokens; `/integrity`, `/export`, `/metrics` | Token validation tests, multi-tab, repeated attempts, replay; evaluator v1 | Fair drop works end to end; first FIFO vs Fair scorecard exists |
| 10–14 | Fairness panel, ghost overlay, story strip | Sweeper/waitlist, step-up endpoint, perf fixes (indexes, pool sizes, status cache) | Flash crowd 50k + identity farming; L6–L7 rules; tune false positives; record runs | Combined attack scenario meets scorecard targets |
| 14–16 | Polish, draw-verify button, empty/error states | Bug fixes from load runs; freeze backend | Final runs ×3 with fixed seeds; export charts as backup images | Code freeze |
| 16–18 | Demo rehearsal ×3 with timer | Rehearsal; integrity Q&A prep | Rehearsal; fairness/Sybil Q&A prep | Demo under 5:00 twice in a row |

For a 12-hour event, compress by dropping the 14–18 block to 2 hours and skipping everything in the cut list's first three lines.

### Build first

1. Seat table + atomic claim + idempotency (integrity is non-negotiable and fast to prove).
2. One-entry-per-identity + draw (the fairness mechanism).
3. Simulator + evaluator producing the advantage ratio (the evidence).
4. Dashboard panels that show those numbers.

### Cut list, in order, if behind

1. Grafana / Prometheus (never needed).
2. Proof-of-work and drand (explain verbally instead).
3. Step-up endpoint: keep L7 flags on the dashboard only and say step-up is the next step.
4. Waitlist promotion: make claim window long enough that offers rarely expire.
5. Story strip animation: static labels lit by phase.
6. Never cut: FIFO/Fair switch, 0-oversold proof, advantage ratio with chance band, reconnect state.

## 18. 5-minute demo

The demo is one attack run twice: FIFO loses to bots in 60 seconds, then the same attack against Fair mode produces a proportional, provable result with zero oversold.

Pre-record both full runs as a fallback video; run live only if the rehearsal on the venue network passed.

| Time | Who | Does | Says (gist) |
| --- | --- | --- | --- |
| 0:00–0:30 | Saanvi | Title slide → dashboard | "500 seats, 50,000 people, and one attacker with 10,000 bots. Most systems give the seats to whoever is fastest. We built one where speed, volume and retries buy nothing." |
| 0:30–1:00 | Ameya | Phone: verify OTP, tap Enter, show "Arriving early doesn't help" and the published seed commitment | "This is the human view. One phone, one entry. Refresh, close the tab, open three more: same entry, same state." (refreshes live) |
| 1:00–1:45 | Saanvi | Switches drop to **FIFO**, launches combined scenario | "First, the normal way." Story strip lights BOT ATTACK → REQUEST SPIKE. "Requests jump to N per second. Within seconds the 500 seats are gone, and the ground-truth panel shows who got them: bots, about X%." |
| 1:45–2:00 | Akshay | Points at integrity panel | "Notice: even under that storm, 500 sold, 0 oversold, 0 duplicates. FIFO is consistent. It's just unfair." |
| 2:00–3:00 | Saanvi | Resets, switches to **Fair**, launches the identical scenario | "Same attack. This time the spike hits a registration window." Rejections stack red. "The flood is shed in Redis; 10,000 clients sharing an identity collapse into one entry. The farmed identities get flagged." Window closes. |
| 3:00–3:40 | Akshay | Triggers draw; shows seed reveal + draw-verify button turning green; claims land in the seat grid | "We committed to this seed before anyone entered. The ranking is HMAC of seed and user id, so anyone can check it. Winners confirm with a single-use token; each claim is one Postgres transaction with SKIP LOCKED on 500 physical seat rows. 501 is structurally impossible." |
| 3:40–4:30 | Saanvi | Fairness panel: ghosted FIFO vs Fair | "Bots held 3.8% of verified identities and won about that many seats, inside the band pure luck predicts. Advantage ratio: FIFO X, Fair about 1.0. Arrival time vs winning: correlation near zero. Bots sent thousands of requests per seat; humans sent about ten." |
| 4:30–5:00 | Ameya | Architecture slide | "Three layers: shed junk cheaply, make identity the only lever, then make the draw provable. Even when detection misses a bot, it gets one ticket, like you." |

The X values come from the recorded runs and are filled in on the morning of judging; never quote a number the run did not produce.

## 19. Judge questions + answers

Every answer leans on a constraint, a formula, or a measured number, never on "our detector is smart."

| Question | Answer |
| --- | --- |
| Why is your allocation actually fair? | Every verified identity has exactly one entry, and the winners are a uniform random sample of entries from a seed committed before entry. Fair means equal per-identity probability; we measure it as advantage ratio ≈ 1 and bot wins inside the hypergeometric chance band. |
| Why not FIFO? | FIFO converts latency and automation into seats. We ran the same attack on FIFO on our own backend: bots won X% with Y% of identities, and arrival order strongly predicted winning. |
| What stops 10,000 fake accounts? | Nothing stops them completely, and we don't claim otherwise. Each needs a verified phone, OTP throttles slow creation, clustering flags farms, flagged winners must re-verify, and seats are non-transferable. What's left gets proportional share, 500 × k/N, not domination. |
| What happens if bot detection fails entirely? | Turn L6–L8 off on the admin panel and rerun: bots still get exactly their identity share, because detection never decided winners. That's the point of the design. |
| How do you guarantee zero overselling? | There are 500 physical seat rows; a claim is one transaction that flips one free row to sold with `FOR UPDATE SKIP LOCKED`; `UNIQUE(drop_id, entry_id)` stops a second seat per entry. A count-based race can't happen because we never count, we take a row. `/integrity` recomputes this by SQL live. |
| Why Redis + PostgreSQL? | Postgres for anything that must be true (transactions, constraints, audit). Redis for anything that must be fast and can be lost (rate buckets, counters, caches). If Redis dies, we get slower, not wrong. |
| How do you handle duplicate requests? | Idempotency key per user action, plus natural idempotency: one entry per identity and one allocation per entry are database constraints. Three identical claims return one identical response. |
| What happens after a lost network connection? | All state is server-side. On reconnect the client calls `GET /me` and retries any pending write with the same key. If the claim had committed, the retry returns the stored success. |
| How did you simulate 50,000 users? | 50,000 logical labelled clients from a multi-process asyncio simulator on the same public API, arriving in a 30 s burst; we report the achieved peak requests/s and concurrent sockets rather than pretending to 50,000 simultaneous sockets on one laptop. |
| What does "fairness" mathematically mean? | Equal win probability per verified identity regardless of arrival time and request count: P(win \| identity) = 500/N. Tested by advantage ratio, a chance band on bot wins, and Spearman correlation between arrival order and winning. |
| What if you flag a legitimate user? | They are not blocked and not down-weighted. If they win, they enter one fresh OTP within the claim window. We measure the false positive rate on simulated humans and keep it under 2%. |
| Couldn't you rig the draw? | The seed hash is published before the window opens and the entry-set hash at close; after reveal anyone can recompute ranks. Mixing in a public randomness beacon after close removes even our ability to choose the seed. |
| Isn't the lottery worse UX than a queue? | Users can enter any time in the window and close the tab; no one stares at a spinner hoping not to time out. That is better UX for humans and worse only for bots. |

## 20. Architecture weaknesses + fixes

The design's real limits are identity cost, operator trust and demo realism; each has a concrete fix folded back into the plan above.

### 3 biggest weaknesses and the fix

| Weakness | Why it matters | Fix (already in the plan) |
| --- | --- | --- |
| Fairness is only as strong as identity cost; simulated OTP has zero real cost | A judge can say "your bots could just make 50,000 phones" | Say it first: we bound attacker share to identity share, show the 500 × k/N curve, and model identity cost as a simulator parameter (bot identity budget) so the scorecard shows share vs budget |
| Operator could rig the draw | A provable draw that trusts us is half-provable | Seed commit before open + entry-set hash at close + client-side verify button; drand as stretch |
| One-laptop load test isn't 50,000 real sockets | Over-claiming load costs credibility | Report achieved RPS and sockets; use a second laptop for the simulator if available |

### 3 realistic attack vectors

1. **Rented real SIMs / OTP farms.** Passes L6 and L8. Residual risk is proportional share only; real-world fix is KYC-grade identity or venue ID check, which we name as the deployment step.
2. **Session theft to claim someone else's offer.** httpOnly + SameSite cookies, token bound to `sid_hash`, offers bound to `user_id`; the stolen session still only ever holds the victim's single seat, never an extra one.
3. **Polling flood from many real sessions to degrade latency for humans.** Server-chosen `poll_after_ms`, 2 s `/me` cache, per-session limits; and because outcomes don't depend on latency, degradation hurts UX, not fairness.

### 3 things judges may criticize

1. "A lottery isn't first-come, users expect a queue." → UX answer in section 19; it's also how the largest ticketing and sneaker drops increasingly work.
2. "Your risk rules are hand-tuned." → Yes, deliberately: transparent, explainable, measured for false positives, and not outcome-deciding.
3. "You simulate your own attackers, of course you win." → Show the FIFO run losing on the same harness, publish scenario files and seeds, and invite a judge to change the attacker parameters live.

### 3 features to simplify or remove

1. Risk-weighted lottery → removed in favour of step-up.
2. WebSockets → replaced by server-paced polling.
3. Prometheus/Grafana → replaced by one `/metrics` JSON.

### 3 features to make especially impressive

1. **Ghosted FIFO vs Fair overlay** with bot wins inside the chance band: the whole thesis in one glance.
2. **Live integrity counter** recomputed by SQL during the claim storm, staying at 0 oversold.
3. **Draw-verify button** that recomputes all ranks in the browser from the revealed seed and turns green.

### Biggest weakness, fixed

The biggest single risk is a judge reframing the project as "you just moved the problem to identity." The fix is to make that move the thesis, not the gap: add an **identity-budget sweep** to the evaluator (bot identity budget 0, 500, 2,000, 5,000, 10,000) and plot bot seat share against identity share for FIFO and Fair. FIFO sits far above the diagonal at every budget; Fair hugs the diagonal. That chart answers the hardest question before it is asked, and it costs Saanvi about an hour of runs.
