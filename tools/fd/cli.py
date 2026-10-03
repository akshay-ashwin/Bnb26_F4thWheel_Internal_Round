"""Argument parsing for `uv run fd <task>`. Imports are lazy so hooks start fast."""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path

from fd import __version__, console


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="fd", description="Fair Drop task CLI (replaces Make).")
    parser.add_argument("--version", action="version", version=f"fd {__version__}")
    sub = parser.add_subparsers(dest="task", metavar="<task>", required=True)

    up = sub.add_parser("up", help="start the stack and wait until healthy")
    up.add_argument("--build", action="store_true", help="rebuild images first")
    sub.add_parser("down", help="stop the stack (named volumes are kept)")
    logs = sub.add_parser("logs", help="follow container logs")
    logs.add_argument("service", nargs="?", help="limit to one service")

    sub.add_parser("migrate", help="apply database migrations, dump schema.sql (idempotent)")
    sub.add_parser("reset-db", help="drop the Postgres volume and re-migrate")
    ta = sub.add_parser(
        "test-api", help="migrate fairdrop_test, then run api pytest in the container"
    )
    ta.add_argument("pytest_args", nargs="*", help="extra pytest arguments (put -- before options)")
    tw = sub.add_parser("test-web", help="run web tests in the container")
    tw.add_argument("--e2e", action="store_true", help="also run Playwright (Plan 15)")

    lint = sub.add_parser("lint", help="ruff, mypy, eslint, tsc and the no-CR check")
    lint.add_argument("--staged", action="store_true", help="fast checks on staged files only")
    sub.add_parser("fmt", help="format code with ruff / prettier")

    sub.add_parser("hooks", help="install the versioned git hooks (core.hooksPath)")
    secrets = sub.add_parser("secrets", help="generate .env from .env.example")
    secrets.add_argument("--force", action="store_true", help="overwrite an existing .env")

    sim = sub.add_parser("sim", help="run an attack scenario (Plan 18)")
    sim.add_argument("--scenario", required=True)
    sim.add_argument("--mode", required=True, choices=["fifo", "fair"])
    sim.add_argument("--native", action="store_true", help="run on the host, not in a container")
    ev = sub.add_parser("eval", help="evaluate a run (Plan 19)")
    ev.add_argument("--run", required=True)
    ev.add_argument("--native", action="store_true", help="run on the host, not in a container")

    sub.add_parser("demo-reset", help="reset for a demo run (Plan 19)")
    sub.add_parser("openapi", help="export the OpenAPI snapshot (Plan 03)")
    sub.add_parser("doctor", help="pre-flight checks for this machine")

    attr = sub.add_parser("attribution-check", help="Rule R1 guard (used by hooks and CI)")
    attr_sub = attr.add_subparsers(dest="mode", required=True)
    cm = attr_sub.add_parser("commit-msg", help="check a commit message file")
    cm.add_argument("message_file", type=Path)
    attr_sub.add_parser("pre-push", help="check pushed commits (git feeds stdin)")
    rg = attr_sub.add_parser("range", help="check a revision range, e.g. origin/main..HEAD")
    rg.add_argument("spec")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    console.setup_utf8()
    args = build_parser().parse_args(argv)
    task: str = args.task

    if task == "attribution-check":
        from fd import attribution

        if args.mode == "commit-msg":
            return attribution.check_commit_msg(args.message_file)
        if args.mode == "pre-push":
            return attribution.check_pre_push(sys.stdin.read())
        return attribution.check_range(args.spec)
    if task == "hooks":
        from fd import hooks

        return hooks.install()
    if task == "secrets":
        from fd import secrets_cmd

        return secrets_cmd.generate(force=args.force)
    if task == "doctor":
        from fd import doctor

        return doctor.run()

    from fd import tasks

    if task in tasks.NOT_IMPLEMENTED:
        return tasks.not_implemented(task)
    if task == "up":
        return tasks.up(build=args.build)
    if task == "down":
        return tasks.down()
    if task == "logs":
        return tasks.logs(args.service)
    if task == "migrate":
        return tasks.migrate()
    if task == "reset-db":
        return tasks.reset_db()
    if task == "test-api":
        return tasks.test_api(args.pytest_args)
    if task == "test-web":
        return tasks.test_web(e2e=args.e2e)
    if task == "lint":
        return tasks.lint_task(staged=args.staged)
    if task == "fmt":
        return tasks.fmt_task()
    raise AssertionError(f"unhandled task {task}")  # argparse restricts choices


if __name__ == "__main__":
    raise SystemExit(main())
