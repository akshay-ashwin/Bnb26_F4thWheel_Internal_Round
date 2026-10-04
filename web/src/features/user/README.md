# User journey (Plan 16)

One screen per drop at `/drop/:dropId`, driven only by `GET /drops/{id}` and `GET /drops/{id}/me`.

- `resolveUiState.ts` — the single pure function that picks the screen. Precedence:
  **allocation > entry status > phase** (after "drop not found" and "no session").
- `dropController.ts` — polling (`poll_after_ms` from the server), the out-of-order guard, and the
  three writes (enter, claim, step-up), all through `withRetry` with one `Idempotency-Key` per action.
- `copy.ts` — every user-facing sentence; `copy.test.ts` enforces the copy rules.

## States

| UI state                                               | When                                                                                            |
| ------------------------------------------------------ | ----------------------------------------------------------------------------------------------- |
| `DROP_NOT_FOUND`                                       | `/drops/{id}` is 404                                                                            |
| `VERIFY`                                               | `/me` is 401                                                                                    |
| `ALLOCATED`                                            | `/me.allocation` present, or this tab's claim returned 200 (wins over everything)               |
| `OFFERED` / `STEP_UP` / `WAITLISTED` / `OFFER_EXPIRED` | entry status of the same name (`STEP_UP_REQUIRED` → `STEP_UP`)                                  |
| `NOT_SELECTED`                                         | entry `NOT_SELECTED` or `DISQUALIFIED` (neutral copy), or Fair `DONE` with a `REGISTERED` entry |
| `BEFORE_WINDOW`                                        | phase `SCHEDULED`                                                                               |
| `CAN_ENTER`                                            | Fair, `OPEN`, no entry                                                                          |
| `ENTERED_WAITING_CLOSE`                                | Fair, `OPEN`, `REGISTERED`                                                                      |
| `WAITING_DRAW`                                         | Fair, `CLOSED` / `DRAWN` / `CLAIMING`, `REGISTERED`                                             |
| `FIFO_RACE`                                            | FIFO, `OPEN`, no entry or `REGISTERED` ("Get a seat" enters, then claims)                       |
| `SOLD_OUT`                                             | FIFO only: `seats_remaining` is 0 or phase `DONE`, no allocation                                |
| `REGISTRATION_CLOSED`                                  | no entry and the window is over (and not FIFO sold out)                                         |

Overlays (never replace the screen): reconnecting banner, "Slow down a moment", generic error with
request id.

## Error code → UI

| Code                                                                    | Where it shows                     | User sees                                                                                         | Client retries?                                                                                                                                        |
| ----------------------------------------------------------------------- | ---------------------------------- | ------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------ |
| `RATE_LIMITED`                                                          | current screen                     | "Slow down a moment" only if it repeats                                                           | yes, after `retry_after_ms` + jitter, at most twice                                                                                                    |
| `OTP_THROTTLED`                                                         | verify screen                      | "Too many codes requested. You can ask for a new one in m:ss."                                    | no                                                                                                                                                     |
| `INVALID_PHONE`                                                         | verify screen                      | "That doesn't look like an Indian mobile number."                                                 | no                                                                                                                                                     |
| `OTP_INVALID`                                                           | verify / step-up                   | "That code didn't match. Check it and enter it again."                                            | no (user re-enters)                                                                                                                                    |
| `OTP_EXPIRED`                                                           | verify screen, back to phone       | "That code has expired. Send a new one."                                                          | no                                                                                                                                                     |
| `UNAUTHENTICATED`                                                       | `VERIFY`                           | sign-in screen; a pending claim marker is kept                                                    | no                                                                                                                                                     |
| `WINDOW_CLOSED`                                                         | `REGISTRATION_CLOSED` (from `/me`) | "Entry is closed"                                                                                 | no                                                                                                                                                     |
| `WINDOW_NOT_OPEN`                                                       | `BEFORE_WINDOW` (from `/me`)       | countdown / "Opens soon"                                                                          | no                                                                                                                                                     |
| `TOKEN_INVALID`                                                         | `OFFERED`                          | nothing on the first one: fresh token from `/me`, same key; a second one shows the generic error  | once                                                                                                                                                   |
| `NOT_OFFERED`                                                           | screen from `/me`                  | that screen's copy                                                                                | no                                                                                                                                                     |
| `OFFER_EXPIRED`                                                         | `OFFER_EXPIRED`                    | "Your confirmation window ended, so the seat went to the next person."                            | no                                                                                                                                                     |
| `SOLD_OUT`                                                              | `SOLD_OUT`                         | "Sold out"                                                                                        | no                                                                                                                                                     |
| `STEP_UP_REQUIRED`                                                      | `STEP_UP` (from `/me`)             | "Quick check: enter the code we just sent…"                                                       | after the step-up                                                                                                                                      |
| `IDEMPOTENCY_KEY_REUSED`, `IDEMPOTENCY_KEY_MISSING`, `VALIDATION_ERROR` | current screen                     | generic error + request id                                                                        | no (client bug)                                                                                                                                        |
| `NOT_FOUND`                                                             | `DROP_NOT_FOUND`                   | "Drop not found"                                                                                  | no                                                                                                                                                     |
| `SERVICE_UNAVAILABLE`, other 5xx, network error, timeout                | current screen                     | "Reconnecting… your place is safe"                                                                | yes, same key, backoff 300 ms → 5 s with full jitter, honours `Retry-After`, up to 8 tries; waiting for the browser to come back online does not count |
| `INVALID_TRANSITION`                                                    | —                                  | admin only                                                                                        | —                                                                                                                                                      |
| anything else                                                           | current screen                     | "Something went wrong. Your place is safe; we'll keep trying." + request id in a details expander | no                                                                                                                                                     |

## Copy rules

1. Never accuse: no "bot", "suspicious", "fraud", "flagged" (step-up is a "quick check").
2. Never imply retrying, refreshing or speed helps: no "try again", "retry", "refresh", "hurry",
   "be quick"; terminal screens have no buttons.
3. Show the promise: "Arriving early doesn't help." and the first 12 characters of `seed_commit`
   with a "How the draw works" link.

The test matches whole words, case-insensitive (`robot` does not match `bot`).
