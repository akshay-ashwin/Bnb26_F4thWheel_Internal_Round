# D-004 — Lean mode

Date: 2026-10-04  ·  Raised during: Plan 02 (human-directed)  ·  Status: ADOPTED

## The original plan said

R2–R4 and each plan's close-out require deviation records, downstream plan rewrites, a full refinement pass and a long review log for every plan.

## What we do instead

Root `CLAUDE.md` now has a "LEAN MODE" section that overrides R2–R4 and the close-outs (short review logs, one-line changelog entries, D-records only for contract changes, no plan-mode step, lane ownership). Each plan from 02 to 19 has a `[CUT]` note under its title listing what is not built; the plan bodies are not rewritten.

## Why

The deadline is short (human directive). The cuts remove hardening and polish, not the mechanism or the evidence.

## Invariants check

All 8 invariants and R1 (with the hooks) are kept. Integrity tests stay. Cuts such as the Redis jti check, counter reconciliation and Redis-down fallback buckets never decide winners or seat counts.

## Contract impact

None.

## Plans edited

02, 05–19: a `[CUT]` note under the title only.

## Rollback

Revert this commit.
