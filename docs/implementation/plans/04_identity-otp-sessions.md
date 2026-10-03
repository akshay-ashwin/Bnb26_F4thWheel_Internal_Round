# Plan 04 — Identity: Phone OTP, Users, Sessions (Cookie + Bearer)

| Field | Value |
|---|---|
| Design-doc sections | §4 step 1 Verify, §7 What lives where (users), §8 L6 (hooks only), §11 `/auth/otp/request`, `/auth/otp/verify`, conventions on auth, §20 attack vector 2 (session theft) |
| Original owner | Akshay |
| Depends on | Plan 03 |
| Unlocks | Plans 05–13; simulator login (18); user verify screen (16) |
| Target time | 2 hours |

## 0. Mandatory operating protocol (read before writing anything)

1. Read `CLAUDE.md` (repo root) — it overrides this plan if they conflict.
2. Read `docs/implementation/PLAN_CHANGELOG.md`. If an earlier step edited this plan, the edited text is authoritative.
3. Read the review log of the previous plan in `docs/review-logs/` for handover notes.
4. Run the Pre-flight checks in section 3. Do not start building on a broken foundation.

Standing rules that apply to every line of work in this plan:

- **R1 — No AI attribution.** Never add Claude/Anthropic as author, co-author or contributor; no `Co-Authored-By` trailers, no "Generated with Claude Code" lines in commits or PRs; never touch git identity; never use `--no-verify`.
- **R2 — Better solution wins, and the future is rewritten.** If you find a better approach at any point, implement it (respecting the invariants in CLAUDE.md), write a Deviation Record in `docs/decisions/`, edit every affected later plan, and log it in `PLAN_CHANGELOG.md`.
- **R3 — Refine every step.** Do the Refinement Pass at the end of this plan before declaring it done, and refine the remaining plans if this step taught you something.
- **R4 — Review log.** Write `docs/review-logs/04-identity-otp-sessions.md` in plain language from the template.

## 1. Goal

Make "one phone number = one identity" real and cheap to check, issue sessions that work identically for browsers (httpOnly cookie) and the simulator (bearer token), and leave clean hook points for OTP abuse controls (L6) and risk signals (L7).

## 2. Scope

In scope: phone normalization + hashing, OTP request/verify, simulated SMS provider, user creation, session creation/reuse/lookup, client IP + device + UA extraction, current-session dependency, `sid_hash`.
Out of scope: OTP throttling rules (Plan 13 — leave a hook), step-up OTP (Plan 11 reuses this module).

## 3. Pre-flight checks

1. Stubs for both auth endpoints exist with frozen models (Plan 03).
2. `PHONE_PEPPER` and `SESSION_SECRET` set.

## 4. Implementation steps

### 4.1 Phone normalization and hashing

1. Parse with the `phonenumbers` library; default region IN; reject anything not a valid mobile number → `400 INVALID_PHONE`.
2. Normalize to E.164.
3. `phone_hash = HMAC-SHA256(PHONE_PEPPER, e164)` hex. The raw phone is never stored or logged. The pepper lives only in env.
4. Also derive (for L6, Plan 13) a `phone_prefix` = first N digits of the national number (configurable, default 6) — computed here, passed to the hook, never stored in Postgres.

### 4.2 OTP storage (Redis) and the simulated SMS provider

- Storage decision: OTP requests live in Redis only (`otp:req:{request_id}` hash: phone_hash, otp_hash, device_id, attempts, created_ms; TTL 300 s) plus a dedupe key `otp:phone:{phone_hash}` → request_id with TTL 30 s. If Redis is down, OTP login is unavailable (returns 503) — acceptable because identity is established before the drop, and the design says failures cost availability, not integrity. Record this.
- OTP: 6 digits from a CSPRNG; store only its HMAC (keyed with SESSION_SECRET) — not plaintext.
- SMS provider interface with one implementation, `SimulatedSmsProvider`, that logs "OTP sent to <phone_hash prefix>" (never the OTP unless SIM_MODE and log level DEBUG). Real provider is out of scope; the interface makes it a swap.

