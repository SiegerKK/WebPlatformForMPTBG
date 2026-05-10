# CPU Optimization PR 1 — Dirty Runtime Foundation

> Goal:
>
> ```text
> Add safe CPU optimization infrastructure without changing gameplay semantics.
> ```
>
> This PR is intentionally conservative. It should not remove `copy.deepcopy(state)` yet and should not rewrite scheduled actions, needs, or Brain decision policy.
>
> The purpose is to prepare the simulation for later CPU optimization by adding:
>
> ```text
> - TickProfiler with detailed CPU breakdown;
> - TickRuntime / DirtySet tracking;
> - dirty-set based delta builder;
> - fallback to old old_state/new_state diff;
> - selected-agent brain trace gating;
> - safe pathfinding/nearest-object cache for read-only graph queries.
> ```

---

# 1. Why this PR exists

The current optimization branch already reduced network traffic with:

```text
zone_delta
state_revision
debug subscriptions
debug-map-lite
projection endpoints
```

But backend CPU can still be high because each tick may still:

```text
- scan many agents/locations;
- build deltas by comparing old_state and new_state;
- update debug/trace/memory too eagerly;
- recompute graph/path queries.
```

This PR adds the foundation needed to stop doing unnecessary work.

---

# 2. Scope

## In scope

```text
1. TickProfiler with sections/counters.
2. Runtime-only DirtySet.
3. Dirty marker helpers.
4. Add dirty marking in the safest high-traffic mutation paths.
5. Build zone_delta from dirty-set when available.
6. Keep old diff builder as fallback.
7. Brain trace gating: full trace only for selected/debug agents.
8. Read-only pathfinding/nearest-object cache by map_revision.
9. Tests.
```

## Out of scope

Do not do in this PR:

```text
- remove full copy.deepcopy(state);
- event-driven scheduled actions;
- lazy needs model;
- brain decision invalidation;
- AI budget;
- static/runtime state split;
- hot/cold persistence split;
- large frontend rewrite.
```

---

# 3. TickProfiler

## 3.1. Add file

```text
backend/app/games/zone_stalkers/performance/tick_profiler.py
```

## 3.2. API

```python
from contextlib import contextmanager
from time import perf_counter
from typing import Any


class TickProfiler:
    def __init__(self, enabled: bool = True):
        self.enabled = enabled
        self.sections: dict[str, float] = {}
        self.counters: dict[str, int] = {}

    @contextmanager
    def section(self, name: str):
        if not self.enabled:
            yield
            return

        started = perf_counter()
        try:
            yield
        finally:
            elapsed_ms = (perf_counter() - started) * 1000.0
            self.sections[name] = self.sections.get(name, 0.0) + elapsed_ms

    def inc(self, name: str, value: int = 1) -> None:
        if not self.enabled:
            return
        self.counters[name] = self.counters.get(name, 0) + value

    def set_counter(self, name: str, value: int) -> None:
        if not self.enabled:
            return
        self.counters[name] = value

    def to_dict(self) -> dict[str, Any]:
        return {
            "sections_ms": {k: round(v, 3) for k, v in self.sections.items()},
            "counters": dict(self.counters),
        }
```

## 3.3. Add instrumentation

Instrument at least:

```text
ruleset.tick
tick_zone_map
memory_decay
scheduled_actions
active_plan_runtime
npc_brain_total
location_observations
delta_build
save_state
event_db_write if accessible
```

Recommended sections:

```text
load_state_ms
deepcopy_ms
migration_ms
memory_v3_ensure_ms
memory_decay_ms
scheduled_actions_ms
active_plan_runtime_ms
npc_brain_total_ms
location_observations_ms
world_time_advance_ms
delta_build_ms
debug_delta_build_ms
save_state_ms
```

Recommended counters:

```text
agents_total
agents_processed_count
npc_brain_decision_count
npc_brain_skipped_count
dirty_agents_count
dirty_locations_count
dirty_traders_count
dirty_state_fields_count
due_tasks_count
events_count
```

## 3.4. Store metrics

Extend existing performance metrics storage so each tick can include:

```json
{
  "tick_total_ms": 42.5,
  "sections_ms": {
    "npc_brain_total": 18.0,
    "delta_build": 2.3
  },
  "counters": {
    "agents_total": 20,
    "dirty_agents_count": 3
  }
}
```

