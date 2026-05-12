# Zone Stalkers — Applied CPU Optimizations PR1–PR5

## 1. Summary

Zone Stalkers auto-run performance degraded as NPC count and NPC lifetime grew: Effective simulation speed dropped not only from Brain v3 logic, but also from state growth, retrieval overhead, tick pipeline costs, and map/runtime data access.

Observed result now:

- Before PR5: 10 NPCs could drop Effective to **x45 or lower**.
- After PR5: 10 NPCs run around **x150–x200**.
- Next target remains future support for around **100 NPCs**.

## 2. Optimization timeline

### PR1 — Profiling / dirty runtime foundation / baseline instrumentation

Applied:

- Tick profiler sections/counters for staged CPU attribution.
- Runtime dirty-tracking foundation for agent/location/state changes.
- Initial instrumentation for identifying dominant hot paths.
- Safety-first rollout groundwork for later PRs.

### PR2 — Copy-on-write runtime

Applied:

- Replaced heavy full-state mutation pressure with copy-on-write runtime mutation.
- Reduced full `deepcopy` pressure in tick processing path.
- Preserved existing tick semantics while reducing write amplification.
- Improved dirty-state handling compatibility with runtime updates.

### PR3 — Event-driven scheduled actions and lazy needs

Applied:

- Event-driven long action progression and scheduling.
- Lazy needs model to avoid unnecessary per-tick recomputation.
- Scheduled threshold-task processing for needs transitions.
- Reduced unnecessary NPC work on idle/unchanged turns.

### PR4 — Brain invalidation and AI budget

Applied:

- `brain_runtime` cache/validity layer (`valid_until_turn` + invalidation metadata).
- Invalidation reasons and priorities, including urgent paths.
- Decision queue plus per-tick AI budget execution constraints.
- Urgent bypass and starvation-promotion behavior for fairness and responsiveness.
- E2E correctness handling for active plans under invalidation.

### PR5 — Memory store cleanup and fast retrieval

Applied:

- Removed legacy `agent["memory"]` runtime/test usage.
- `memory_v3` became the canonical NPC memory store.
- `memory_v3` hot cap enforced at **500** records.
- `retrieve_memory` switched to optimized raw scoring + heap/top-k + candidate limits.
- Retrieval is read-only by default.
- `context_builder` moved to memory_v3-only assumptions.
- Tests migrated to memory_v3 helpers/assertions.

## 3. Current architecture after PR1–PR5

- Tick loop and batch auto-run rely on optimized runtime mutation patterns.
- NPC memory is memory_v3-only.
- Brain execution is primarily invalidation/expiry-driven instead of unconditional rerun.
- Stable active plans continue without forcing full brain recomputation each tick.
- Scheduled actions and plan execution are event-driven where possible.
- Remaining system-wide costs still include projection/delta/serialization paths.

## 4. Performance impact

- 10 NPCs no longer collapse to x45 Effective speed in the tested scenario.
- Current observed range is around x150–x200.
- This is a substantial gain but still below long-term 100-NPC target robustness.

Notes:

- PR1–PR4 alone did not produce the same visible gain in the tested workload.
- PR5 memory cleanup/retrieval optimization produced a major practical improvement.
- Map/runtime structural improvements also materially contributed.

## 5. Remaining bottlenecks

- Map/location/path access patterns in dense worlds.
- Full projection/delta generation and downstream payload handling.
- State serialization/compression and persistence overhead.
- DB event insertion volume under high activity.
- Active plan runtime/monitor costs under scale.
- memory_v3 remains hot-state (even capped).
- Frontend/debug subscription fan-out and render pressure.

## 6. Next optimization candidates

- Dirty-delta specialization for `tick_many`/batch auto-run.
- Hot/cold state split for lower steady-state memory churn.
- Active-plan monitoring throttling/event-driven monitors.
- Partialized state-save pipelines.
- Expanded map indexes/path caches.
- Performance smoke benchmark for 100 NPCs in CI/dev tooling.

## 7. Test and validation coverage

Current grouped CI coverage:

- Backend core tests
- Zone Stalkers core tests
- Decision core tests
- Memory v3 / PR5 tests
- Brain v3 E2E matrix
- Backend full non-e2e sentinel
- Frontend build

## 8. Maintenance rules

- Do not reintroduce `agent["memory"]` runtime compatibility.
- Keep memory_v3 cap enforcement.
- Keep `retrieve_memory` read-only by default.
- New CPU optimizations must add/retain profiler visibility.
- Keep full backend non-e2e sentinel in CI as a catch-all safety net.

