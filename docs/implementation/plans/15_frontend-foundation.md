# Plan 15 — Frontend Foundation: App Shell, Typed API Client, Retry/Idempotency Engine, Polling, Mocks

> **[CUT] by D-004 (2026-10-04, LEAN MODE):** do not build: BroadcastChannel, MSW scenario panel (a few fixtures only), generated-types CI check. Also skip anything in "stretch", "optional" or "if time permits" text.

| Field | Value |
|---|---|
| Design-doc sections | §6 Frontend + Live updates, §7 Idempotency (sessionStorage key), §7 Session reliability, §10 (both views render from two endpoints), §11 conventions, §14 user-visible failure behaviour, §16 Ameya deliverable 1 |
| Original owner | Ameya |
| Depends on | Plan 03 (frozen OpenAPI); later wired to Plans 04–14 |
| Unlocks | Plans 16, 17 |
| Target time | 2.5 hours |

## 0. Mandatory operating protocol (read before writing anything)

1. Read `CLAUDE.md` (repo root) — it overrides this plan if they conflict.
2. Read `docs/implementation/PLAN_CHANGELOG.md`. If an earlier step edited this plan, the edited text is authoritative.
3. Read the review log of the previous plan in `docs/review-logs/` for handover notes.
4. Run the Pre-flight checks in section 3. Do not start building on a broken foundation.

Standing rules that apply to every line of work in this plan:

- **R1 — No AI attribution.** Never add Claude/Anthropic as author, co-author or contributor; no `Co-Authored-By` trailers, no "Generated with Claude Code" lines in commits or PRs; never touch git identity; never use `--no-verify`.
- **R2 — Better solution wins, and the future is rewritten.** If you find a better approach at any point, implement it (respecting the invariants in CLAUDE.md), write a Deviation Record in `docs/decisions/`, edit every affected later plan, and log it in `PLAN_CHANGELOG.md`.
- **R3 — Refine every step.** Do the Refinement Pass at the end of this plan before declaring it done, and refine the remaining plans if this step taught you something.
- **R4 — Review log.** Write `docs/review-logs/15-frontend-foundation.md` in plain language from the template.

> Updated by D-001 (2026-10-04): stack rules apply to this plan. Everything runs through Docker Compose and `uv run fd <task>` (no `make`, no host Python or Node), files are LF, Node is 24 LTS (D-002), macOS and Windows are both supported, and fullstack-dev-skills may be used as advice but never its `project:*` workflow commands. CLAUDE.md always wins. See CLAUDE.md "Stack and platform rules" and `docs/decisions/D-001-cross-platform-docker-uv-node22-skills.md`. Read any `make X` below as `uv run fd X`.
> Plan 01 handover (2026-10-04): `web/` already runs on Node 24 LTS (D-002) with pnpm 12.8.1 pinned in `packageManager`, Vite 8, React 19, **Tailwind v4 through `@tailwindcss/vite` (CSS-first: `src/index.css` is `@import "tailwindcss"`; there is no `tailwind.config.js`, so design tokens go in an `@theme` block)**, TypeScript **pinned `~6.0.3`** (typescript-eslint 8.x does not support TS 7 yet; revisit when it does), ESLint **pinned `~10.11.0`** with a flat config (pnpm 12 refuses releases younger than a day, so add new dependencies at the latest version that is at least a day old), Prettier (LF, width 100) and Vitest 5 (`pnpm test` is `vitest run --passWithNoTests`). Scripts: `lint`, `typecheck`, `format`, `format:check`, `test`, `build`. `fd test-web --e2e` still prints "not implemented yet (Plan 15)". Recharts and routing are not installed yet. Dev proxy `/api` to `api:8000` and the prod nginx profile (`web-prod`, port 8080) are verified. `node_modules` and the pnpm store live in the `web-node-modules` named volume; the container runs `pnpm install --frozen-lockfile` on start. Regenerate `pnpm-lock.yaml` in a container (`pnpm install --lockfile-only`), never on the host.

## 1. Goal

A React app whose every network interaction is correct under refresh, reconnect, multi-tab and lost responses: typed from the OpenAPI spec, retries safely with stable idempotency keys, paces polling by the server, knows the server's clock, and can run entirely on mocks that reproduce every state and error in the contract.

## 2. Scope

In scope: Vite/React/TS/Tailwind/Recharts setup, routing, typed client generation, error mapping, server-time sync, idempotency key manager, retry policy, poller, connectivity state, device id, state store, MSW mocks with a scenario switcher, design tokens, test setup.
Out of scope: screens (16, 17).

