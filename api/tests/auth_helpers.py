"""Helpers for the identity tests: log in through the real endpoints, and a whoami route."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import httpx
from fastapi import APIRouter, FastAPI

from app.deps import CurrentSession

PHONE = "+91 98765 43210"
PHONE_E164 = "+919876543210"
DEVICE = "device-aaaa-0001"
OTHER_DEVICE = "device-bbbb-0002"


@dataclass
class Login:
    token: str
    user_public_id: str
    cookie: str | None


def add_whoami(app: FastAPI) -> None:
    """A test-only protected route: proves the session dependency end to end."""
    router = APIRouter()

    @router.get("/api/_whoami")
    async def whoami(session: CurrentSession) -> dict[str, str]:
        return {"user_public_id": session.user_public_id, "sid_hash": session.sid_hash}

    app.include_router(router)


async def request_otp(
    client: httpx.AsyncClient, phone: str = PHONE, device_id: str = DEVICE, **headers: str
) -> httpx.Response:
    return await client.post(
        "/api/auth/otp/request", json={"phone": phone, "device_id": device_id}, headers=headers
    )


async def verify(
    client: httpx.AsyncClient,
    request_id: str,
    otp: str,
    device_id: str = DEVICE,
    **headers: str,
) -> httpx.Response:
    return await client.post(
        "/api/auth/otp/verify",
        json={"request_id": request_id, "otp": otp, "device_id": device_id},
        headers=headers,
    )


async def login(
    client: httpx.AsyncClient, phone: str = PHONE, device_id: str = DEVICE, **headers: str
) -> Login:
    """Request a code (SIM_MODE gives dev_otp) and verify it. Raises if anything fails."""
    first = await request_otp(client, phone, device_id, **headers)
    first.raise_for_status()
    body: dict[str, Any] = first.json()
    done = await verify(client, body["request_id"], body["dev_otp"], device_id, **headers)
    done.raise_for_status()
    out: dict[str, Any] = done.json()
    return Login(out["session_token"], out["user_public_id"], done.cookies.get("fd_session"))
