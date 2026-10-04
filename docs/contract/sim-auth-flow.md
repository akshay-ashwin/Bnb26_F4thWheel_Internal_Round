# Simulator auth flow (example)

How a simulated client gets a session. It uses the same two public endpoints as the browser; only the transport differs (bearer token instead of the cookie). Added in Plan 04. This file explains the frozen contract, it does not change it.

## What the simulator needs

- The api must run with `SIM_MODE=true`. Then `POST /auth/otp/request` returns `dev_otp`, and `X-Sim-Client-IP` is honoured (a different apparent client address per simulated client). With `SIM_MODE=false` both are ignored and the simulator cannot log in at all.
- **One identity = one phone number.** Generate Indian mobile numbers: `+91` followed by ten digits whose first digit is 6-9. Anything else is `400 INVALID_PHONE`. Any spelling of a number is the same identity: `+919876543210`, `09876543210` and `98765 43210` give one user.
- `device_id`: 8 to 128 characters from letters, digits and `. _ : -`. Anything else is `400 VALIDATION_ERROR` on `body.device_id`. One device id per simulated client; the same phone from a second device id gets a second session for the same user.

## The flow

```
1. POST /api/auth/otp/request   {"phone": "+919812300001", "device_id": "sim-client-000001"}
   headers: X-Sim-Client-IP: 203.0.113.17
   200 {"request_id": "_gLerhN9XAT495s10ZQ_Ag", "expires_in_s": 300, "dev_otp": "880442", "server_time": "..."}

2. POST /api/auth/otp/verify    {"request_id": "_gLerhN9XAT495s10ZQ_Ag", "otp": "880442", "device_id": "sim-client-000001"}
   headers: X-Sim-Client-IP: 203.0.113.17
   200 {"session_token": "<uuid>.<signature>", "user_public_id": "rzhaaj556sed53sr4g7g73t2ty", "server_time": "..."}

3. Every later call:  Authorization: Bearer <session_token>
```

Python sketch (httpx):

```python
async def login(client: httpx.AsyncClient, phone: str, device_id: str, ip: str) -> tuple[str, str]:
    headers = {"X-Sim-Client-IP": ip}
    r = await client.post("/api/auth/otp/request", json={"phone": phone, "device_id": device_id}, headers=headers)
    r.raise_for_status()
    body = r.json()
    r = await client.post(
        "/api/auth/otp/verify",
        json={"request_id": body["request_id"], "otp": body["dev_otp"], "device_id": device_id},
        headers=headers,
    )
    r.raise_for_status()
    out = r.json()
    return out["session_token"], out["user_public_id"]
```

## Rules the simulator must respect

- **Verify must use the same `device_id` as the request.** A code used from another device id is `401 OTP_INVALID` and burns one of the five guesses.
- **A code works once.** After a successful verify the same `request_id` is `410 OTP_EXPIRED`.
- **Five wrong guesses per request.** After that the request is deleted and you must request a new code.
- Asking again for the same phone within 30 s returns the **same** `request_id` (and the same `dev_otp` in SIM_MODE). Verifying again later from the same device returns the **same** `session_token`.
- Sessions last 24 hours (`SESSION_TTL_S`), for bearer tokens as well as the cookie. After that, every call is `401 UNAUTHENTICATED`; log in again.
- `429` and `503` carry `Retry-After`; wait and retry. `503` on the auth endpoints means Redis is unavailable (codes live only in Redis). `400`, `401`, `410` are not retried automatically.
- Do not send ground-truth labels (`sim:*`) in any auth call; the backend never reads them (invariant 6).
