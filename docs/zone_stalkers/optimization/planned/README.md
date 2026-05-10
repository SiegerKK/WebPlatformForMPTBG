# Planned CPU Optimization PRs

CPU PR1 (`cpu_pr1_dirty_runtime_foundation`) has been **implemented and merged**. The implemented doc has been moved to [`../implemented/cpu_pr1_dirty_runtime_foundation.md`](../implemented/cpu_pr1_dirty_runtime_foundation.md).

The following PRs have not yet been implemented. They are planned as a sequential series that reduces backend CPU cost without changing gameplay semantics.

Each PR builds on the previous:

1. ~~[`cpu_pr1_dirty_runtime_foundation.md`](../implemented/cpu_pr1_dirty_runtime_foundation.md)~~ — **Completed.** TickProfiler, DirtySet, dirty-based delta builder, brain trace gating, pathfinding cache.
2. [`cpu_pr2_copy_on_write_runtime.md`](./cpu_pr2_copy_on_write_runtime.md) — Replace full `copy.deepcopy(state)` with copy-on-write mutation.
3. [`cpu_pr3_event_driven_actions_lazy_needs.md`](./cpu_pr3_event_driven_actions_lazy_needs.md) — Event-driven long actions (started_turn/ends_turn), lazy needs model with scheduled threshold tasks.
4. [`cpu_pr4_brain_invalidation_ai_budget.md`](./cpu_pr4_brain_invalidation_ai_budget.md) — Brain invalidation, valid_until_turn cache, per-tick AI decision budget.

The prerequisite for PR2 is that CPU PR1 has been merged.
