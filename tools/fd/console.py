"""Console helpers: UTF-8 output on every OS, plain-text status lines (no ANSI meaning)."""

from __future__ import annotations

import sys


def setup_utf8() -> None:
    """Force UTF-8 on stdout/stderr so a Windows console never crashes on a dash."""
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            reconfigure(encoding="utf-8", errors="replace")


def info(message: str) -> None:
    print(f"fd: {message}", flush=True)


def ok(message: str) -> None:
    print(f"fd: OK    {message}", flush=True)


def warn(message: str) -> None:
    print(f"fd: WARN  {message}", file=sys.stderr, flush=True)


def fail(message: str) -> None:
    print(f"fd: FAIL  {message}", file=sys.stderr, flush=True)
