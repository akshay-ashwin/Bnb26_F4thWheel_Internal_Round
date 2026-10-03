"""End-to-end proof that the real hook shims reject AI attribution, in a throwaway repo.

Needs git and uv on PATH (true on every CI runner we use). The throwaway repo gets its own
local identity and a core.hooksPath pointing at this repo's infra/git-hooks; the real
repository's config and history are never touched.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from fd import repo

HOOKS = repo.repo_root() / "infra" / "git-hooks"
OWNER = {
    "GIT_AUTHOR_NAME": "Owner",
    "GIT_AUTHOR_EMAIL": "owner@example.com",
    "GIT_COMMITTER_NAME": "Owner",
    "GIT_COMMITTER_EMAIL": "owner@example.com",
}


def _git(
    cwd: Path,
    *args: str,
    env: dict[str, str] | None = None,
    stdin: str | None = None,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        cwd=cwd,
        input=stdin,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
        env={**os.environ, **(env or {})},
    )


@unittest.skipUnless(shutil.which("git") and shutil.which("uv"), "needs git and uv")
class HookShims(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.repo = Path(self._tmp.name)
        _git(self.repo, "init", "-q", "-b", "main")
        _git(self.repo, "config", "--local", "user.name", "Owner")
        _git(self.repo, "config", "--local", "user.email", "owner@example.com")
        _git(self.repo, "config", "--local", "core.hooksPath", HOOKS.as_posix())
        (self.repo / "a.txt").write_text("hello\n", encoding="utf-8", newline="\n")
        _git(self.repo, "add", "a.txt")

    def commit(
        self, message: str, env: dict[str, str] | None = None
    ) -> subprocess.CompletedProcess[str]:
        return _git(self.repo, "commit", "-m", message, env=env)

    def test_clean_commit_passes(self) -> None:
        proc = self.commit("feat: ordinary change")
        self.assertEqual(proc.returncode, 0, proc.stderr)

    def test_ai_trailer_is_rejected(self) -> None:
        proc = self.commit("feat: x\n\nCo-Authored-By: Claude <noreply@anthropic.com>")
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("REJECTED", proc.stderr)
        self.assertEqual(_git(self.repo, "rev-parse", "--verify", "HEAD").returncode != 0, True)

    def test_ai_author_identity_is_rejected(self) -> None:
        env = {**OWNER, "GIT_AUTHOR_EMAIL": "noreply@anthropic.com"}
        proc = self.commit("feat: innocent message", env=env)
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("author email", proc.stderr)

    def test_pre_push_blocks_ai_authored_commit(self) -> None:
        # Create the bad commit with the hooks switched off in this throwaway repo only.
        _git(self.repo, "config", "--local", "core.hooksPath", os.devnull)
        env = {**OWNER, "GIT_AUTHOR_NAME": "Claude", "GIT_AUTHOR_EMAIL": "noreply@anthropic.com"}
        self.assertEqual(self.commit("feat: sneaky", env=env).returncode, 0)
        sha = _git(self.repo, "rev-parse", "HEAD").stdout.strip()
        stdin = f"refs/heads/main {sha} refs/heads/main {'0' * 40}\n"
        proc = subprocess.run(
            ["sh", (HOOKS / "pre-push").as_posix(), "origin", "https://example.invalid/x.git"],
            cwd=self.repo,
            input=stdin,
            capture_output=True,
            text=True,
            encoding="utf-8",
            check=False,
        )
        self.assertNotEqual(proc.returncode, 0, proc.stdout)
        self.assertIn("REJECTED", proc.stderr)

    def test_hook_fails_closed_without_uv(self) -> None:
        sh = shutil.which("sh")
        if sh is None:
            self.skipTest("no sh on PATH")
        msg = self.repo / "msg.txt"
        msg.write_text("feat: x\n", encoding="utf-8", newline="\n")
        proc = subprocess.run(
            [sh, (HOOKS / "commit-msg").as_posix(), msg.as_posix()],
            cwd=self.repo,
            capture_output=True,
            text=True,
            encoding="utf-8",
            check=False,
            env={"PATH": str(Path(sh).parent)},  # sh and coreutils only: no uv on PATH
        )
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("uv run fd doctor", proc.stderr)


if __name__ == "__main__":
    unittest.main()
