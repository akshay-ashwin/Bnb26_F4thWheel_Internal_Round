"""Typed settings loaded from the environment, validated once at startup."""

from functools import lru_cache
from typing import Literal, Self
from urllib.parse import urlsplit

from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

MIN_SECRET_BYTES = 32
PG_RESERVED_CONNECTIONS = 3  # superuser_reserved_connections default

_REQUIRED_SECRETS = ("phone_pepper", "session_secret", "token_signing_key", "admin_key")


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=None, extra="ignore", case_sensitive=False)

    app_env: Literal["dev", "demo", "prod"] = "dev"
    sim_mode: bool = False
    log_level: str = "INFO"
    # Fraction of 2xx requests that get an access-log line. Errors and slow requests always do.
    log_sample_rate: float = Field(default=0.01, ge=0, le=1)
    log_slow_ms: int = 250

    database_url: SecretStr
    db_pool_min: int = Field(default=2, ge=1)
    db_pool_max: int = Field(default=30, ge=1)
    db_max_connections: int = 200  # must match infra/postgres/postgresql.conf
    db_headroom: int = 10  # migrations, admin psql, sweeper
    db_acquire_timeout_ms: int = 1000
    db_connect_timeout_ms: int = 1000
    db_statement_timeout_ms: int = 5000
    db_idle_in_tx_timeout_ms: int = 10000

    redis_url: str = "redis://redis:6379/0"
    redis_timeout_ms: int = Field(default=50, ge=1)
    redis_breaker_failures: int = 3
    redis_breaker_cooloff_ms: int = 2000

    uvicorn_workers: int = Field(default=4, ge=1)
    api_reload: bool = False

    phone_pepper: SecretStr = SecretStr("")
    session_secret: SecretStr = SecretStr("")
    token_signing_key: SecretStr = SecretStr("")
    token_key_id: str = "k1"  # noqa: S105 (key identifier, not a secret)
    admin_key: SecretStr = SecretStr("")
    sim_telemetry_key: SecretStr = SecretStr("")

    cookie_secure: bool = False
    cookie_domain: str = ""

    # Behaviour knobs of the allocation backend (defaults are the production values).
    trusted_proxy_cidrs: str = ""  # comma-separated; proxies whose X-Forwarded-For we trust
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

    @property
    def pool_acquire_timeout_s(self) -> float:
        return self.db_acquire_timeout_ms / 1000

    @property
    def trusted_cidrs(self) -> tuple[str, ...]:
        return tuple(c.strip() for c in self.trusted_proxy_cidrs.split(",") if c.strip())

    @property
    def effective_workers(self) -> int:
        return 1 if self.api_reload else self.uvicorn_workers

    @property
    def db_connection_budget(self) -> int:
        return self.effective_workers * self.db_pool_max + self.db_headroom

    @model_validator(mode="after")
    def _validate(self) -> Self:
        if self.app_env == "prod" and self.sim_mode:
            raise ValueError("refusing to start: APP_ENV=prod with SIM_MODE=true")
        names = list(_REQUIRED_SECRETS) + (["sim_telemetry_key"] if self.sim_mode else [])
        for name in names:
            if len(getattr(self, name).get_secret_value().encode()) < MIN_SECRET_BYTES:
                raise ValueError(
                    f"{name.upper()} must be set and at least {MIN_SECRET_BYTES} bytes"
                )
        if self.db_pool_min > self.db_pool_max:
            raise ValueError("DB_POOL_MIN must not exceed DB_POOL_MAX")
        limit = self.db_max_connections - PG_RESERVED_CONNECTIONS
        if self.db_connection_budget >= limit:
            raise ValueError(
                f"pool math: {self.effective_workers} workers x DB_POOL_MAX {self.db_pool_max}"
                f" + headroom {self.db_headroom} = {self.db_connection_budget}"
                f" must be below {limit} (max_connections {self.db_max_connections} minus"
                f" {PG_RESERVED_CONNECTIONS} reserved)"
            )
        return self

    def secret_values(self) -> list[str]:
        """Every secret value, for exact-match log scrubbing."""
        values = [
            self.database_url.get_secret_value(),
            self.phone_pepper.get_secret_value(),
            self.session_secret.get_secret_value(),
            self.token_signing_key.get_secret_value(),
            self.admin_key.get_secret_value(),
            self.sim_telemetry_key.get_secret_value(),
        ]
        password = urlsplit(values[0]).password
        if password:
            values.append(password)
        return [v for v in values if v]

    def public_summary(self) -> dict[str, object]:
        """Effective configuration for the one boot log line. Never contains secrets."""
        return {
            "app_env": self.app_env,
            "sim_mode": self.sim_mode,
            "workers": self.effective_workers,
            "db_pool": [self.db_pool_min, self.db_pool_max],
            "db_connection_budget": self.db_connection_budget,
            "redis_timeout_ms": self.redis_timeout_ms,
            "log_sample_rate": self.log_sample_rate,
            "token_key_id": self.token_key_id,
        }


@lru_cache
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]  # fields come from the environment