### 4.3 `POST /auth/otp/request`

Flow:
1. Validate body `{phone, device_id}`; device_id must be a reasonable opaque string (length 8–128, safe charset) else 400.
2. Normalize + hash phone.
3. Call the L6 hook `otp_request_guard(phone_hash, phone_prefix, device_id, client_ip)` — no-op now; Plan 13 makes it throw `429 OTP_THROTTLED`.
4. If `otp:phone:{phone_hash}` exists → return the SAME request_id (contract: same phone within 30 s returns same request_id) without generating a new OTP.
5. Else generate OTP + request_id (random URL-safe 128-bit), store, send via provider.
6. Record `otp_requested_at_ms` for the L7 "verified < 2 s after request" signal (store inside the request hash).
7. Respond `200 {request_id, expires_in_s, dev_otp?}` — `dev_otp` present ONLY when `SIM_MODE=true`.

### 4.4 `POST /auth/otp/verify`

Flow:
1. Load request by id; missing → `410 OTP_EXPIRED` (expired and unknown look the same — don't leak existence).
2. If attempts ≥ 5 → delete request, `401 OTP_INVALID`.
3. Constant-time compare HMAC of submitted OTP with stored hash; mismatch → increment attempts, `401 OTP_INVALID`.
4. device_id in the verify body should match the request's device_id; mismatch → `401 OTP_INVALID` (prevents OTP relay to another device; record decision).
5. On success, delete the OTP request (single use).
6. Upsert user by phone_hash: insert with new uuid + new random `public_id` (e.g. 16 random bytes base32, lowercase, no padding — this exact format matters for the draw in Plan 09; record it in the GLOSSARY) and first_device_id/first_ip; on conflict fetch the existing user. Must be race-safe when two verifies for the same phone land simultaneously (insert … on conflict do nothing, then select).
7. Session reuse: if an unrevoked session exists for (user_id, device_id), reuse it (contract: re-verify returns existing session for same device). Otherwise create a new session row with device_id, ip, ua_hash.
8. Compute verify latency since OTP request and pass `(user_id, device_id, client_ip, ua_hash, verify_latency_ms)` to the L7 hook `on_identity_verified(...)` — no-op now; Plan 13 uses it.
9. Respond `200 {session_token, user_public_id}` and set the cookie.

### 4.5 Session token format

Decision (recommended): stateless-signed session id. `session_token = <session_uuid>.<HMAC-SHA256(SESSION_SECRET, session_uuid) base64url>`. Verification is a cheap HMAC (no DB hit), which the L3 rate limiter (Plan 12) needs before any Postgres access. Revocation is checked by loading the session row (cached, below). Alternative (opaque random token + hash column) rejected because it requires a DB/Redis lookup just to identify the session in the rate limiter. Record this.

`sid_hash = SHA-256(session_uuid)` hex — used to bind admission tokens (Plan 10). Never put the raw session id inside admission tokens.

### 4.6 Cookie

Name `fd_session`; httpOnly; `SameSite=Lax`; `Secure` from `COOKIE_SECURE` (true in demo if served over HTTPS); `Path=/api`; Max-Age 24 h. Because web and api share an origin via proxy (Plan 01), no CORS credentials setup is needed; if CORS is ever enabled, restrict origins explicitly.

### 4.7 Current-session dependency

1. Read cookie first; else `Authorization: Bearer <session_token>`.
2. Verify signature; failure → `401 UNAUTHENTICATED`.
3. Load session: Redis cache `sess:{id}` (user_id, user_public_id, device_id, revoked flag) TTL 60 s; on miss or Redis down, load from Postgres and repopulate. Revoked → 401.
4. Expose a request-scoped Session context: session_id, sid_hash, user_id, user_public_id, device_id.

### 4.8 Client IP, device, UA extraction (shared helper, used by L2, L6, L7, entries)

- If `SIM_MODE=true` and `X-Sim-Client-IP` is present and valid → use it (documented test-harness substitute, design §15).
- Else if the direct peer is in `TRUSTED_PROXY_CIDRS` → first untrusted address from `X-Forwarded-For` (right-to-left).
- Else the socket peer address.
- `/24` network derived for IPv4; for IPv6 use /48 (record).
- UA hash = SHA-256 of the User-Agent string truncated to 512 chars.
- device_id: from body on auth endpoints; on other endpoints taken from the session (not from headers — avoids spoofing per request).

## 5. Tests

1. Valid Indian mobile normalizes; invalid → 400 INVALID_PHONE; two formats of the same number produce the same phone_hash.
2. Request twice within 30 s → same request_id; after 30 s → new id.
3. `dev_otp` present only in SIM_MODE.
4. Wrong OTP increments attempts; 5 wrong → request dead; expired → 410.
5. Verify creates exactly one user under 50 concurrent verifies for the same phone (race test).
6. Re-verify from same device returns the same session; different device creates a new session for the same user.
7. Cookie flags correct; bearer token works identically; tampered token → 401.
8. Revoked session → 401 within cache TTL bounds (document the up-to-60 s window, or invalidate cache on revoke — recommended).
9. `X-Sim-Client-IP` ignored when SIM_MODE=false.
10. No phone, OTP, or token appears in logs (assert via captured logs).

## 6. Verification / Definition of Done

All tests pass; a manual run with curl: request → verify → call a protected stub with cookie and with bearer → both identify the same user_public_id. Measure: verify endpoint p95 under 200 concurrent verifies (record number).

## 7. Plan-update obligations

- If the public_id format changed → Plans 09 (draw message encoding), 17 (browser verify), 19 (evaluator join).
- If the session token format changed → Plans 10 (sid_hash), 12 (L3 key extraction), 18 (simulator auth).
- If OTP storage moved to Postgres → Plans 11 (step-up), 13 (L6).

## 8. Review log must explain

- What "identity" means in this system and why the phone is hashed with a secret pepper.
- The full login story from typing a phone to holding a session, for both browser and simulator.
- Why session tokens are signed (cheap check before any DB/Redis work) and what sid_hash is for.
- How the server decides a client's IP, and the SIM_MODE caveat.
- The two hook points left for Plans 13 and 11.

## Close-out sequence (do all of these, in order)

1. **Definition of Done.** Run every check in the Verification section. Record the actual numbers (tests passed, latencies, oversold counts) — never write a number you did not observe.
2. **Refinement Pass (R3).** Re-read the full diff as a strict reviewer; remove dead code and debug output; check names against `docs/GLOSSARY.md`; walk each failure path (Postgres slow, Redis gone, request retried, two tabs at once); run the whole test suite, not just the new tests; ask "is there a simpler way?" and apply R2 if yes.
3. **Downstream sync (R2).** Open each plan listed in "Plan-update obligations" (and any other later plan you affected). Edit stale instructions, mark them `> Updated by D-00X`, and append to `PLAN_CHANGELOG.md`. If nothing changed, write "no downstream changes" in the review log.
4. **Review log (R4).** Write `docs/review-logs/04-identity-otp-sessions.md` from the template, covering every item in this plan's "Review log must explain" list.
5. **Attribution check (R1).** List unpushed commits with their author and full message; confirm the author is the repo owner and no message contains AI attribution. Fix by rewording before pushing if needed.
6. **Commit** the plan's final state with a conventional message (e.g. `feat(alloc): atomic claim with skip-locked seats`) and the repo owner's identity.
7. **Chat summary.** 5–10 lines: what is done, key numbers, deviations, link to the review log, what Plan 05 starts with.
