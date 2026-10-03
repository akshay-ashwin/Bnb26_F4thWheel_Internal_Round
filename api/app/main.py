"""Fair Drop API application factory."""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.cache import Cache
from app.config import Settings
from app.db import create_pool
from app.errors import install_error_handlers
from app.jobs import Jobs
from app.metrics import Metrics
from app.middleware import GatewayMiddleware
from app.routers import admin, ops, public, sim
from app.services.auth import SimulatedSmsProvider


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings.from_env()
    logging.basicConfig(level=settings.log_level)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        app.state.pool = await create_pool(settings)
        app.state.cache = Cache(settings)
        app.state.metrics.set_cache(app.state.cache)
        app.state.metrics.start()
        jobs = Jobs(app.state.pool, app.state.cache, settings, app.state.metrics)
        if settings.run_jobs:
            jobs.start()
        try:
            yield
        finally:
            await jobs.stop()
            await app.state.metrics.stop()
            await app.state.cache.close()
            await app.state.pool.close()

    app = FastAPI(
        title="Fair Drop API",
        lifespan=lifespan,
        docs_url="/api/docs",
        openapi_url="/api/openapi.json",
    )
    app.state.settings = settings
    app.state.metrics = Metrics(None)
    app.state.sms = SimulatedSmsProvider()
    app.state.tasks = set()
    install_error_handlers(app)
    app.include_router(ops.router)
    app.include_router(public.router)
    app.include_router(admin.router)
    if settings.sim_mode:  # contract: 404 NOT_FOUND when SIM_MODE is off
        app.include_router(sim.router)
    app.add_middleware(GatewayMiddleware, metrics=app.state.metrics)
    return app


app = create_app()
