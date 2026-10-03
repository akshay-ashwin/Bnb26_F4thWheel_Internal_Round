"""Docker Compose backed tasks. Each returns a process exit code."""

from __future__ import annotations

from fd import console, lint, repo

# Tasks that belong to a later plan print this instead of failing mysteriously.
NOT_IMPLEMENTED: dict[str, int] = {
    "migrate": 2,
    "reset-db": 2,
    "test-api": 3,
    "openapi": 3,
    "sim": 18,
    "eval": 19,
    "demo-reset": 19,
}


def not_implemented(task: str) -> int:
    console.info(f"`{task}` is not implemented yet (Plan {NOT_IMPLEMENTED[task]:02d})")
    return 0


def _compose(*args: str) -> int:
    return repo.run_live(["docker", "compose", *args])


def _require_env() -> bool:
    if (repo.repo_root() / ".env").is_file():
        return True
    console.fail(".env is missing: run `uv run fd secrets` first")
    return False


def up(*, build: bool) -> int:
    if not _require_env():
        return 1
    flags = ["--build"] if build else []
    return _compose("up", "-d", "--wait", *flags)


def down() -> int:
    return _compose("down")


def logs(service: str | None) -> int:
    return _compose("logs", "-f", *([service] if service else []))


def test_web(*, e2e: bool) -> int:
    if e2e:
        console.info("`test-web --e2e` is not implemented yet (Plan 15)")
        return 0
    return _compose("run", "--rm", "--no-deps", "web", "pnpm", "test")


_CONTAINER_LINT: tuple[tuple[str, str], ...] = (
    ("api", "ruff check . && ruff format --check . && mypy ."),
    ("sim", "ruff check . && ruff format --check . && mypy ."),
    ("web", "pnpm lint && pnpm typecheck && pnpm format:check"),
)
_CONTAINER_FMT: tuple[tuple[str, str], ...] = (
    ("api", "ruff check --fix . && ruff format ."),
    ("sim", "ruff check --fix . && ruff format ."),
    ("web", "pnpm format"),
)
_TOOLS_LINT: tuple[tuple[str, ...], ...] = (
    ("ruff", "check", "tools"),
    ("ruff", "format", "--check", "tools"),
    ("mypy",),
)
_TOOLS_FMT: tuple[tuple[str, ...], ...] = (
    ("ruff", "check", "--fix", "tools"),
    ("ruff", "format", "tools"),
)


def _uv_lint_tool(args: tuple[str, ...]) -> int:
    return repo.run_live(["uv", "run", "--quiet", "--group", "lint", *args])


def lint_task(*, staged: bool) -> int:
    code = lint.run_cr_check(staged=staged)
    if staged:
        return code  # pre-commit stays fast and Docker-free
    for args in _TOOLS_LINT:
        code |= _uv_lint_tool(args)
    for service, command in _CONTAINER_LINT:
        code |= _compose("run", "--rm", "--no-deps", service, "sh", "-c", command)
    return 1 if code else 0


def fmt_task() -> int:
    code = 0
    for args in _TOOLS_FMT:
        code |= _uv_lint_tool(args)
    for service, command in _CONTAINER_FMT:
        code |= _compose("run", "--rm", "--no-deps", service, "sh", "-c", command)
    return 1 if code else 0
