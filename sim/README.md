# sim

**Purpose:** the attack simulator and evaluator. It hits the same public API as the browser, labels its own clients as human or bot (ground truth), and produces the fairness scorecard. Ground truth stays in this project; the backend never reads it (invariant 6).
**Owner:** Saanvi (simulator and evaluator).

- Layout: a `src` layout (`src/sim/`), so Plan 18 adds `clients/`, `scenarios/`, `runner/` and `evaluator/` as subpackages of `sim`. Output goes to `out/` (git-ignored).
- Plan 01 contains only a CLI that prints its version and exits.
- Runs in the compose `sim` service (profile `sim`): `uv run fd sim --scenario <name> --mode <fifo|fair>`. It also runs natively on Windows and macOS: `uv run --project sim sim` or `uv run fd sim ... --native`. It must not require uvloop or any Unix-only API; `uvloop` is the optional extra `fast` (`sys_platform != 'win32'`), installed in the container image only.
- A native run creates `sim/.venv` on the host (about 2,500 small files, git-ignored). That is why the repo must not live in a cloud-synced folder; the container run uses the `sim-venv` named volume instead and creates nothing on the host.
- Per-OS tuning notes: `docs/PLATFORMS.md`; full details arrive with Plan 18.
