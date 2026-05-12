# CPU Optimization PR 2 — Copy-on-Write Runtime and Remove Full Deepcopy

> ⚠️ Archived PR document.
> Superseded by [`../cpu_optimization_applied_pr1_pr5.md`](../cpu_optimization_applied_pr1_pr5.md).

> Base:
>
> ```text
> after CPU Optimization PR 1 — Dirty Runtime Foundation
> ```
>
> Goal:
>
> ```text
> Remove or minimize full copy.deepcopy(state) from tick hot path.
> ```
>
> This PR should preserve gameplay behavior. It should not introduce event-driven scheduled actions, lazy needs, or brain invalidation yet.

---

# 1. Problem

`tick_zone_map()` currently starts from a full copy:

```python
state = copy.deepcopy(state)
```

This copies:

```text
agents
locations
traders
memory
memory_v3
brain_trace
debug
inventory
active plans
combat state
```

Cost grows with total world size, not with the number of changed entities.

After PR1, we already have:

```text
TickRuntime
dirty sets
dirty delta fallback
profiler
```

Now we can replace full deepcopy with copy-on-write mutation.

---

# 2. Scope

## In scope

```text
1. ZoneTickRuntime copy-on-write wrapper.
2. Replace hot-path mutations with runtime accessors.
3. Ensure tick does not mutate input state.
4. Keep fallback/debug flag to use deepcopy if needed.
5. Tests for mutation isolation.
```

## Out of scope

Do not do in this PR:

```text
- event-driven scheduled actions;
- lazy needs;
- brain invalidation;
- AI budget;
- pathfinding algorithm changes;
- memory model redesign.
```

---

# 3. Add ZoneTickRuntime

## 3.1. Add file

```text
backend/app/games/zone_stalkers/runtime/zone_tick_runtime.py
```

## 3.2. Class

```python
from dataclasses import dataclass, field
from typing import Any


@dataclass
class ZoneTickRuntime:
    source_state: dict[str, Any]
    profiler: Any | None = None

    dirty_agents: set[str] = field(default_factory=set)
    dirty_locations: set[str] = field(default_factory=set)
    dirty_traders: set[str] = field(default_factory=set)
    dirty_state_fields: set[str] = field(default_factory=set)

    def __post_init__(self) -> None:
        self.state = dict(self.source_state)
        self._agents_copied = False
        self._locations_copied = False
        self._traders_copied = False
        self._copied_agents: set[str] = set()
        self._copied_locations: set[str] = set()
        self._copied_traders: set[str] = set()

    def ensure_agents_map(self) -> dict:
        if not self._agents_copied:
            self.state["agents"] = dict(self.state.get("agents", {}))
            self._agents_copied = True
        return self.state["agents"]

    def agent(self, agent_id: str) -> dict:
        agents = self.ensure_agents_map()
        if agent_id not in agents:
            raise KeyError(f"Unknown agent {agent_id}")
        if agent_id not in self._copied_agents:
            agents[agent_id] = dict(agents[agent_id])
            self._copied_agents.add(agent_id)
        return agents[agent_id]

    def ensure_locations_map(self) -> dict:
        if not self._locations_copied:
            self.state["locations"] = dict(self.state.get("locations", {}))
            self._locations_copied = True
        return self.state["locations"]

    def location(self, location_id: str) -> dict:
        locations = self.ensure_locations_map()
        if location_id not in locations:
            raise KeyError(f"Unknown location {location_id}")
        if location_id not in self._copied_locations:
            locations[location_id] = dict(locations[location_id])
            self._copied_locations.add(location_id)
        return locations[location_id]

    def ensure_traders_map(self) -> dict:
        if not self._traders_copied:
            self.state["traders"] = dict(self.state.get("traders", {}))
            self._traders_copied = True
        return self.state["traders"]

    def trader(self, trader_id: str) -> dict:
        traders = self.ensure_traders_map()
        if trader_id not in traders:
            raise KeyError(f"Unknown trader {trader_id}")
        if trader_id not in self._copied_traders:
            traders[trader_id] = dict(traders[trader_id])
            self._copied_traders.add(trader_id)
        return traders[trader_id]
```

## 3.3. Dirty helpers

```python
def mark_agent_dirty(self, agent_id: str) -> None:
    self.dirty_agents.add(agent_id)

def mark_location_dirty(self, location_id: str) -> None:
    self.dirty_locations.add(location_id)

def mark_trader_dirty(self, trader_id: str) -> None:
    self.dirty_traders.add(trader_id)

def mark_state_dirty(self, field: str) -> None:
    self.dirty_state_fields.add(field)
```

## 3.4. Setter helpers

```python
def set_agent_field(self, agent_id: str, key: str, value) -> bool:
    agent = self.agent(agent_id)
    if agent.get(key) == value:
        return False
    agent[key] = value
    self.mark_agent_dirty(agent_id)
    return True
```

Add similar helpers:

```text
set_location_field
set_trader_field
set_state_field
```

---

# 4. Handling nested mutable structures

A shallow copy of an agent is not enough for nested mutable structures:

```text
inventory
equipment
scheduled_action
active_plan_v3
memory
memory_v3
brain_trace
```

Do not deep-copy everything by default. Copy nested structures only before mutating them.

Add methods:

