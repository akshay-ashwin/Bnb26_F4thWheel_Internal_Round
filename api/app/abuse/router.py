"""`PUT /api/admin/abuse/config` for the DEV STUB only, in the frozen contract shape.

The real app already defines this route (Plan 03 admin router); there it should call
`ConfigStore.update(body, contract=True)` and return `contract_dict()` plus `server_time`.
Do not mount this router next to it."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse

from app.abuse.config import ConfigError, ConfigStore
from app.clock import server_time


def config_router(store: ConfigStore, require_admin: Callable[..., Any]) -> APIRouter:
    router = APIRouter(prefix="/api/admin/abuse", dependencies=[Depends(require_admin)])

    @router.get("/config")
    async def get_config() -> dict[str, Any]:
        return {**store.current().contract_dict(), "server_time": server_time()}

    @router.put("/config", response_model=None)
    async def put_config(request: Request) -> dict[str, Any] | JSONResponse:
        try:
            payload = await request.json()
            cfg = await store.update(payload, contract=True)
        except (ConfigError, ValueError) as exc:
            return JSONResponse(
                status_code=400,
                content={
                    "error": {"code": "VALIDATION_ERROR", "message": str(exc)},
                    "server_time": server_time(),
                },
            )
        return {**cfg.contract_dict(), "server_time": server_time()}

    return router
