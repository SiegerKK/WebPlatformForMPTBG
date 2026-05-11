# Zone Stalkers — tick process / auto-run accumulator + batch (implementation status)

## Scope

This document tracks what is implemented in PR #21/#22 and the currently known boundaries.

It is **not** an empty-map fast path. Changes are general for debug-map / NPC maps / events / emissions / high-speed auto-run.

## Implemented

- Accumulator scheduler for auto-run speed control (`x10/x100/x600` multipliers).
- Configurable catch-up limits and runtime settings:
  - `AUTO_TICK_MAX_TICKS_PER_BATCH`
  - `AUTO_TICK_MAX_ACCUMULATED_TICKS`
  - `AUTO_TICK_MAX_WS_UPDATES_PER_SECOND`
  - `AUTO_TICK_MAX_CATCHUP_BATCHES_PER_LOOP`
  - `AUTO_TICK_CATCHUP_MODE` (`accurate` / `smooth`).
- Lightweight Redis auto-tick settings key (`ctx:auto_tick:<context_id>`).
- Batch tick advancement pipeline:
  - `tick_match_many(...)`
  - `ZoneStalkerRuleSet.tick_many(...)`
  - `tick_zone_map_many(...)`.
- Batch overhead reduction behavior:
  - one state load and one state save per batch,
  - one delta build and one WS cycle per batch (with coalescing),
  - one commit/rollback cycle per batch.
- Effective speed metrics in backend and debug UI target/effective speed display.
- Player-aware batch stop semantics for human/view-relevant events.

## Known limitations / follow-ups

- Concurrency protection is **process-local** (`running` flag in runtime map).  
  Multi-worker deployments still need distributed locking (e.g. Redis lock).
- Full static/runtime state split is not part of these PRs.
- Brain v3 CPU optimizations remain separate work.

## Verification baseline

- Non-e2e backend suite is green.
- COW invariants from PR2 preserved:
  - default `tick_zone_map` path does not reintroduce full-state pre-deepcopy,
  - batch path uses one initial copy and inner `copy_state=False` ticks.
