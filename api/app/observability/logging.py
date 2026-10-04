"""Structured JSON logging with a scrubbing filter (defence in depth).

Primary rule: code never logs phones, OTPs, tokens or seeds. The filter is the backstop. It
redacts (1) every known secret value by exact match, (2) values of sensitive field names, and
(3) strings with a recognisable secret shape (JWT, Bearer, session cookie, phone, otp=NNNNNN).
It deliberately does NOT redact arbitrary 64-hex strings: the public seed commitment and
entry_set_hash look the same, and a live seed is registered by value instead.
"""

import json
import logging
import re
import sys
import threading
from typing import Any

REDACTED = "[REDACTED]"

SENSITIVE_KEYS = frozenset(
    {
        "phone",
        "otp",
        "dev_otp",
        "session_token",
        "admission_token",
        "authorization",
        "cookie",
        "set-cookie",
        "x-admin-key",
        "password",
        "seed",
        "token",
        "secret",
    }
)

_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"eyJ[A-Za-z0-9_-]{6,}\.[A-Za-z0-9_-]{6,}\.[A-Za-z0-9_-]*"),  # JWT
    re.compile(r"(?i)bearer\s+[A-Za-z0-9._~+/=-]{8,}"),
    re.compile(r"(?i)fd_session=[^;\s\"']+"),
    re.compile(r"(?i)(otp|dev_otp|code)[\"']?\s*[:=]\s*[\"']?\d{4,8}"),
    re.compile(r"(?<![\w.])\+\d[\d\s().-]{7,15}\d(?![\w.])"),  # +E.164 with separators
    re.compile(r"(?<![\w.-])\d{10,15}(?![\w.-])"),  # bare long digit run
    re.compile(r"(?<![\w.])\(?\d{3}\)?[\s.-]\d{3}[\s.-]\d{4}(?![\w.])"),  # 555-123-4567
)

_RESERVED = frozenset(logging.LogRecord("", 0, "", 0, "", None, None).__dict__) | {
    "message",
    "asctime",
    "taskName",
}


class Scrubber:
    def __init__(self) -> None:
        self._secrets: set[str] = set()
        self._lock = threading.Lock()

    def register(self, *values: str) -> None:
        """Add exact values that must never appear in a log line (min length guards noise)."""
        with self._lock:
            self._secrets.update(v for v in values if len(v) >= 8)

    def scrub_text(self, text: str) -> str:
        for secret in self._secrets:
            if secret in text:
                text = text.replace(secret, REDACTED)
        for pattern in _PATTERNS:
            text = pattern.sub(REDACTED, text)
        return text

    def scrub_value(self, value: Any, key: str | None = None) -> Any:
        if key is not None and key.lower() in SENSITIVE_KEYS:
            return REDACTED
        if isinstance(value, str):
            return self.scrub_text(value)
        if isinstance(value, dict):
            return {str(k): self.scrub_value(v, str(k)) for k, v in value.items()}
        if isinstance(value, list | tuple | set):
            return [self.scrub_value(v) for v in value]
        if isinstance(value, int | float | bool) or value is None:
            return value
        return self.scrub_text(str(value))


scrubber = Scrubber()


class ScrubFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.msg = scrubber.scrub_text(record.getMessage())
        record.args = None
        if record.exc_info:
            trace = logging.Formatter().formatException(record.exc_info)
            record.exc_text = scrubber.scrub_text(trace)
            record.exc_info = None
        elif record.exc_text:
            record.exc_text = scrubber.scrub_text(record.exc_text)
        for key in list(record.__dict__):
            if key not in _RESERVED:
                record.__dict__[key] = scrubber.scrub_value(record.__dict__[key], key)
        return True


class JsonFormatter(logging.Formatter):
    def __init__(self, pid: int) -> None:
        super().__init__()
        self._pid = pid

    def format(self, record: logging.LogRecord) -> str:
        out: dict[str, Any] = {
            "ts": self.formatTime(record, "%Y-%m-%dT%H:%M:%S") + f".{int(record.msecs):03d}Z",
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
            "pid": self._pid,
        }
        for key, value in record.__dict__.items():
            if key not in _RESERVED:
                out[key] = value
        if record.exc_text:
            out["exc"] = record.exc_text
        return json.dumps(out, default=str, ensure_ascii=False)


class _StderrHandler(logging.StreamHandler):  # type: ignore[type-arg]
    """Looks sys.stderr up on every write, so it never holds a stream that was swapped or closed."""

    def __init__(self) -> None:
        logging.Handler.__init__(self)

    @property
    def stream(self) -> Any:
        return sys.stderr

    @stream.setter
    def stream(self, value: Any) -> None:
        pass


def configure_logging(level: str, secrets: list[str], pid: int) -> None:
    """Route the root logger and uvicorn's loggers through one scrubbed JSON handler."""
    scrubber.register(*secrets)
    handler = _StderrHandler()
    handler.setFormatter(JsonFormatter(pid))
    handler.addFilter(ScrubFilter())
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(level.upper())
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        lg = logging.getLogger(name)
        lg.handlers.clear()
        lg.propagate = True
    # Our middleware writes the access log (route template, no query string, sampled).
    logging.getLogger("uvicorn.access").disabled = True
