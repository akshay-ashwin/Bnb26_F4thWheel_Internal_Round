"""Placeholder entry point: prints the version and exits. Plan 18 adds the real commands."""

from __future__ import annotations

import sys
from collections.abc import Sequence

from sim import __version__


def main(argv: Sequence[str] | None = None) -> int:
    _ = argv if argv is not None else sys.argv[1:]
    print(f"fairdrop-sim {__version__}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
