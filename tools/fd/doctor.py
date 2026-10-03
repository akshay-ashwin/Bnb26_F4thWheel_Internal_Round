"""`fd doctor`: cross-platform pre-flight (tools, Docker mode, ports, cloud-sync, .env, hooks)."""

from __future__ import annotations

import re
import socket
from pathlib import Path

from fd import console, envfile, repo

DEFAULT_PORTS: dict[str, int] = {
    "POSTGRES_PORT": 5432,
    "REDIS_PORT": 6379,
    "API_PORT": 8000,
    "WEB_PORT": 5173,
    "WEB_PROD_PORT": 8080,
}
_SYNC_MARKERS = ("onedrive", "icloud", "dropbox", "google drive", "mobile documents")
_MIN_COMPOSE = (2, 20)


def _version_line(args: list[str]) -> str | None:
    try:
        proc = repo.run(args)
    except FileNotFoundError:
        return None
    return proc.stdout.strip().splitlines()[0] if proc.returncode == 0 and proc.stdout else None


def _port_is_free(port: int) -> bool:
    """Test by binding: catches both a listener and an OS-reserved (e.g. Hyper-V) range."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        try:
            sock.bind(("127.0.0.1", port))
        except OSError:
            return False
    return True


def _compose_running() -> bool:
    proc = repo.run(["docker", "compose", "ps", "-q"])
    return proc.returncode == 0 and bool(proc.stdout.strip())


def run() -> int:
    root = repo.repo_root()
    problems = 0

    def need(passed: bool, good: str, bad: str) -> None:
        nonlocal problems
        if passed:
            console.ok(good)
        else:
            console.fail(bad)
            problems += 1

    # 1. Tools
    git_v = _version_line(["git", "--version"])
    need(git_v is not None, git_v or "", "git not found")
    uv_v = _version_line(["uv", "--version"])
    need(uv_v is not None, uv_v or "", "uv not found (https://docs.astral.sh/uv/)")
    docker_v = _version_line(["docker", "--version"])
    need(docker_v is not None, docker_v or "", "docker not found")

    compose_v = _version_line(["docker", "compose", "version", "--short"])
    parsed = re.match(r"v?(\d+)\.(\d+)", compose_v or "")
    compose_ok = bool(parsed) and (int(parsed.group(1)), int(parsed.group(2))) >= _MIN_COMPOSE  # type: ignore[union-attr]
    need(compose_ok, f"docker compose {compose_v}", f"Compose v2 >= 2.20 required (found {compose_v})")

    # 2. Docker daemon in Linux-container mode
    info = repo.run(["docker", "info", "--format", "{{.OSType}}"]) if docker_v else None
    daemon_ok = info is not None and info.returncode == 0
    os_type = info.stdout.strip() if info is not None and daemon_ok else ""
    need(
        daemon_ok and os_type == "linux",
        "Docker daemon is running in Linux-container mode",
        "Docker daemon not reachable or not in Linux-container mode "
        "(start Docker Desktop; on Windows use the WSL2 backend)",
    )

    # 3. Git identity and hooks
    name = repo.git("config", "--get", "user.name").stdout.strip()
    email = repo.git("config", "--get", "user.email").stdout.strip()
    need(bool(name and email), f"git identity: {name} <{email}>", "git user.name/user.email not set (ask the repo owner)")
    hooks = repo.git("config", "--get", "core.hooksPath").stdout.strip()
    if hooks == "infra/git-hooks":
        console.ok("git hooks installed (core.hooksPath = infra/git-hooks)")
    else:
        console.warn("git hooks not installed: run `uv run fd hooks`")

    # 4. Ports (skipped when our own stack is already holding them)
    values = {**{k: str(v) for k, v in DEFAULT_PORTS.items()}, **envfile.read_values(root / ".env")}
    stack_up = daemon_ok and _compose_running()
    for key in DEFAULT_PORTS:
        port = int(values[key])
        if stack_up:
            console.info(f"port {port} ({key}) not tested: the stack is running")
        else:
            need(
                _port_is_free(port),
                f"port {port} ({key}) is free",
                f"port {port} ({key}) cannot be bound (in use or OS-reserved); "
                f"pick another in .env ({key}=...)",
            )

    # 5. Cloud-synced folder warning
    lowered = str(root).lower()
    if any(marker in lowered for marker in _SYNC_MARKERS):
        console.warn(
            f"repo is inside a cloud-synced folder ({root}); sync can lock files and corrupt "
            ".git. Move it (for example to C:\\dev or ~/dev)."
        )
    else:
        console.ok("repo is not inside a cloud-synced folder")

    # 6. .env sanity
    env_path = root / ".env"
    if env_path.is_file():
        need(b"\r" not in env_path.read_bytes(), ".env has LF endings", ".env contains CR bytes; regenerate with `uv run fd secrets --force`")
    else:
        console.warn(".env missing: run `uv run fd secrets`")

    console.info("doctor finished: " + ("all required checks passed" if problems == 0 else f"{problems} problem(s)"))
    return 1 if problems else 0


__all__ = ["Path", "run"]
