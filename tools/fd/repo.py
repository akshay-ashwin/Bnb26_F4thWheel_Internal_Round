"""Repo root discovery and subprocess helpers. Argument lists only, never shell=True."""

from __future__ import annotations

import subprocess
from collections.abc import Sequence
from pathlib import Path


def repo_root() -> Path:
    """The repository root, found from this file so `fd` works from any subfolder."""
    root = Path(__file__).resolve().parents[2]
    if not (root / "tools" / "fd").is_dir():
        raise RuntimeError(f"cannot locate repo root from {__file__}")
    return root


def run(
    args: Sequence[str],
    *,
    cwd: Path | None = None,
    input_text: str | None = None,
) -> subprocess.CompletedProcess[str]:
    """Run a command, capture UTF-8 text output, never raise on a non-zero exit."""
    return subprocess.run(
        list(args),
        cwd=cwd or repo_root(),
        input=input_text,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )


def run_live(args: Sequence[str], *, cwd: Path | None = None) -> int:
    """Run a command attached to this console and return its exit code."""
    try:
        return subprocess.run(list(args), cwd=cwd or repo_root(), check=False).returncode
    except FileNotFoundError:
        print(f"fd: command not found: {args[0]} (run `uv run fd doctor`)")
        return 127


def git(*args: str, cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    return run(["git", *args], cwd=cwd)
