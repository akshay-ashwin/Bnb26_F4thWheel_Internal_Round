"""`PUT /api/admin/abuse/config` (contract) plus a `GET` the dashboard can read."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse

from app.abuse.config import ConfigError, ConfigStore


def config_router(store: ConfigStore, require_admin: Callable[..., Any]) -> APIRouter:
    router = APIRouter(prefix="/api/admin/abuse", dependencies=[Depends(require_admin)])

    @router.get("/config")
    async def get_config() -> dict[str, Any]:
        return {**store.current().to_dict(), "server_time": datetime.now(UTC).isoformat()}

    @router.put("/config", response_model=None)
    async def put_config(request: Request) -> dict[str, Any] | JSONResponse:
        try:
            payload = await request.json()
            cfg = await store.update(payload)
        except (ConfigError, ValueError) as exc:
            return JSONResponse(
                status_code=400,
                content={
                    "error": {"code": "VALIDATION_ERROR", "message": str(exc)},
                    "server_time": datetime.now(UTC).isoformat(),
                },
            )
        return {**cfg.to_dict(), "server_time": datetime.now(UTC).isoformat()}

    return router
