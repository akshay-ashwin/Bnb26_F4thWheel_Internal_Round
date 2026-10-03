"""Docker Compose backed tasks. Each returns a process exit code."""

from __future__ import annotations

import re
import time

from fd import console, envfile, lint, repo

# The API's own database role (created NOLOGIN by the migrations, given LOGIN and a password here).
APP_ROLE = "fairdrop_app"
# Database the api tests run against. The `_test` suffix is the safety guard: the tests truncate
# tables and refuse any other name (api/tests/conftest.py checks it again).
TEST_DB = "fairdrop_test"
_SAFE_SECRET = re.compile(r"[A-Za-z0-9]{16,}")
_MIGRATE_ATTEMPTS = 3

# Tasks that belong to a later plan print this instead of failing mysteriously.
NOT_IMPLEMENTED: dict[str, int] = {
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


def _env_values() -> dict[str, str]:
    return envfile.read_values(repo.repo_root() / ".env")


def _ensure_postgres() -> int:
    return _compose("up", "-d", "--wait", "postgres")


def _migrate_database(database: str | None, *, dump_schema: bool) -> int:
    """Apply api/migrations to one database with dbmate, then give the app role its password.

    Idempotent: dbmate skips applied migrations and creates the database when it is missing.
    The password goes to psql on stdin (never on a command line).
    """
    values = _env_values()
    password = values.get("APP_DB_PASSWORD", "")
    if not _SAFE_SECRET.fullmatch(password):
        console.fail(
            "APP_DB_PASSWORD is missing or not alphanumeric: run `uv run fd secrets --force`"
            " (then `uv run fd reset-db`) to regenerate .env"
        )
        return 1
    if _ensure_postgres():
        return 1
    env = {"MIGRATE_DB": database} if database else None
    flags = [] if dump_schema else ["--no-dump-schema"]
    dbmate = ["docker", "compose", "--profile", "tools", "run", "--rm", "migrate", *flags]
    # A brand-new Postgres volume restarts the server once after initialisation; the health check
    # can go green just before, which drops the first connection. Migrations are transactional
    # and idempotent, so trying again is safe.
    for attempt in range(1, _MIGRATE_ATTEMPTS + 1):
        code = repo.run_live([*dbmate, "--wait", "up"], env=env)
        if not code:
            break
        if attempt < _MIGRATE_ATTEMPTS:
            console.info(f"migration attempt {attempt} failed; retrying in 3 s")
            time.sleep(3)
    if code:
        return code
    database = database or values.get("POSTGRES_DB", "fairdrop")
    sql = f"ALTER ROLE {APP_ROLE} WITH LOGIN PASSWORD '{password}';\n"
    psql = 'exec psql -q -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d "$1"'
    result = repo.run(
        ["docker", "compose", "exec", "-T", "postgres", "sh", "-c", psql, "psql", database],
        input_text=sql,
    )
    if result.returncode:
        console.fail(f"could not set the {APP_ROLE} password: {result.stderr.strip()}")
        return 1
    console.ok(f"migrations applied to {database}; role {APP_ROLE} can log in")
    return 0


def migrate() -> int:
    if not _require_env():
        return 1
    return _migrate_database(None, dump_schema=True)


def reset_db() -> int:
    """Remove the Postgres data volume (and every container using it), then migrate again."""
    if not _require_env():
        return 1
    if _compose("down"):
        return 1
    # Volume name = compose project (fairdrop, set in compose.yaml) + "_pgdata". Missing is fine.
    repo.run(["docker", "volume", "rm", "fairdrop_pgdata"])
    return _migrate_database(None, dump_schema=True)


def test_api(pytest_args: list[str]) -> int:
    """Migrate fairdrop_test with the same files as the real database, then run pytest.

    The api container gets two URLs: TEST_DATABASE_URL (owner role, for fixtures and fault
    injection) and TEST_APP_DATABASE_URL (the restricted fairdrop_app role, what the api uses).
    """
    if not _require_env():
        return 1
    if not TEST_DB.endswith("_test"):  # pragma: no cover - constant guard
        raise AssertionError("test database name must end in _test")
    code = _migrate_database(TEST_DB, dump_schema=False)
    if code:
        return code
    values = _env_values()
    user, password = values["POSTGRES_USER"], values["POSTGRES_PASSWORD"]
    app_password = values["APP_DB_PASSWORD"]
    env = {
        "TEST_DATABASE_URL": f"postgresql://{user}:{password}@postgres:5432/{TEST_DB}",
        "TEST_APP_DATABASE_URL": f"postgresql://{APP_ROLE}:{app_password}@postgres:5432/{TEST_DB}",
    }
    # Same sync-then-run shape as the api service command; "$@" carries the pytest arguments.
    script = 'uv sync --frozen --no-install-project && exec pytest "$@"'
    return repo.run_live(
        ["docker", "compose", "run", "--rm"]
        + ["-e", "TEST_DATABASE_URL", "-e", "TEST_APP_DATABASE_URL"]
        + ["api", "sh", "-c", script, "pytest", *pytest_args],
        env=env,
    )


def up(*, build: bool) -> int:
    if not _require_env():
        return 1
    flags = ["--build"] if build else []
    return _compose("up", "-d", "--wait", *flags)


def down() -> int:
    return _compose("down")


def logs(service: str | None) -> int:
    return _compose("logs", "-f", *([service] if service else []))


# A one-off `compose run` replaces the service command, so it must re-sync the venv volume itself.
_SYNC_THEN = ("sh", "-c", 'uv sync --frozen --no-install-project -q 1>&2 && exec "$@"', "sh")

OPENAPI_SNAPSHOT = "docs/contract/openapi.json"


def openapi(*, check: bool) -> int:
    """Export the spec from the api container; write it (or compare it) from the host side."""
    if not _require_env():
        return 1
    result = repo.run(
        [
            "docker",
            "compose",
            "run",
            "--rm",
            "--no-deps",
            "-T",
            "api",
            *_SYNC_THEN,
            "python",
            "-m",
            "app.export_openapi",
        ]
    )
    if result.returncode != 0:
        console.fail("could not export the OpenAPI spec")
        return 1
    spec = result.stdout  # text mode already normalises CRLF to LF
    path = repo.repo_root() / OPENAPI_SNAPSHOT
    if check:
        if path.is_file() and path.read_bytes().decode("utf-8") == spec:
            console.info("OpenAPI snapshot is up to date")
            return 0
        console.fail(
            f"{OPENAPI_SNAPSHOT} differs from the live spec: contract change? run `uv run fd openapi`"
        )
        return 1
    path.write_bytes(spec.encode("utf-8"))
    console.info(f"wrote {OPENAPI_SNAPSHOT}")
    return 0


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