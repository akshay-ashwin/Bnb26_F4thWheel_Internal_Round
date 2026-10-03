# Plan 16 — User Journey Screens (Verify → Enter → Wait → Offered/Step-Up → Allocated/Waitlist)

> **[CUT] by D-004 (2026-10-04, LEAN MODE):** do not build: a11y/Lighthouse and QR placeholder; keep only 3 e2e tests (happy path, refresh, offline-during-confirm). Also skip anything in "stretch", "optional" or "if time permits" text.

| Field | Value |
|---|---|
| Design-doc sections | §10 User view table, §13 state machine, §7 Session reliability, §14 "User sees" column, §18 demo 0:30–1:00 |
| Original owner | Ameya |
| Depends on | Plan 15; real API from Plans 04–11 (mocks until then) |
| Unlocks | Demo user segment; Playwright e2e reliability proofs |
| Target time | 3 hours |

## 0. Mandatory operating protocol (read before writing anything)

1. Read `CLAUDE.md` (repo root) — it overrides this plan if they conflict.
2. Read `docs/implementation/PLAN_CHANGELOG.md`. If an earlier step edited this plan, the edited text is authoritative.
3. Read the review log of the previous plan in `docs/review-logs/` for handover notes.
4. Run the Pre-flight checks in section 3. Do not start building on a broken foundation.

Standing rules that apply to every line of work in this plan:

- **R1 — No AI attribution.** Never add Claude/Anthropic as author, co-author or contributor; no `Co-Authored-By` trailers, no "Generated with Claude Code" lines in commits or PRs; never touch git identity; never use `--no-verify`.
- **R2 — Better solution wins, and the future is rewritten.** If you find a better approach at any point, implement it (respecting the invariants in CLAUDE.md), write a Deviation Record in `docs/decisions/`, edit every affected later plan, and log it in `PLAN_CHANGELOG.md`.
- **R3 — Refine every step.** Do the Refinement Pass at the end of this plan before declaring it done, and refine the remaining plans if this step taught you something.
- **R4 — Review log.** Write `docs/review-logs/16-user-journey-ui.md` in plain language from the template.

> Updated by D-001 (2026-10-04): stack rules apply to this plan. Everything runs through Docker Compose and `uv run fd <task>` (no `make`, no host Python or Node), files are LF, Node is 24 LTS (D-002), macOS and Windows are both supported, and fullstack-dev-skills may be used as advice but never its `project:*` workflow commands. CLAUDE.md always wins. See CLAUDE.md "Stack and platform rules" and `docs/decisions/D-001-cross-platform-docker-uv-node22-skills.md`. Read any `make X` below as `uv run fd X`.

## 1. Goal

One screen, driven entirely by `GET /me` and `GET /drops/{id}`, that renders exactly one clear state at a time, never lies about what is happening, keeps working through refresh/reconnect/multi-tab, and makes the fairness promise visible to ordinary users ("arriving early doesn't help", the seed commitment).

## 2. Scope

In scope: state resolver, every screen in the design table, phone/OTP flow, error-code → UI mapping, copywriting rules, reconnect banner, rate-limit message, seed commitment + "how the draw works", e2e tests.
Out of scope: dashboard (17).

## 3. Pre-flight checks

1. Plan 15 engine pieces exist; mocks cover every state.
2. Contract examples for `/me` in every status available.

## 4. State resolver (single pure function; unit-test exhaustively)

Inputs: drop (mode, phase, times, seed_commit), `/me` (or none if unauthenticated), connectivity, last error. Output: one UI state:

| UI state | Condition |
|---|---|
| `VERIFY` | no session (401 from `/me`) |
| `BEFORE_WINDOW` | phase SCHEDULED |
| `CAN_ENTER` | phase OPEN, no entry |
| `ENTERED_WAITING_CLOSE` | Fair, entry REGISTERED, phase OPEN |
| `WAITING_DRAW` | Fair, phase CLOSED (or DRAWN without offer yet) |
| `FIFO_RACE` | FIFO, phase OPEN, entry REGISTERED (claim available) |
| `OFFERED` | entry OFFERED |
| `STEP_UP` | entry STEP_UP_REQUIRED |
| `ALLOCATED` | allocation present (highest precedence over everything) |
| `WAITLISTED` | entry WAITLISTED |
| `OFFER_EXPIRED` | entry OFFER_EXPIRED |
| `NOT_SELECTED` | entry NOT_SELECTED, or phase DONE without allocation |
| `SOLD_OUT` | FIFO DONE without allocation |
Overlays (not states): reconnect banner, rate-limit notice, toast for transient errors.

Precedence rule: allocation > entry status > phase. Document it in the review log.

## 5. Screens (copy and behaviour)

