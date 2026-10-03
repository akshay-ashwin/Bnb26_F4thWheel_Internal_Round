"""App factory. The OpenAPI spec this app generates IS the frozen contract (docs/contract)."""

import logging
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI
from fastapi.openapi.utils import get_openapi

from app import db
from app.cache import Cache
from app.config import Settings, get_settings
from app.errors import install_handlers
from app.middleware.asgi import AbuseLayersMiddleware, RequestContextMiddleware
from app.observability.logging import configure_logging
from app.routers import admin, ops, public, sim

log = logging.getLogger("fairdrop")

_DROPPED_SCHEMAS = ("HTTPValidationError", "ValidationError")


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        configure_logging(settings.log_level, settings.secret_values(), os.getpid())
        log.info("starting", extra={"config": settings.public_summary()})
        app.state.pool = await db.create_pool(settings)
        app.state.cache = Cache(settings)
        try:
            yield
        finally:
            await app.state.cache.close()
            await app.state.pool.close()

    app = FastAPI(
        title="Fair Drop API",
        version="1.0.0",
        docs_url="/api/docs",
        openapi_url="/api/openapi.json",
        redoc_url=None,
        lifespan=lifespan,
    )
    install_handlers(app)

    # add_middleware: the last one added is the outermost.
    app.add_middleware(AbuseLayersMiddleware)
    app.add_middleware(
        RequestContextMiddleware,
        sample_rate=settings.log_sample_rate,
        slow_ms=settings.log_slow_ms,
    )

    app.include_router(ops.router, prefix="/api")
    app.include_router(public.router, prefix="/api")
    app.include_router(admin.router, prefix="/api")
    if settings.sim_mode:
        app.include_router(sim.router, prefix="/api")

    app.openapi = lambda: _openapi(app)  # type: ignore[method-assign]
    return app


def _openapi(app: FastAPI) -> dict[str, Any]:
    """FastAPI's default 422 HTTPValidationError is not in our contract: validation is 400."""
    if app.openapi_schema:
        return app.openapi_schema
    schema = get_openapi(
        title=app.title,
        version=app.version,
        description="Fair Drop public, admin and simulator API. Errors: one envelope.",
        routes=app.routes,
    )
    for path in schema["paths"].values():
        for op in path.values():
            if _is_default_422(op["responses"].get("422")):
                del op["responses"]["422"]
    for name in _DROPPED_SCHEMAS:
        schema.get("components", {}).get("schemas", {}).pop(name, None)
    app.openapi_schema = schema
    return schema


def _is_default_422(response: dict[str, Any] | None) -> bool:
    if not response:
        return False
    ref = str(response.get("content", {}).get("application/json", {}).get("schema", {}))
    return "HTTPValidationError" in ref


app = create_app()
