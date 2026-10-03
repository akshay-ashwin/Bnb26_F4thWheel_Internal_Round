"""Abuse protection (Saanvi's lane): L1–L3 rate limiting, L6 OTP controls, L7 risk scoring.

Wiring for the backend:

    limiter = Limiter(redis, session_secret=SESSION_SECRET.encode(), user_resolver=...)
    store = ConfigStore(redis, persist=<writes app_settings['abuse_config']>)
    counters = install_abuse(app, limiter, store)
    app.include_router(config_router(store, require_admin))

Hooks for the OTP, entry and draw code: `risk.otp_guard`, `risk.record_verify`,
`risk.score_entry`, `risk.rescore`.
"""

from app.abuse.config import AbuseConfig, ConfigError, ConfigStore
from app.abuse.limiter import Decision, Limiter
from app.abuse.middleware import AbuseMiddleware, install_abuse
from app.abuse.router import config_router

__all__ = [
    "AbuseConfig",
    "AbuseMiddleware",
    "ConfigError",
    "ConfigStore",
    "Decision",
    "Limiter",
    "config_router",
    "install_abuse",
]
