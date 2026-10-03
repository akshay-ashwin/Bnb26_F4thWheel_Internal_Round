# Plan Changelog

Every time a plan is edited after it was first written (Rule R2 or the Refinement Pass of Rule R3), append one line here. Newest at the bottom. Read this file before starting any plan: a later step may have changed what your plan says.

| Date | Edited during plan | Plans changed | Deviation record | One-line summary |
|---|---|---|---|---|
| (initial) | — | 01–19 | — | Plans created from the Fair Drop architecture document. |
| 2026-10-04 | before Plan 01 (human-directed) | CLAUDE.md (root + docs copy), 00_HOW_TO_USE, 01–19 (stack-rules pointer in §0); substantive edits in 01, 02, 03, 04, 06, 08, 09, 14, 15, 16, 18, 19 | D-001 | Cross-platform stack: macOS + Windows, everything via Docker Compose; Make replaced by `uv run fd <task>` (same names, plus `openapi` and `doctor`); LF forced; Node 20 to Node 22 LTS; uvloop optional and per-OS tuning notes replace `ulimit`; Plan 18 shard hash made stable (`crc32`) and spawn-safe; fullstack-dev-skills allowed as advice but never `project:*` commands, CLAUDE.md wins. No contract change. |
