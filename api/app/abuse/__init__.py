"""Abuse protection (Saanvi's lane): L1–L3 rate limiting, L6 OTP controls, L7 risk scoring.

Integration boundary for the backend (implemented in `app.abuse.hooks`):

* `check(request) -> Decision`      L1-L3 request limiter; called by the gateway middleware before
                                    routing. Never touches Postgres. Failures fail open.
* `otp_request_guard(...)`          L6 hook in POST /auth/otp/request (raises OtpThrottled)
* `on_identity_verified(...)`       L7 hook after a successful OTP verify
* `score_entry(...)`                L7 hook before an entry is stored -> (risk_score, risk_flags)
* `rescore_eligible(conn, drop_id)` L7 re-score of every eligible entry inside the draw transaction

The building blocks (`Limiter`, `ConfigStore`, `AbuseMiddleware`, `risk.*`) are also used
directly by the dev stub (`app.abuse.devstub`). The backend never reads simulator ground truth
(invariant 6).
"""

from app.abuse.config import AbuseConfig, ConfigError, ConfigStore
from app.abuse.hooks import (
    check,
    on_identity_verified,
    otp_request_guard,
    rescore_eligible,
    score_entry,
)
from app.abuse.limiter import Decision, Limiter, is_skipped
from app.abuse.middleware import AbuseMiddleware, install_abuse
from app.abuse.router import config_router

__all__ = [
    "AbuseConfig",
    "AbuseMiddleware",
    "ConfigError",
    "ConfigStore",
    "Decision",
    "Limiter",
    "check",
    "config_router",
    "install_abuse",
    "is_skipped",
    "on_identity_verified",
    "otp_request_guard",
    "rescore_eligible",
    "score_entry",
]