## 3.5. Acceptance

```text
[ ] /zone-stalkers/debug/performance/{match_id} shows profiler sections/counters.
[ ] Profiler can be disabled with config/state flag.
[ ] Profiler failure never breaks tick.
[ ] Existing tick result shape remains compatible.
```

---

# 4. TickRuntime / DirtySet

## 4.1. Add files

```text
backend/app/games/zone_stalkers/runtime/tick_runtime.py
backend/app/games/zone_stalkers/runtime/dirty.py
```

## 4.2. Runtime structure

```python
from dataclasses import dataclass, field
from typing import Any


@dataclass
class TickRuntime:
    profiler: Any | None = None
    dirty_agents: set[str] = field(default_factory=set)
    dirty_locations: set[str] = field(default_factory=set)
    dirty_traders: set[str] = field(default_factory=set)
    dirty_state_fields: set[str] = field(default_factory=set)
    events: list[dict[str, Any]] = field(default_factory=list)

    def to_debug_counters(self) -> dict[str, int]:
        return {
            "dirty_agents_count": len(self.dirty_agents),
            "dirty_locations_count": len(self.dirty_locations),
            "dirty_traders_count": len(self.dirty_traders),
            "dirty_state_fields_count": len(self.dirty_state_fields),
        }
```

## 4.3. Dirty helpers

```python
def mark_agent_dirty(runtime: TickRuntime | None, agent_id: str | None) -> None:
    if runtime and agent_id:
        runtime.dirty_agents.add(str(agent_id))


def mark_location_dirty(runtime: TickRuntime | None, location_id: str | None) -> None:
    if runtime and location_id:
        runtime.dirty_locations.add(str(location_id))


def mark_trader_dirty(runtime: TickRuntime | None, trader_id: str | None) -> None:
    if runtime and trader_id:
        runtime.dirty_traders.add(str(trader_id))


def mark_state_dirty(runtime: TickRuntime | None, field: str | None) -> None:
    if runtime and field:
        runtime.dirty_state_fields.add(str(field))
```

## 4.4. Setter helpers for hot fields

Add safe helper functions:

```python
def set_agent_field(state: dict, runtime: TickRuntime | None, agent_id: str, key: str, value) -> bool:
    agent = state.get("agents", {}).get(agent_id)
    if not agent:
        return False
    if agent.get(key) == value:
        return False
    agent[key] = value
    mark_agent_dirty(runtime, agent_id)
    return True
```

Similar helpers:

```text
set_location_field
set_trader_field
set_state_field
```

## 4.5. Mark dirty in important mutation paths

Minimum required paths:

```text
agent:
  location_id
  hp
  hunger
  thirst
  sleepiness
  money
  inventory
  equipment
  is_alive
  has_left_zone
  scheduled_action
  active_plan_v3
  current_goal
  global_goal
  action_used
  brain_runtime / debug summaries if touched

location:
  agents
  artifacts
  items
  anomaly_activity
  dominant_anomaly_type

trader:
  money
  inventory
  prices

state:
  world_turn
  world_day
  world_hour
  world_minute
  emission_active
  emission_scheduled_turn
  emission_ends_turn
  game_over
  active_events
  state_revision
  _debug_revision
```

Do not attempt to cover every single mutation in the first commit. Cover the main paths and keep fallback diff.

---

# 5. Delta from dirty-set

## 5.1. Update file

```text
backend/app/games/zone_stalkers/delta.py
```

or add:

```text
backend/app/games/zone_stalkers/delta_dirty.py
```

## 5.2. API

```python
def build_zone_delta_from_dirty(
    *,
    state: dict,
    runtime: TickRuntime,
    events: list[dict],
    mode: str = "game",
) -> dict:
    ...
```

## 5.3. Behavior

Use existing compact functions from current `delta.py`:

```text
compact_agent_for_delta
compact_location_for_delta
compact_trader_for_delta
compact event preview
```

Build:

```json
{
  "base_revision": "previous_revision",
  "revision": "current_revision",
  "world": {},
  "changes": {
    "agents": {
      "agent_1": {}
    },
    "locations": {
      "loc_A": {}
    },
    "traders": {},
    "state": {}
  },
  "events": {
    "count": 3,
    "preview": []
  }
}
```

