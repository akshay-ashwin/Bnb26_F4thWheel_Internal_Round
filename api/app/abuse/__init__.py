"""Abuse-layer integration boundary (Saanvi's lane).

Everything the backend needs from the abuse system goes through the functions exported here, so
the rest of the code never changes when the real limiter and risk rules replace these defaults:

* `check(request) -> Decision`      L1-L3 request limiter; called by the gateway middleware before
                                    routing. Must never touch Postgres. Failures fail open.
* `otp_request_guard(...)`          L6 hook in POST /auth/otp/request (raise AppError OTP_THROTTLED)
* `on_identity_verified(...)`       L7 hook after a successful OTP verify
* `score_entry(...)`                L7 hook before an entry is stored -> (risk_score, risk_flags)
* `rescore_eligible(conn, drop_id)` L7 re-score of every eligible entry inside the draw transaction

Defaults allow everything and score 0. The backend never reads simulator ground truth (invariant 6).
"""

from app.abuse.limiter import Decision, check
from app.abuse.risk import on_identity_verified, otp_request_guard, rescore_eligible, score_entry

__all__ = [
    "Decision",
    "check",
    "on_identity_verified",
    "otp_request_guard",
    "rescore_eligible",
    "score_entry",
]
