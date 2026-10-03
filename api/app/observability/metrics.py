"""Metrics hook slot. Plan 14 appends a callable here; until then nothing is recorded."""

from collections.abc import Callable

# (route_template, method, status, latency_ms)
RequestHook = Callable[[str, str, int, float], None]
request_hooks: list[RequestHook] = []