## 5.4. Fallback

If runtime is missing or dirty-set is empty when events/world changed:

```text
fallback to build_zone_delta(old_state, new_state, events)
```

Do not remove the old builder.

## 5.5. Acceptance

```text
[ ] If one agent changes, dirty delta includes only this agent.
[ ] If agent moves loc_A → loc_B, dirty delta includes agent, loc_A, loc_B.
[ ] World time/state_revision changes are included.
[ ] Old diff builder remains fallback.
[ ] Normal frontend delta flow still works.
```

---

# 6. Brain trace gating

## 6.1. Problem

Full brain trace for every NPC every tick grows memory and CPU.

## 6.2. Add config fields

In zone state:

```json
{
  "debug_brain_trace_enabled": false,
  "debug_brain_trace_agent_ids": []
}
```

Default:

```text
debug_brain_trace_enabled = false
debug_brain_trace_agent_ids = []
```

## 6.3. Behavior

When disabled:

```text
- keep compact latest_decision_summary;
- do not append full trace event for every agent.
```

When enabled:

```text
- trace only selected agent ids;
- keep bounded last N trace events.
```

Suggested cap:

```text
MAX_BRAIN_TRACE_EVENTS_PER_AGENT = 200
```

## 6.4. Acceptance

```text
[ ] NPC profile can still show current decision.
[ ] Full brain trace works for selected debug agents.
[ ] Normal simulation does not grow brain_trace for every NPC.
```

---

# 7. Pathfinding / nearest-object cache

## 7.1. Add file

```text
backend/app/games/zone_stalkers/pathfinding_cache.py
```

## 7.2. Cache key

Cache by:

```text
map_revision
from_location_id
to_location_id
query kind
```

Examples:

```text
shortest_path
shortest_distance
nearest_trader
nearest_shelter
nearest_anomaly_candidates
```

## 7.3. Rules

```text
- cache only read-only graph queries;
- invalidate when map_revision changes;
- do not cache dynamic results that depend on agent inventory/health unless key includes those inputs;
- keep size bounded.
```

Suggested cap:

```text
MAX_PATH_CACHE_ENTRIES = 5000
```

## 7.4. Acceptance

```text
[ ] repeated shortest path queries hit cache.
[ ] map_revision change invalidates cache.
[ ] pathfinding behavior remains identical.
```

---

# 8. Tests

Add/update:

```text
backend/tests/test_zone_stalkers_tick_profiler.py
backend/tests/test_zone_stalkers_dirty_runtime.py
backend/tests/test_zone_stalkers_delta_dirty.py
backend/tests/test_zone_stalkers_brain_trace_gating.py
backend/tests/test_zone_stalkers_pathfinding_cache.py
```

Required tests:

```python
def test_tick_profiler_records_sections_and_counters():
    ...

def test_dirty_set_marks_agent_and_location_on_travel():
    ...

def test_dirty_delta_contains_only_dirty_agent():
    ...

def test_dirty_delta_falls_back_when_runtime_missing():
    ...

def test_dirty_runtime_not_persisted_in_state():
    ...

def test_brain_trace_disabled_by_default_keeps_only_summary():
    ...

def test_pathfinding_cache_invalidates_on_map_revision_change():
    ...
```

Regression tests to run:

```text
backend/tests -k "not e2e"
backend/tests/decision/v3/test_e2e_brain_v3_goals.py
frontend build
```

---

# 9. Manual acceptance

Run a small scenario and verify:

```text
[ ] UI still updates via zone_delta.
[ ] NPC movement/travel still visible.
[ ] Debug map still works.
[ ] Hunt/kill target scenario still works.
[ ] Performance endpoint shows sections/counters.
[ ] dirty counters are non-zero when state changes.
[ ] dirty delta is smaller than old full diff in typical tick.
```

---

# 10. Definition of done

```text
[ ] TickProfiler added and visible in performance endpoint.
[ ] TickRuntime / DirtySet added.
[ ] Main mutation paths mark dirty.
[ ] Delta can be built from dirty-set.
[ ] Old diff delta remains fallback.
[ ] Brain trace is gated.
[ ] Pathfinding cache added for safe read-only queries.
[ ] Tests pass.
[ ] No gameplay semantics intentionally changed.
```

This PR prepares the CPU architecture without risking large behavior changes.
