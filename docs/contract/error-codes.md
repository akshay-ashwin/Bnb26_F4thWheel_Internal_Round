# Error codes

Every error response is the envelope from [`README.md`](README.md): `{"error": {"code", "message", "retry_after_ms"?}, "server_time"}`. This table is the complete list. "Retry?" says what a well-behaved client does; the UI state names are the screens defined in Plans 15 and 16.

Statuses for codes the design doc lists come from its API section 11. `UNAUTHENTICATED` is 401 (Plan 04), `VALIDATION_ERROR` and `IDEMPOTENCY_KEY_MISSING` are 400 (Plans 03 and 05), `SERVICE_UNAVAILABLE` is 503 with a `Retry-After` header and `INTERNAL` is 500 with a request id and no stack trace (Plan 03).

| Code | HTTP | Meaning | Retry? | UI state it maps to |
| --- | --- | --- | --- | --- |
| `RATE_LIMITED` | 429 | A request-rate bucket (L1–L3) said no. Costs no database work. | Yes, after `retry_after_ms` (plus jitter), once or twice. | Stay on the current screen; show "Slow a moment" only if it persists. |
| `OTP_THROTTLED` | 429 | Too many OTP requests for this phone, device, IP or prefix (L6). | Yes, after `retry_after_ms`. | OTP request screen with a visible cooldown. |
| `INVALID_PHONE` | 400 | The phone number is not valid. | No; fix the input. | Phone field error. |
| `OTP_INVALID` | 401 | Wrong code (sign-in or step-up). | No automatic retry; the user re-enters. | OTP field error; on step-up, stay on the step-up screen. |
| `OTP_EXPIRED` | 410 | The code or its request expired. | No; request a new code. | OTP screen: "Code expired, send a new one". |
| `UNAUTHENTICATED` | 401 | No valid session (or bad admin or telemetry key). | No; sign in again. Keep any pending claim marker. | Verify (sign-in) screen. |
| `WINDOW_CLOSED` | 403 | Registration closed before this entry was made. | No. | "Registration closed" screen. |
| `WINDOW_NOT_OPEN` | 403 | Registration has not opened yet. | Yes, after the open time shown on the drop. | "Waiting for the drop to open" screen. |
| `TOKEN_INVALID` | 401 | Admission token failed signature, `exp`, `jti` or session binding (L4). | Once: refresh `/me` for a fresh token and retry with the same idempotency key; then surface the error. | Offer screen, error banner. |
| `NOT_OFFERED` | 403 | This entry has no offer (not selected, or not its turn). | No. | Result screen: "not selected" or "waiting". |
| `OFFER_EXPIRED` | 409 | The claim window ended before the claim. | No. | Result screen: "offer expired" (waitlist if applicable). |
| `SOLD_OUT` | 409 | All seats are taken. | No. | Result screen: "sold out". |
| `STEP_UP_REQUIRED` | 423 | A flagged winner must re-verify with a fresh OTP first (L8). | After the step-up succeeds. | Step-up screen (OTP to the same phone). |
| `IDEMPOTENCY_KEY_REUSED` | 422 | The same idempotency key was reused with a different request. | No; this is a client bug. | Generic error banner. |
| `IDEMPOTENCY_KEY_MISSING` | 400 | A state-changing call that requires `Idempotency-Key` had none. | No; this is a client bug. | Generic error banner. |
| `INVALID_TRANSITION` | 409 | An admin phase action is not allowed from the current phase. | No. | Admin console: action disabled or error toast. |
| `NOT_FOUND` | 404 | Unknown drop or resource. | No. | "Drop not found" screen. |
| `VALIDATION_ERROR` | 400 | The request body or a parameter failed validation. | No; fix the request. | Inline form error or generic banner. |
| `SERVICE_UNAVAILABLE` | 503 | Postgres is slow or down, or a lock or pool timeout occurred. Nothing was written. | Yes, with the same idempotency key, honouring `Retry-After`, with exponential backoff and jitter. | "Reconnecting... your place is safe" banner. |
| `INTERNAL` | 500 | Unexpected server error. The message carries a request id. | Yes, a bounded number of times; then show the error. | Generic error banner with the request id. |