1. **Verify phone.** Phone input (Indian format hint), "Send code" → OTP input (6 boxes, paste-friendly, auto-submit on 6 digits). In SIM_MODE show the `dev_otp` in a clearly marked dev hint. Handle INVALID_PHONE, OTP_THROTTLED (show wait time), OTP_INVALID (attempts left if known), OTP_EXPIRED (resend). After verify, return to the drop.
2. **Before window.** Event card, capacity "500 seats", countdown to open if `reg_opens_at` known (or "Opens soon"), small mono text "Draw commitment: <first 12 chars of seed_commit>…" with "How the draw works" link.
3. **Can enter (Fair).** Big "Enter the draw" button; under it "Entering at any time in the window gives the same chance. Arriving early doesn't help." Countdown to close. Button: disabled immediately on tap (Plan 15 Button), uses the entries action key.
4. **Entered, waiting for close.** "You're in. Arriving early doesn't help." + "You can close this tab — your entry is saved." + countdown to draw + live entries count (from `/drops/{id}` or a small field — if not available, omit; don't fake).
5. **Waiting for draw.** "The draw is happening…" + short explanation; poll fast per server.
6. **FIFO race (baseline mode).** Plain "Get a seat" button that claims immediately (the old, unfair way). Label mode subtly: "First come, first served" — the demo needs this to look like a normal ticket site.
7. **Offered.** "You won a seat. Confirm within m:ss" (countdown from `offer_expires_at` with server clock) + Confirm button → claim with action key `claim`; on TOKEN_INVALID auto-refresh `/me` once and retry (Plan 15). When < 20 s, emphasise urgency (colour + aria-live).
8. **Step-up.** "Quick check: enter the code we just sent." OTP input → `POST /step-up` with key; never use words like "suspicious" or "bot". On success → back to Offered with the remaining time. On 3 failures → expired state copy.
9. **Allocated.** Seat number large, confirmation id (allocation_id short form), QR placeholder (generated locally from the allocation id; it's a placeholder — say so in a tooltip), "This seat is tied to your verified phone and can't be transferred." Same in every tab and after refresh.
10. **Waitlisted.** "You're #37 on the waitlist. Seats free up as offers expire." Live position; reassure "You can close this tab; we'll keep your place" (only true if they come back — phrase: "check back before the claim window ends"). Record final copy.
11. **Offer expired.** "Your confirmation window ended, so the seat went to the next person." No retry button.
12. **Not selected / Sold out.** Clear final state; no retry button (design rule: never imply retrying helps). Link to the draw proof once available ("Check the draw yourself").
13. **Reconnect banner.** "Reconnecting… your place is safe" with spinner; on reconnect, refetch `/me` and resume any pending action with the same key.
14. **Rate limited.** "Slow down a moment" honouring Retry-After; should never appear at normal pace (if it appears in human sim runs, that's a bug for Plan 12).

## 6. Error code → UI mapping (put the full table in `web/src/features/user/README.md`)
Map every code from `docs/contract/error-codes.md` to: which state/overlay, the exact user-facing copy, retry or not. Unknown codes → generic "Something went wrong. Your place is safe; we'll keep trying." with the request id in a details expander.

## 7. "How the draw works" page
Plain-language explanation of commit → enter → reveal → rank, with the formula in one line, a link to verify once the seed is revealed (the public proof), and the statement "Your chance is the same no matter when you entered in the window."

## 8. E2E tests (Playwright, against the real stack in Fair mode via an admin helper)

> Updated by D-001 (2026-10-04): run with `uv run fd test-web --e2e` (Playwright in a container on the compose network, Plan 15 §4.13), so results are the same on macOS and Windows. Network-emulation tests (offline during confirm) use Playwright's own emulation, not host tools. Screenshots saved to `docs/screens/` come from the bind mount; they must be PNG with no OS-specific chrome.

1. Happy path: verify → enter → (admin close + draw) → offered → confirm → allocated.
2. Refresh at every state → same screen.
3. Three tabs open; confirm in tab 2 → all tabs show the same seat within one poll.
4. Go offline during confirm (Playwright network emulation), come back → allocated, exactly one allocation in export.
5. Token expiry while offered (short token TTL config) → confirm still succeeds via auto-refresh.
6. Waitlisted user gets promoted after an offered user lets their offer expire (short claim window config).
7. FIFO mode: "Get a seat" works; sold-out state when full.
8. Step-up path with dev_otp in SIM_MODE.

Also: Lighthouse/axe check for accessibility basics; mobile viewport 360 px.

## 9. Verification / Definition of Done

All e2e tests pass on the real stack; screenshots of each state saved to `docs/screens/` (useful as demo backup).

## 10. Plan-update obligations

- If copy or states changed, update Plan 19's demo script lines that quote the UI.
- If any API gap was found (e.g., entries count not exposed), raise it via R2 in the backend plan or document the omission.

## 11. Review log must explain

- The state precedence rule and the full state list in plain words.
- How each reliability promise (refresh, reconnect, tabs, lost response) is satisfied, with the e2e test that proves it.
- The copy principles (never accuse, never imply retries help, show the commitment).

## Close-out sequence (do all of these, in order)

1. **Definition of Done.** Run every check in the Verification section. Record the actual numbers (tests passed, latencies, oversold counts) — never write a number you did not observe.
2. **Refinement Pass (R3).** Re-read the full diff as a strict reviewer; remove dead code and debug output; check names against `docs/GLOSSARY.md`; walk each failure path (Postgres slow, Redis gone, request retried, two tabs at once); run the whole test suite, not just the new tests; ask "is there a simpler way?" and apply R2 if yes.
3. **Downstream sync (R2).** Open each plan listed in "Plan-update obligations" (and any other later plan you affected). Edit stale instructions, mark them `> Updated by D-00X`, and append to `PLAN_CHANGELOG.md`. If nothing changed, write "no downstream changes" in the review log.
4. **Review log (R4).** Write `docs/review-logs/16-user-journey-ui.md` from the template, covering every item in this plan's "Review log must explain" list.
5. **Attribution check (R1).** List unpushed commits with their author and full message; confirm the author is the repo owner and no message contains AI attribution. Fix by rewording before pushing if needed.
6. **Commit** the plan's final state with a conventional message (e.g. `feat(alloc): atomic claim with skip-locked seats`) and the repo owner's identity.
7. **Chat summary.** 5–10 lines: what is done, key numbers, deviations, link to the review log, what Plan 17 starts with.
