"""Tiny .env / .env.example reader and writer (LF only, no third-party code)."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

_ASSIGN = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*)=(.*)$")
_SECRET_MARK = re.compile(r"^#\s*secret\s*:\s*yes\b", re.IGNORECASE)
_REF = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")


@dataclass(frozen=True)
class Entry:
    key: str
    value: str
    secret: bool


def read_values(path: Path) -> dict[str, str]:
    """KEY -> value for every assignment in a .env file (no interpolation)."""
    values: dict[str, str] = {}
    if not path.is_file():
        return values
    for line in path.read_text(encoding="utf-8").splitlines():
        match = _ASSIGN.match(line.strip())
        if match:
            values[match.group(1)] = match.group(2).strip()
    return values


def parse_example(text: str) -> list[Entry]:
    """Assignments from .env.example; `# Secret: yes` in the comment block marks a secret."""
    entries: list[Entry] = []
    secret = False
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            secret = False
        elif line.startswith("#"):
            secret = secret or bool(_SECRET_MARK.match(line))
        else:
            match = _ASSIGN.match(line)
            if match:
                entries.append(Entry(match.group(1), match.group(2).strip(), secret))
            secret = False
    return entries


def expand_refs(values: dict[str, str]) -> dict[str, str]:
    """Replace ${NAME} with another value from the same file (one level, in file order)."""
    return {k: _REF.sub(lambda m: values.get(m.group(1), m.group(0)), v) for k, v in values.items()}
