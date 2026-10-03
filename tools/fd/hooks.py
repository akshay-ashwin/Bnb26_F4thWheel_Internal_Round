"""`fd hooks`: point git at the versioned hook shims (repo-local config; never identity)."""

from __future__ import annotations

import os
import stat

from fd import console, repo

HOOKS_DIR = "infra/git-hooks"
HOOK_NAMES = ("commit-msg", "pre-push", "pre-commit")


def install() -> int:
    root = repo.repo_root()
    hooks_path = root / HOOKS_DIR
    missing = [name for name in HOOK_NAMES if not (hooks_path / name).is_file()]
    if missing:
        console.fail(f"missing hook shims in {HOOKS_DIR}: {', '.join(missing)}")
        return 1

    proc = repo.git("config", "--local", "core.hooksPath", HOOKS_DIR, cwd=root)
    if proc.returncode != 0:
        console.fail(f"could not set core.hooksPath: {proc.stderr.strip()}")
        return 1

    for name in HOOK_NAMES:
        rel = f"{HOOKS_DIR}/{name}"
        if os.name != "nt":
            path = hooks_path / name
            path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
        # Executable bit in the index so macOS/Linux checkouts get runnable hooks.
        if repo.git("ls-files", "--error-unmatch", rel, cwd=root).returncode == 0:
            repo.git("update-index", "--chmod=+x", rel, cwd=root)

    console.ok(f"core.hooksPath = {HOOKS_DIR} (repo-local). Hooks: {', '.join(HOOK_NAMES)}")
    return 0