```python
def mutable_agent_list(self, agent_id: str, key: str) -> list:
    agent = self.agent(agent_id)
    value = list(agent.get(key) or [])
    agent[key] = value
    self.mark_agent_dirty(agent_id)
    return value

def mutable_agent_dict(self, agent_id: str, key: str) -> dict:
    agent = self.agent(agent_id)
    value = dict(agent.get(key) or {})
    agent[key] = value
    self.mark_agent_dirty(agent_id)
    return value
```

Equivalent for locations/traders where needed.

Rules:

```text
Before appending to inventory → copy list.
Before editing scheduled_action → copy dict.
Before editing active_plan_v3 → copy dict.
Before editing memory_v3.records → copy nested dict.
```

---

# 5. Migration strategy

## 5.1. Add feature flag

In state/config:

```json
{
  "cpu_copy_on_write_enabled": true
}
```

Default for tests can be true after implementation. Keep fallback:

```python
if not state.get("cpu_copy_on_write_enabled", True):
    state = copy.deepcopy(state)
```

## 5.2. Do not rewrite everything in one commit

Recommended commit order:

```text
Commit 1:
  add ZoneTickRuntime and tests.

Commit 2:
  use runtime for world time, simple state fields.

Commit 3:
  migrate agent movement/travel.

Commit 4:
  migrate needs/hp/death.

Commit 5:
  migrate inventory/trade/equipment.

Commit 6:
  migrate active_plan/scheduled_action mutations.

Commit 7:
  remove default full deepcopy.
```

## 5.3. Keep fallback safety

If unconverted direct mutation exists, it can mutate source state. Use tests to find this.

---

# 6. Functions to audit

Audit and migrate mutation-heavy functions in:

```text
backend/app/games/zone_stalkers/rules/tick_rules.py
backend/app/games/zone_stalkers/decision/executors.py
backend/app/games/zone_stalkers/decision/active_plan_runtime.py
backend/app/games/zone_stalkers/decision/active_plan_composer.py
backend/app/games/zone_stalkers/decision/objectives/generator.py
backend/app/games/zone_stalkers/memory/*
backend/app/games/zone_stalkers/combat/*
```

Prioritize:

```text
agent movement
location agents list update
scheduled_action update
active_plan_v3 update
hp/death
needs
inventory/trade
memory add
hunt/debug fields
world time
```

---

# 7. Input immutability tests

Add:

```text
backend/tests/test_zone_stalkers_copy_on_write_runtime.py
```

Required tests:

```python
def test_tick_zone_map_does_not_mutate_input_state():
    ...

def test_copy_on_write_agent_mutation_does_not_mutate_original_agent():
    ...

def test_copy_on_write_location_mutation_does_not_mutate_original_location():
    ...

def test_inventory_mutation_copies_inventory_list():
    ...

def test_scheduled_action_mutation_copies_nested_dict():
    ...

def test_active_plan_mutation_copies_nested_dict():
    ...

def test_memory_v3_mutation_copies_records_container():
    ...
```

Important test pattern:

```python
old = make_state()
old_before = copy.deepcopy(old)

new_state, events = tick_zone_map(old)

assert old == old_before
assert new_state is not old
```

---

# 8. Gameplay regression tests

Run existing tests and add targeted ones:

```text
travel
sleep
explore
trade
death
emission
hunt target
combat if available
```

Required:

```python
def test_travel_arrival_still_moves_agent_and_updates_locations():
    ...

def test_death_still_removes_or_marks_agent_correctly():
    ...

def test_trade_still_updates_agent_and_trader_inventory():
    ...

def test_emission_still_interrupts_or_damages_agents():
    ...
```

---

# 9. Profiler acceptance

After this PR, profiler should show:

```text
deepcopy_ms near 0
copy_on_write_agents_count
copy_on_write_locations_count
copy_on_write_traders_count
```

Add profiler counters:

```text
cow_agents_copied
cow_locations_copied
cow_traders_copied
cow_nested_copies
```

---

# 10. Risks and mitigations

## Risk: hidden direct mutation

Mitigation:

```text
input immutability tests
focused audit of mutation paths
fallback feature flag
```

## Risk: nested structures shared accidentally

Mitigation:

```text
mutable_agent_list/dict helpers
tests for inventory/scheduled_action/active_plan/memory
```

## Risk: dirty-set misses a change

Mitigation:

```text
delta fallback still available
tests for common mutations
manual UI smoke test
```

---

# 11. Acceptance criteria

```text
[ ] tick_zone_map no longer performs full copy.deepcopy(state) by default.
[ ] Input state is not mutated.
[ ] Changed agents/locations/traders are copied on write.
[ ] Nested structures are copied before mutation.
[ ] Dirty-set remains correct.
[ ] Delta still works.
[ ] Gameplay regression tests pass.
[ ] Profiler shows deepcopy_ms reduced.
[ ] Feature flag can temporarily re-enable old deepcopy path.
```

---

# 12. Manual acceptance

Run:

```text
1. Start small simulation.
2. Enable auto tick.
3. Move NPC through route.
4. Open debug map.
5. Run kill target scenario.
6. Run get rich scenario.
7. Trigger emission.
8. Check no UI desync.
9. Check profiler: deepcopy_ms near 0.
```

This PR is complete only if behavior remains stable while full state copy is removed from hot path.