## 3. Pre-flight checks

1. `docs/contract/openapi.json` and `docs/contract/examples/` exist (Plans 03, 14 — if 14 isn't done, hand-write examples from the contract for now and mark them).
2. Vite dev proxy `/api → api:8000` works (Plan 01).

## 4. Implementation steps

### 4.1 Project structure (`web/src/`)
`app/` (router, providers), `api/` (generated types, client, errors, retry, idempotency, time), `state/` (stores), `features/user/`, `features/admin/`, `components/` (shared UI), `mocks/` (MSW handlers + fixtures), `lib/` (utils), `workers/` (Web Worker for draw verification, used by Plan 17).

### 4.2 Routing
- `/` → current drop selector (or redirect to the active drop).
- `/drop/:dropId` → user journey (Plan 16).
- `/admin` and `/admin/drop/:dropId` → judge dashboard (Plan 17), gated by an admin key prompt stored in sessionStorage (never localStorage; never in the URL).
- `/how-it-works` → static explainer of the draw (linked from the seed commitment).

### 4.3 Typed API client
- Generate TS types from `openapi.json` (openapi-typescript) into `api/generated/`; a script regenerates and CI fails on drift.
- One thin fetch wrapper: base `/api`, `credentials: 'same-origin'` (cookie session), JSON in/out, attaches `Idempotency-Key` when given, attaches `X-Admin-Key` for admin calls, parses `server_time` on every response (success or error) and feeds the clock sync, converts error envelopes into a typed `ApiError {code, status, message, retryAfterMs}`.
- Network failures → `NetworkError`; timeouts via AbortController (default 10 s, claim 8 s).

### 4.4 Server clock sync
Maintain `offset = server_time − midpoint(request start, response end)`; smooth with an exponential moving average; ignore samples with RTT > 2 s. All countdowns (window close, offer expiry) use `Date.now() + offset`. Expose `serverNow()`. Design rule: timers come from the server, not the client clock.

### 4.5 Idempotency key manager
- Key per (dropId, action) stored in sessionStorage as `idem:{dropId}:{action}` → UUID (crypto.randomUUID).
- Created on first attempt, reused on every retry and after page refresh in the same tab, cleared only on a terminal outcome (success, or a final business error like NOT_OFFERED/SOLD_OUT/OFFER_EXPIRED).
- A second tab gets its own key (sessionStorage is per tab) — correct by design, because natural idempotency on the server collapses them (Plan 08 multi-tab test).
- Pending action marker `pending:{dropId}:{action}` lets the app resume an interrupted claim after refresh (Plan 16 uses it: on load, if a claim was pending, re-check `/me` and retry with the same key if still OFFERED).

### 4.6 Retry policy (single function used by all writes)

| Outcome | Behaviour |
|---|---|
| Network error, timeout, 502/503/504 | Retry with SAME key, exponential backoff 300 ms → 5 s with full jitter, honour Retry-After; show "Reconnecting… your place is safe" after first failure |
| 429 RATE_LIMITED | Wait `retry_after_ms` (plus jitter), retry once or twice; show "Slow down a moment" only if it persists |
| 401 TOKEN_INVALID (claim) | Refresh `/me` once to get a fresh token, retry with the same key; then surface error |
| 401 UNAUTHENTICATED | Go to verify screen, keep pending marker |
| Other 4xx | No retry; map to UI state |
| 200/201 | Done; clear key |

Never auto-retry in a way that implies speed helps (no spam loops); max attempts bounded.

### 4.7 Poller
- One poller per tab for `/me`, scheduled by `poll_after_ms` from the last response (fallback 3 s); pause when `document.hidden` and resume with an immediate fetch on visibility; immediate refetch after any write, on `online` event, and on reconnect.
- Optional (record choice): BroadcastChannel to share the latest `/me` between tabs and reduce polling; if implemented, only the leader tab polls. Not required for correctness.
- Admin poller: fixed 1 s for metrics/integrity/sim, pausing when hidden.

### 4.8 Connectivity state
Global store: `online | reconnecting | offline`, driven by fetch outcomes and browser online/offline events. Drives the banner.

### 4.9 Device id
On first load, generate a random device id (UUID) and keep it in localStorage (persists across sessions on this browser; it's a fingerprint-lite input to L6/L7 and sent only in OTP request/verify bodies). Note in the review log that clearing storage changes it — fine for this design.

### 4.10 State management
Lightweight store (Zustand recommended) for: session presence, `/me` snapshot, drop info, connectivity, clock. Server state is the truth; the store only mirrors the last response. No optimistic seat assignment ever.

### 4.11 Mocks (MSW) and scenario switcher
- Handlers for every endpoint using contract examples.
- A dev-only floating panel to choose a mock scenario: before window, window open (verified/unverified), entered, waiting for draw, offered (with countdown), step-up, allocated, waitlisted (#37), not selected, sold out, rate-limited, connection lost (handlers fail), token invalid then recovered, lost response (server "commits" but returns network error, next `/me` shows allocated).
- Toggle via `VITE_USE_MOCKS`.

### 4.12 Design tokens and base components
Tailwind theme: semantic colours (success, warning, danger, info, neutral), large readable type for the dashboard (projector), dark theme for the dashboard, light for the user app. Base components: Button (with built-in "busy/disabled after first tap"), Card, Banner, Countdown (server-clock based), Modal, Badge, Stat, Spinner. Accessibility: focus states, aria-live for status changes, colour never the only signal.

### 4.13 Testing setup
Vitest + Testing Library for units; Playwright for e2e (Plan 16/17 add specs) runnable against mocks and against the real stack.

> Updated by D-001 (2026-10-04): everything runs in containers, so there is no host Node. Unit tests run in the `web` container (Node 22 LTS, `uv run fd test-web`). For e2e, add a compose service under profile `e2e` that runs Playwright with its browsers inside a Linux container on the compose network (`uv run fd test-web --e2e`), so the real-stack tests behave the same on macOS and Windows. Use Playwright's official image or `playwright install --with-deps` on the Node 22 `web` image; pin the Playwright version and record which route you chose (the official image's bundled Node version must be checked against Node 22). Test reports, traces and screenshots are written to a bind-mounted, git-ignored folder; curated screenshots go to `docs/screens/`. Dev-server file watching uses polling (Plan 01 §4.4). (Node later moved to 24 LTS by D-002.)

## 5. Tests

1. Clock sync converges on a mocked server offset of +7 s within 3 responses.
2. Idempotency key stable across retries and refresh; cleared on success.
3. Retry policy table: each row covered with mocked responses.
4. Poller honours poll_after_ms, pauses when hidden, refetches on focus.
5. Lost-response scenario: UI ends on allocated without a second claim key.
6. Type generation script produces no diff on CI.

## 6. Verification / Definition of Done

The web dev server (started by `uv run fd up`; it runs `pnpm dev` inside the `web` container with `VITE_USE_MOCKS=true` for mock mode) with mocks shows a placeholder page per state via the switcher; unit tests pass; the client works against the real API for `/drops/{id}` and `/me`.

## 7. Plan-update obligations

- Plan 16: uses the retry engine, key manager, pending markers, poller and Countdown — no ad-hoc fetches.
- Plan 17: uses the admin poller and the worker folder.
- If you adopted BroadcastChannel, document the leader-tab rule in Plan 16.

## 8. Review log must explain

- How the app survives refresh, network drop, multiple tabs and lost responses — one story each.
- Why countdowns use server time.
- The retry table in plain words, and why retries never make a user "faster".
- How to run the app on mocks and switch states.

## Close-out sequence (do all of these, in order)

1. **Definition of Done.** Run every check in the Verification section. Record the actual numbers (tests passed, latencies, oversold counts) — never write a number you did not observe.
2. **Refinement Pass (R3).** Re-read the full diff as a strict reviewer; remove dead code and debug output; check names against `docs/GLOSSARY.md`; walk each failure path (Postgres slow, Redis gone, request retried, two tabs at once); run the whole test suite, not just the new tests; ask "is there a simpler way?" and apply R2 if yes.
3. **Downstream sync (R2).** Open each plan listed in "Plan-update obligations" (and any other later plan you affected). Edit stale instructions, mark them `> Updated by D-00X`, and append to `PLAN_CHANGELOG.md`. If nothing changed, write "no downstream changes" in the review log.
4. **Review log (R4).** Write `docs/review-logs/15-frontend-foundation.md` from the template, covering every item in this plan's "Review log must explain" list.
5. **Attribution check (R1).** List unpushed commits with their author and full message; confirm the author is the repo owner and no message contains AI attribution. Fix by rewording before pushing if needed.
6. **Commit** the plan's final state with a conventional message (e.g. `feat(alloc): atomic claim with skip-locked seats`) and the repo owner's identity.
7. **Chat summary.** 5–10 lines: what is done, key numbers, deviations, link to the review log, what Plan 16 starts with.
