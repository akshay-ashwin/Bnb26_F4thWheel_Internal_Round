"""Rule R1 guard: detect AI attribution in commit messages and in author/committer identities.

Pure matching functions (`scan_message`, `scan_identity`) are unit-tested; the git plumbing
below feeds them from the commit-msg hook, the pre-push hook and CI (`range`).
Patterns are documented in docs/decisions/D-003-attribution-patterns.md.
"""

from __future__ import annotations

import re
import subprocess
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from fd import repo

_AI = r"(claude|anthropic)"
_TRAILER_KEYS = (
    r"co-authored-by|co-developed-by|assisted-by|reviewed-by|signed-off-by|"
    r"generated-by|suggested-by|helped-by"
)

# (label, compiled pattern) applied to the whole commit message, case-insensitive.
MESSAGE_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "AI trailer (Co-Authored-By / Co-developed-by / Assisted-by / Reviewed-by ...)",
        re.compile(rf"^[ \t]*(?:{_TRAILER_KEYS})[ \t]*:.*{_AI}", re.IGNORECASE | re.MULTILINE),
    ),
    ("noreply@anthropic.com address", re.compile(r"noreply@anthropic\.com", re.IGNORECASE)),
    (
        '"Generated with/by Claude" line',
        re.compile(r"generated[ \t]+(?:with|by)[ \t]+(?:\[)?[ \t]*claude", re.IGNORECASE),
    ),
    (
        "Claude Code URL",
        re.compile(r"(?:claude\.com|anthropic\.com)/claude-code|claude\.ai/code", re.IGNORECASE),
    ),
    ("robot emoji", re.compile("\U0001f916")),
)

_IDENTITY_PATTERN = re.compile(_AI, re.IGNORECASE)

_ZERO_SHA = "0" * 40


def _git(*args: str) -> subprocess.CompletedProcess[str]:
    """Run git in the process working directory.

    Git runs hooks inside the repository being committed to or pushed from, which may be a
    worktree or another checkout; it is not necessarily the repo that contains `fd`.
    """
    return repo.run(["git", *args], cwd=Path.cwd())


@dataclass(frozen=True)
class Finding:
    where: str
    reason: str
    excerpt: str

    def render(self) -> str:
        return f"  - {self.where}: {self.reason}: {self.excerpt!r}"


def scan_message(text: str, where: str = "commit message") -> list[Finding]:
    findings: list[Finding] = []
    for label, pattern in MESSAGE_PATTERNS:
        match = pattern.search(text)
        if match:
            findings.append(Finding(where, label, match.group(0).strip()[:80]))
    return findings


def scan_identity(name: str, email: str, role: str, where: str = "commit") -> list[Finding]:
    """Broad match on purpose: an AI name or e-mail as author/committer is never legitimate."""
    findings: list[Finding] = []
    for field, value in (("name", name), ("email", email)):
        match = _IDENTITY_PATTERN.search(value)
        if match:
            findings.append(Finding(where, f"{role} {field} names an AI tool", value[:80]))
    return findings


def _parse_ident(ident: str) -> tuple[str, str]:
    """Split the output of `git var GIT_AUTHOR_IDENT` ('Name <email> ts tz')."""
    match = re.match(r"^(.*?)\s*<(.*?)>", ident)
    return (match.group(1), match.group(2)) if match else (ident, "")


def _report(findings: Sequence[Finding], header: str) -> int:
    if not findings:
        return 0
    print(f"fd: REJECTED (Rule R1: no AI attribution). {header}", file=sys.stderr)
    for finding in findings:
        print(finding.render(), file=sys.stderr)
    print(
        "fd: remove the attribution and try again. --no-verify is forbidden "
        "(see docs/CONTRIBUTING.md).",
        file=sys.stderr,
    )
    return 1


def check_commit_msg(message_file: Path) -> int:
    """commit-msg hook: message text plus the identities this commit will be created with."""
    text = message_file.read_text(encoding="utf-8", errors="replace")
    findings = scan_message(text)
    for role, var in (("author", "GIT_AUTHOR_IDENT"), ("committer", "GIT_COMMITTER_IDENT")):
        proc = _git("var", var)
        if proc.returncode != 0:
            # Fail closed: an identity we cannot read is an identity we cannot vouch for.
            print(f"fd: cannot read {var}: {proc.stderr.strip()}", file=sys.stderr)
            return 1
        name, email = _parse_ident(proc.stdout.strip())
        findings += scan_identity(name, email, role, "this commit")
    return _report(findings, "This commit was not created.")


_FIELD = "\x1f"
_RECORD = "\x1e"
_LOG_FORMAT = f"%H{_FIELD}%an{_FIELD}%ae{_FIELD}%cn{_FIELD}%ce{_FIELD}%B{_RECORD}"


def scan_range(rev_args: Sequence[str]) -> tuple[int, list[Finding]]:
    """Scan every commit selected by `git log <rev_args>`. Returns (commit_count, findings)."""
    proc = _git("log", f"--format={_LOG_FORMAT}", *rev_args)
    if proc.returncode != 0:
        raise RuntimeError(f"git log failed: {proc.stderr.strip()}")
    findings: list[Finding] = []
    count = 0
    for record in proc.stdout.split(_RECORD):
        if not record.strip():
            continue
        sha, an, ae, cn, ce, body = record.strip("\n").split(_FIELD, 5)
        count += 1
        where = f"commit {sha[:10]}"
        findings += scan_identity(an, ae, "author", where)
        findings += scan_identity(cn, ce, "committer", where)
        findings += scan_message(body, where)
    return count, findings


def _push_rev_args(stdin_text: str) -> list[list[str]]:
    """Turn git's pre-push stdin ('<lref> <lsha> <rref> <rsha>' lines) into git-log arguments."""
    specs: list[list[str]] = []
    for line in stdin_text.splitlines():
        parts = line.split()
        if len(parts) != 4:
            continue
        _lref, lsha, _rref, rsha = parts
        if lsha == _ZERO_SHA:  # deleting a remote ref: nothing to scan
            continue
        if rsha == _ZERO_SHA or _git("cat-file", "-e", f"{rsha}^{{commit}}").returncode != 0:
            specs.append([lsha, "--not", "--remotes"])  # new branch or unknown remote tip
        else:
            specs.append([f"{rsha}..{lsha}"])
    return specs


def check_pre_push(stdin_text: str) -> int:
    findings: list[Finding] = []
    total = 0
    try:
        for rev_args in _push_rev_args(stdin_text):
            count, found = scan_range(rev_args)
            total += count
            findings += found
    except RuntimeError as exc:
        print(f"fd: {exc}; blocking the push (fail closed).", file=sys.stderr)
        return 1
    return _report(findings, f"Push blocked ({total} commit(s) scanned).")


def check_range(spec: str) -> int:
    """CI entry point: `fd attribution-check range origin/main..HEAD`."""
    try:
        count, findings = scan_range([spec])
    except RuntimeError as exc:
        print(f"fd: {exc}", file=sys.stderr)
        return 1
    code = _report(findings, f"{count} commit(s) scanned in {spec}.")
    if code == 0:
        print(f"fd: attribution check passed ({count} commit(s) in {spec}).")
    return code
