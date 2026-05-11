# Zone Stalkers — tick process / auto-run accumulator + batch (implementation status)

## Scope

This document tracks what is **already implemented** in PR #21 and what is still planned to reach the full optimization plan.

It is **not** an empty-map fast path. Changes are general for debug-map / NPC maps / events / emissions / high-speed auto-run.

## Implemented

- Accumulator-based auto-tick scheduler (`x10/x100/x600` via multipliers).
- Lightweight Redis runtime key for auto-tick (`ctx:auto_tick:<context_id>`).
- Batch APIs:
  - `tick_match_many(...)`
  - `ZoneStalkerRuleSet.tick_many(...)`
  - `tick_zone_map_many(...)`
- Batch behavior:
  - one initial copy for batch path,
  - one save/commit cycle for batched tick_many path,
  - one aggregated delta/WS cycle per batch.
- Basic WS coalescing with critical-event bypass.
- Configurable Redis state compression level (`STATE_CACHE_COMPRESSION_LEVEL`).
- Basic stop conditions in batch mode (game_over + critical event classes).
- Basic effective-speed and batch metrics for auto-tick.

## Known limitations / follow-ups

- Concurrency protection is **process-local** (`running` flag in runtime map).  
  Multi-worker deployments still need distributed locking (e.g. Redis lock).
- Some stop-condition semantics can still be refined for player/view-specific cases.
- Full static/runtime state split is not part of this PR.
- Brain v3 CPU optimization remains a separate line of work.

## Verification baseline

- Non-e2e backend suite is green.
- COW invariants from PR2 preserved:
  - default `tick_zone_map` path does not reintroduce full-state pre-deepcopy,
  - batch path uses one initial copy and inner `copy_state=False` ticks.
