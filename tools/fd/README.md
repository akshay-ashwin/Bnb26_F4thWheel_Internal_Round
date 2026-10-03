# tools/fd

**Purpose:** the `uv run fd <task>` task CLI that replaces Make, identical on macOS and Windows.
**Owner:** Akshay (stack).

Rules: standard library only; subprocesses always get an argument list (never `shell=True`); output is forced to UTF-8 with no ANSI meaning; it finds the repo root itself so it works from any subfolder; it never imports `api/` or `sim/`.

| Module | Role |
| --- | --- |
| `cli.py` | argparse, one subcommand per task; imports are lazy so git hooks start fast |
| `tasks.py` | Docker Compose backed tasks and the "not implemented yet (Plan NN)" stubs |
| `attribution.py` | Rule R1 guard: pure pattern functions plus the commit-msg, pre-push and CI range checks |
| `lint.py` | the no-CR-bytes check (`--staged` reads the index) |
| `hooks.py` | sets `core.hooksPath` and the executable bit |
| `doctor.py` | pre-flight checks |
| `secrets_cmd.py`, `envfile.py` | `.env` generation from `.env.example` |
| `repo.py`, `console.py` | root discovery, subprocess helpers, UTF-8 console |

Tests live in `tools/tests` and need only git and uv: `uv run python -m unittest discover -s tools/tests -t tools`. They include end-to-end checks of the real hook shims in a throwaway repository. Lint and type-check this project with `uv run fd lint` (ruff and mypy strict, group `lint`).
