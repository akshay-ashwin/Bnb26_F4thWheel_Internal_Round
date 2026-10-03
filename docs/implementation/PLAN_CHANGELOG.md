# Plan Changelog

Every time a plan is edited after it was first written (Rule R2 or the Refinement Pass of Rule R3), append one line here. Newest at the bottom. Read this file before starting any plan: a later step may have changed what your plan says.

| Date | Edited during plan | Plans changed | Deviation record | One-line summary |
|---|---|---|---|---|
| (initial) | — | 01–19 | — | Plans created from the Fair Drop architecture document. |
| 2026-10-04 | before Plan 01 (human-directed) | CLAUDE.md (root + docs copy), 00_HOW_TO_USE, 01–19 (stack-rules pointer in §0); substantive edits in 01, 02, 03, 04, 06, 08, 09, 14, 15, 16, 18, 19 | D-001 | Cross-platform stack: macOS + Windows, everything via Docker Compose; Make replaced by `uv run fd <task>` (same names, plus `openapi` and `doctor`); LF forced; Node 20 to Node 22 LTS; uvloop optional and per-OS tuning notes replace `ulimit`; Plan 18 shard hash made stable (`crc32`) and spawn-safe; fullstack-dev-skills allowed as advice but never `project:*` commands, CLAUDE.md wins. No contract change. |
| 2026-10-04 | Plan 01 (human-directed) | CLAUDE.md, 00_HOW_TO_USE, 01–19 (§0 pointer); Node text in 01 and 15 | D-002 | Node 22 LTS to Node 24 LTS (`node:24-bookworm-slim`, `engines >=24 <25`, CI on 24). No contract change. |
| 2026-10-04 | Plan 01 (human-directed) | CLAUDE.md, 01 (§4.3, §4.10, §6) | D-003 | Attribution hook patterns narrowed and made trailer-aware (Co-developed-by, Assisted-by, Reviewed-by, Claude Code URL), broad match kept for author/committer; hooks fail closed without `uv`; `fd attribution-check range` added for CI. No contract change. |
| 2026-10-04 | Plan 01 (human-directed) | 00_HOW_TO_USE, 01 (§4.1) | — (human directive, no deviation) | `docs/implementation/CLAUDE.md` deleted: root `CLAUDE.md` is the only authoritative copy so the two cannot drift. |
| 2026-10-04 | Plan 01 (refinement) | 01 (§3, §4.1), handover notes in 02, 03, 15, 18 | — (no deviation: handover facts and a port default the plan allows) | Postgres host port default 15432; `sim` uses a `src` layout; each later plan got a "Plan 01 handover" note with the facts it needs (migrate stub, api and web skeleton details and version pins, sim layout, `fd` stubs). No contract change. |
