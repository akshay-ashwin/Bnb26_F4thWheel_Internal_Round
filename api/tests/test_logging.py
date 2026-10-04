import json
import logging

import httpx
import pytest

from app.observability.logging import Scrubber

JWT = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.abcdefghijk"


@pytest.mark.parametrize(
    "text",
    [
        "call +14155550123 now",
        "phone 415-555-0123 here",
        "raw 4155550123 digits",
        f"token {JWT}",
        "Authorization: Bearer abcdef1234567890",
        "cookie fd_session=abc123def456; path=/",
        'body {"otp": "123456"}',
        "otp=123456",
    ],
)
def test_pattern_scrubbing(text: str) -> None:
    out = Scrubber().scrub_text(text)
    assert "[REDACTED]" in out
    for leaked in ("4155550123", "555-0123", "123456", "abcdef1234567890", JWT, "abc123def456"):
        assert leaked not in out


def test_known_secret_values_and_sensitive_keys() -> None:
    s = Scrubber()
    s.register("super-secret-seed-value-0123456789")
    assert "super-secret" not in s.scrub_text("seed was super-secret-seed-value-0123456789!")
    assert s.scrub_value({"phone": "x", "nested": {"otp": "1"}, "ok": 5}) == {
        "phone": "[REDACTED]",
        "nested": {"otp": "[REDACTED]"},
        "ok": 5,
    }


def test_innocent_text_is_untouched() -> None:
    s = Scrubber()
    text = "route=/drops/{drop_id}/me status=200 at 2026-10-04T10:15:30.123Z id 0b6c5d34-8c40-4d6d"
    assert s.scrub_text(text) == text
    commitment = "a" * 64  # public seed commitments look like this and must stay visible
    assert s.scrub_text(commitment) == commitment


async def test_end_to_end_logs_and_responses_never_contain_secrets(
    capsys: pytest.CaptureFixture[str], client: httpx.AsyncClient
) -> None:
    phone = "+14155550123"
    response = await client.post(
        "/api/auth/otp/request", json={"phone": phone, "device_id": "d", "bogus": 1}
    )
    log = logging.getLogger("fairdrop.test")
    log.warning("lookup for %s failed", phone, extra={"otp": "123456", "session_token": "tok"})
    try:
        raise RuntimeError(f"db said phone={phone} otp=654321")
    except RuntimeError:
        log.exception("boom")
    err = capsys.readouterr().err
    assert phone not in err and phone not in response.text
    assert "123456" not in err and "654321" not in err and '"tok"' not in err
    lines = [json.loads(line) for line in err.splitlines() if line.startswith("{")]
    request_line = next(r for r in lines if r.get("msg") == "request")
    assert request_line["status"] == 400
    assert request_line["route"] == "/api/auth/otp/request"
    assert {"request_id", "latency_ms", "pid"} <= request_line.keys()
