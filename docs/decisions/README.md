# Decisions (Deviation Records)

A Deviation Record is a short, numbered document (`D-001-...md`, `D-002-...md`) written whenever the build departs from an implementation plan because something better was found (Rule R2 in `CLAUDE.md`). Each one says what the plan said, what we do instead, the evidence that it is better, an invariants check, the contract impact, which plans were edited, and how to roll back.

Write one with `docs/implementation/templates/DEVIATION_RECORD_TEMPLATE.md`. Take the next free number. Edit every later plan that is affected, mark the passage `> Updated by D-00X (<date>): <why>`, and add a line to `docs/implementation/PLAN_CHANGELOG.md`.

| Record | Summary |
| --- | --- |
| D-001 | Cross-platform stack: Docker-only runtime, `uv run fd` instead of Make, LF everywhere, skills policy |
| D-002 | Node 24 LTS instead of Node 22 LTS |
| D-003 | Narrower, trailer-aware attribution patterns; hooks fail closed |

No record may weaken the eight invariants in `CLAUDE.md`. If you think an invariant is wrong, stop and ask a human.
