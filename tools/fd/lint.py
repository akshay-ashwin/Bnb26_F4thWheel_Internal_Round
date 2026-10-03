"""Host-side lint checks that need no Docker: no CR bytes in tracked text files."""

from __future__ import annotations

import subprocess
from pathlib import Path

from fd import console, repo

_BINARY_SUFFIXES = frozenset(
    ".png .jpg .jpeg .gif .ico .webp .avif .woff .woff2 .ttf .otf .eot .zip .gz .tar .tgz "
    ".pdf .mp4 .webm .mp3 .wasm".split()
)


def _is_binary(data: bytes) -> bool:
    return b"\x00" in data[:8192]


def _index_bytes(root: Path, name: str) -> bytes:
    """Raw bytes of the staged blob (text mode would hide CR behind universal newlines)."""
    return subprocess.run(
        ["git", "show", f":{name}"], cwd=root, capture_output=True, check=True
    ).stdout


def find_cr_files(root: Path, *, staged: bool) -> list[str]:
    """Return tracked (or staged) text files that contain a CR byte.

    Staged mode reads the index blob (what will be committed); full mode reads the working
    tree (what the Docker bind mount will see).
    """
    if staged:
        listing = repo.git("diff", "--cached", "--name-only", "-z", "--diff-filter=ACMR", cwd=root)
    else:
        listing = repo.git("ls-files", "-z", cwd=root)
    if listing.returncode != 0:
        raise RuntimeError(f"git file listing failed: {listing.stderr.strip()}")

    offenders: list[str] = []
    for name in (item for item in listing.stdout.split("\0") if item):
        if Path(name).suffix.lower() in _BINARY_SUFFIXES:
            continue
        if staged:
            raw = _index_bytes(root, name)
        else:
            path = root / name
            if not path.is_file():
                continue
            raw = path.read_bytes()
        if not _is_binary(raw) and b"\r" in raw:
            offenders.append(name)
    return offenders


def run_cr_check(*, staged: bool) -> int:
    root = repo.repo_root()
    try:
        offenders = find_cr_files(root, staged=staged)
    except (RuntimeError, OSError, subprocess.CalledProcessError) as exc:
        console.fail(f"CR check could not run: {exc}")
        return 1
    if offenders:
        console.fail("CR (carriage return) bytes found; files must use LF:")
        for name in offenders:
            print(f"  - {name}")
        console.info("fix: re-save with LF endings (see .editorconfig), then `git add` again.")
        return 1
    console.ok("no CR bytes in tracked text files")
    return 0
