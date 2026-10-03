"""Admission tokens: minted in `/me`, verified in `/claim`.

A compact HS256 JWT with claims {drop_id, entry_id, sid_hash, jti, iat, exp, run}. `sid_hash` ties
it to the session that fetched it; `run` stops a token from before a reset from working after one;
`exp` never passes the offer expiry. A fresh token (new jti) is minted on every `/me` call: tokens
are cheap, and "one entry holds at most one seat" is enforced by the entry row lock plus database
constraints, so several valid tokens for one entry are harmless.
"""

from __future__ import annotations

import secrets
import time
from datetime import datetime
from typing import Any

from app.config import Settings
from app.deps import Session
from app.security import encode_token


def mint_for_me(settings: Settings, row: Any, session: Session) -> str | None:
    """The token to show in `/me` for this entry, or None when no claim is possible right now.

    FIFO: a REGISTERED entry while the drop is OPEN. Fair: an OFFERED entry whose offer has not
    expired (STEP_UP_REQUIRED gets no token until step-up passes).
    """
    status = row["status"]
    now = time.time()
    exp = now + settings.token_ttl_s
    if row["mode"] == "fifo":
        if status != "REGISTERED" or row["phase"] != "OPEN":
            return None
    else:
        if status != "OFFERED" or row["offer_expires_at"] is None:
            return None
        offer: datetime = row["offer_expires_at"]
        if offer.timestamp() <= now:
            return None
        exp = min(exp, offer.timestamp())
    return encode_token(
        settings.token_signing_key,
        {
            "drop_id": str(row["drop_id"]),
            "entry_id": str(row["entry_id"]),
            "sid_hash": session.sid_hash,
            "jti": secrets.token_hex(8),
            "iat": int(now),
            "exp": int(exp),
            "run": row["run_no"],
        },
    )
