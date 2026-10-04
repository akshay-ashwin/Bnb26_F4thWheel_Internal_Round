"""Nothing sensitive reaches a log record, checked on the RAW records.

The app's scrubbing filter is a backstop. This test puts a capture handler in FRONT of it (the
filter edits records in place), so a pass means the auth code itself never logs these values.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Iterator
from uuid import UUID

import httpx
import pytest

from app.config import Settings
from app.identity import session as sess
from app.identity.phone import identify_phone
from tests.auth_helpers import PHONE, add_whoami, request_otp, verify

pytestmark = pytest.mark.usefixtures("clean_db", "clean_redis")

_STANDARD = set(logging.LogRecord("", 0, "", 0, "", None, None).__dict__) | {"message", "asctime"}


class _Capture(logging.Handler):
    def __init__(self) -> None:
        super().__init__(level=logging.DEBUG)
        self.records: list[logging.LogRecord] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.records.append(record)

    def text(self) -> str:
        parts: list[str] = []
        for r in self.records:
            parts.append(r.getMessage())
            parts.extend(f"{k}={v!r}" for k, v in r.__dict__.items() if k not in _STANDARD)
            if r.exc_info:
                parts.append(logging.Formatter().formatException(r.exc_info))
        return "\n".join(parts)


@pytest.fixture
def capture() -> Iterator[_Capture]:
    root = logging.getLogger()
    handler = _Capture()
    old_level = root.level
    root.setLevel(logging.DEBUG)
    root.handlers.insert(0, handler)  # first, so it sees the record before the scrubbing filter
    yield handler
    root.handlers.remove(handler)
    root.setLevel(old_level)


def _bare(value: str) -> re.Pattern[str]:
    return re.compile(r"(?<![0-9A-Za-z])" + re.escape(value) + r"(?![0-9A-Za-z])")


async def test_no_phone_code_token_session_id_or_secret_is_ever_logged(
    client: httpx.AsyncClient, capture: _Capture, base_settings: Settings
) -> None:
    add_whoami(client.app)  # type: ignore[attr-defined]
    secrets_seen: list[str] = []  # strings that must be absent, as substrings
    codes: list[str] = []  # 6-digit values that must be absent as whole tokens

    # Success path, with the phone written the way a person might type it.
    req = (await request_otp(client, phone=PHONE)).json()
    codes.append(req["dev_otp"])
    codes.extend(f"{(int(req['dev_otp']) + n) % 10**6:06d}" for n in (1, 2))
    for wrong in codes[1:]:
        assert (await verify(client, req["request_id"], wrong)).status_code == 401
    ok = await verify(client, req["request_id"], req["dev_otp"])
    assert ok.status_code == 200
    token = ok.json()["session_token"]
    sid = str(UUID(token.partition(".")[0]))
    secrets_seen += [token, token.partition(".")[2], sid]

    # Failure and abuse paths: bad phone, bad device, replayed and unknown codes, bad tokens.
    assert (await request_otp(client, phone="+91 12345 678")).status_code == 400
    assert (await request_otp(client, device_id="bad device id")).status_code == 400
    assert (await verify(client, req["request_id"], req["dev_otp"])).status_code == 410
    assert (await verify(client, "unknown-request-id", "123456")).status_code == 410
    client.cookies.clear()
    for tok in (token, token[:-3] + "xyz", "garbage", ""):
        await client.get("/api/_whoami", headers={"Authorization": f"Bearer {tok}"})
    await sess.revoke_session(client.app.state.pool, client.app.state.cache, UUID(sid))  # type: ignore[attr-defined]
    await client.get("/api/_whoami", headers={"Authorization": f"Bearer {token}"})

    # Exhausted-attempts path.
    second = (await request_otp(client, phone="+91 98765 43299")).json()
    for n in range(1, 7):
        await verify(client, second["request_id"], f"{(int(second['dev_otp']) + n) % 10**6:06d}")
        codes.append(f"{(int(second['dev_otp']) + n) % 10**6:06d}")
    codes.append(second["dev_otp"])

    secrets_seen += [
        # the phone in E.164, national, digits-only and as typed, plus the second number
        "+919876543210",
        "919876543210",
        "9876543210",
        "09876543210",
        "98765 43210",
        PHONE,
        "9876543299",
        "919876543299",
        base_settings.phone_pepper.get_secret_value(),
        base_settings.session_secret.get_secret_value(),
        base_settings.token_signing_key.get_secret_value(),
        base_settings.admin_key.get_secret_value(),
    ]
    text = capture.text()

    # Prove the capture works and the intended breadcrumbs are there.
    assert "otp sent" in text and "phone_ref=" in text
    assert "identity verified" in text and "otp rejected" in text
    assert "otp attempts exhausted" in text

    for secret in secrets_seen:
        assert secret not in text, f"leaked into logs: {secret[:6]}..."
    for code in codes:
        assert not _bare(code).search(text), "an OTP value reached the logs"
    # The full phone hash is not logged either: only the 8-character reference.
    for number in (PHONE, "+91 98765 43299"):
        full = identify_phone(number, base_settings.phone_pepper, 6).phone_hash
        assert full not in text and full[:8] in text
    for record in capture.records:
        for value in record.__dict__.values():
            if isinstance(value, str) and re.fullmatch(r"[0-9a-f]{64}", value):
                pytest.fail("a 64-hex value (a full hash) was logged")


async def test_failed_sms_and_redis_trouble_log_without_values(
    client_redis_down: httpx.AsyncClient, capture: _Capture
) -> None:
    await request_otp(client_redis_down)
    await verify(client_redis_down, "some-request-id", "654321")
    text = capture.text()
    assert "9876543210" not in text and "654321" not in text
