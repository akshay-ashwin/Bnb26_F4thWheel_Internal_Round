"""Typed settings read from the environment (the same variables as `.env.example`)."""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass


def _flag(value: str | None, default: bool) -> bool:
    if value is None or value == "":
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class Settings:
    app_env: str
    sim_mode: bool
    log_level: str
    database_url: str
    db_pool_min: int
    db_pool_max: int
    redis_url: str
    redis_timeout_ms: int
    phone_pepper: str
    session_secret: str
    token_signing_key: str
    admin_key: str
    sim_telemetry_key: str
    cookie_secure: bool
    cookie_domain: str | None
    trusted_proxy_cidrs: tuple[str, ...]
    # Behaviour knobs. Defaults are the production values; tests shrink the time-based ones.
    session_ttl_s: int = 24 * 3600
    otp_ttl_s: int = 300
    otp_dedupe_s: int = 30
    otp_max_attempts: int = 5
    token_ttl_s: int = 60
    step_up_threshold: int = 60
    step_up_max_attempts: int = 3
    draw_grace_s: float = 3.0
    run_jobs: bool = True
    job_interval_s: float = 1.0
    claim_lock_timeout_ms: int = 1000
    claim_statement_timeout_ms: int = 2000
    pool_acquire_timeout_s: float = 3.0

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> Settings:
        e = os.environ if env is None else env

        def req(name: str) -> str:
            value = e.get(name, "")
            if not value:
                raise ValueError(f"missing required environment variable {name}")
            return value

        settings = cls(
            app_env=e.get("APP_ENV", "dev"),
            sim_mode=_flag(e.get("SIM_MODE"), False),
            log_level=e.get("LOG_LEVEL", "INFO").upper(),
            database_url=req("DATABASE_URL"),
            db_pool_min=int(e.get("DB_POOL_MIN", "2")),
            db_pool_max=int(e.get("DB_POOL_MAX", "30")),
            redis_url=req("REDIS_URL"),
            redis_timeout_ms=int(e.get("REDIS_TIMEOUT_MS", "200")),
            phone_pepper=req("PHONE_PEPPER"),
            session_secret=req("SESSION_SECRET"),
            token_signing_key=req("TOKEN_SIGNING_KEY"),
            admin_key=req("ADMIN_KEY"),
            sim_telemetry_key=e.get("SIM_TELEMETRY_KEY", ""),
            cookie_secure=_flag(e.get("COOKIE_SECURE"), False),
            cookie_domain=e.get("COOKIE_DOMAIN") or None,
            trusted_proxy_cidrs=tuple(
                c.strip() for c in e.get("TRUSTED_PROXY_CIDRS", "").split(",") if c.strip()
            ),
            step_up_threshold=int(e.get("STEP_UP_THRESHOLD", "60")),
            draw_grace_s=float(e.get("DRAW_GRACE_S", "3")),
            run_jobs=_flag(e.get("RUN_JOBS"), True),
            job_interval_s=float(e.get("JOB_INTERVAL_S", "1")),
            token_ttl_s=int(e.get("TOKEN_TTL_S", "60")),
            otp_dedupe_s=int(e.get("OTP_DEDUPE_S", "30")),
        )
        settings.validate()
        return settings

    def validate(self) -> None:
        for name in ("phone_pepper", "session_secret", "token_signing_key", "admin_key"):
            if len(getattr(self, name).encode()) < 32:
                raise ValueError(f"{name} must be at least 32 bytes")
        if self.app_env == "prod" and self.sim_mode:
            raise ValueError("SIM_MODE=true is not allowed when APP_ENV=prod")
        if self.db_pool_min > self.db_pool_max:
            raise ValueError("DB_POOL_MIN must not exceed DB_POOL_MAX")
