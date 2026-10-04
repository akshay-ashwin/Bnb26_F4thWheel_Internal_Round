"""The one place that formats server_time: ISO 8601 UTC, milliseconds, Z suffix."""

from datetime import UTC, datetime


def server_time() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")
