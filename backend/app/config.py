from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql+asyncpg://fairdrop:fairdrop@localhost:5432/fairdrop"
    redis_url: str = "redis://localhost:6379/0"
    sim_mode: bool = True
    secret_key: str = "change-me-dev-only"
    admin_api_key: str = "admin-dev-key"
    session_ttl_minutes: int = 720
    otp_ttl_seconds: int = 300
    otp_max_attempts: int = 5
    admission_token_ttl_seconds: int = 300


@lru_cache
def get_settings() -> Settings:
    return Settings()
