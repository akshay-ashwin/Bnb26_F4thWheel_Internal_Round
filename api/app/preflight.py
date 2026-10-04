"""Validate settings once, before uvicorn spawns workers, so a bad config stops the container
with one clear message instead of a crash loop of respawning workers."""

import sys

from pydantic import ValidationError

from app.config import Settings


def main() -> int:
    try:
        Settings()  # type: ignore[call-arg]  # fields come from the environment
    except ValidationError as exc:
        print("fairdrop: refusing to start, invalid configuration:", file=sys.stderr)
        for error in exc.errors():
            where = ".".join(str(p) for p in error["loc"]) or "settings"
            print(f"  - {where}: {error['msg']}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
