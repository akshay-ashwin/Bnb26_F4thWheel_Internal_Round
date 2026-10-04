"""SMS delivery. One interface, one implementation (a real provider is out of scope)."""

import logging
from typing import Protocol

log = logging.getLogger("fairdrop.sms")


class SmsProvider(Protocol):
    async def send_otp(self, *, phone_e164: str, phone_hash: str, otp: str) -> None: ...


class SimulatedSmsProvider:
    """Sends nothing. Logs only a short prefix of the phone hash; never the number or the code
    (not even at DEBUG: SIM_MODE clients get the code as `dev_otp` in the response instead)."""

    async def send_otp(self, *, phone_e164: str, phone_hash: str, otp: str) -> None:
        log.info("otp sent", extra={"phone_ref": phone_hash[:8]})
