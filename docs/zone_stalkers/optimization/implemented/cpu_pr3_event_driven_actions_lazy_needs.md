# CPU Optimization PR 3 — Event-Driven Actions and Lazy Needs

> ⚠️ Archived PR document.
> Superseded by [`../cpu_optimization_applied_pr1_pr5.md`](../cpu_optimization_applied_pr1_pr5.md).

> Base:
>
> ```text
> after CPU Optimization PR 2 — Copy-on-Write Runtime
> ```
>
> Goal:
>
> ```text
> Stop polling long actions and needs every tick.
> ```
>
> This PR changes the time model for long actions and needs, so it must be tested carefully.

---

# 1. Problem

Many NPC actions are long-running:

```text
travel
sleep
explore
wait
trade
active_plan step delays
```

Current model often requires per-tick updates:

```text
turns_remaining -= 1
needs += rate
sleepiness += rate
```

For many NPCs, this creates unnecessary CPU work.

Target model:

```text
- long actions complete through scheduled due tasks;
- needs are computed lazily from base value + last update turn;
- critical thresholds are scheduled as events/tasks.
```

---

# 2. Scope

## In scope

```text
1. scheduled_tasks runtime/state model.
2. Convert travel/sleep/explore/wait to started_turn/ends_turn.
3. Due task processing.
4. Backward migration from turns_remaining.
5. Lazy needs_state.
6. Need threshold tasks.
7. Critical need damage tasks.
8. Interruptions by emission/combat/urgent events.
9. Tests.
```

## Out of scope

Do not do here:

```text
- brain invalidation/AI budget;
- large decision queue;
- pathfinding rewrite;
- removing more state structures;
- changing balance rates unless necessary.
```

---

# 3. Scheduled tasks

## 3.1. State shape

Add:

```json
{
  "scheduled_tasks": {
    "12345": [
      {
        "kind": "travel_arrival",
        "agent_id": "agent_1",
        "scheduled_action_revision": 3
      }
    ]
  }
}
```

Keys are strings because JSON.

## 3.2. Helpers

Add file:

```text
backend/app/games/zone_stalkers/runtime/scheduler.py
```

API:

```python
def schedule_task(state: dict, runtime, turn: int, task: dict) -> None:
    tasks = state.setdefault("scheduled_tasks", {})
    tasks.setdefault(str(turn), []).append(task)
    runtime.mark_state_dirty("scheduled_tasks")


def pop_due_tasks(state: dict, runtime, world_turn: int) -> list[dict]:
    tasks = state.setdefault("scheduled_tasks", {}).pop(str(world_turn), [])
    if tasks:
        runtime.mark_state_dirty("scheduled_tasks")
    return tasks


def cleanup_old_tasks(state: dict, runtime, current_turn: int, max_age: int = 1000) -> None:
    ...
```

## 3.3. Task revisions

Every scheduled action should include a revision token:

```json
{
  "scheduled_action_revision": 7
}
```

If task fires but agent current revision differs:

```text
ignore stale task
```

This prevents old sleep/travel completion tasks from firing after interruption.

---

# 4. New scheduled_action shape

## 4.1. Travel

Old:

```json
{
  "type": "travel",
  "target_id": "loc_B",
  "turns_remaining": 12,
  "turns_total": 30,
  "route": ["loc_A", "loc_X", "loc_B"]
}
```

New:

```json
{
  "type": "travel",
  "target_id": "loc_B",
  "started_turn": 100,
  "ends_turn": 130,
  "turns_total": 30,
  "route": ["loc_A", "loc_X", "loc_B"],
  "interruptible": true,
  "revision": 3
}
```

UI derives:

```python
turns_remaining = max(0, ends_turn - world_turn)
progress = (world_turn - started_turn) / turns_total
```

## 4.2. Sleep

```json
{
  "type": "sleep",
  "started_turn": 100,
  "ends_turn": 160,
  "turns_total": 60,
  "interruptible": true,
  "revision": 4
}
```

Sleep may also need interval tasks if recovery applies gradually:

```text
sleep_tick every 10 turns
sleep_complete at ends_turn
```

## 4.3. Explore/wait/trade

Same pattern:

```text
started_turn
ends_turn
turns_total
revision
interruptible
```

---

# 5. Due task processing

At tick:

```python
due_tasks = pop_due_tasks(state, runtime, world_turn)
for task in due_tasks:
    process_due_task(state, runtime, task, world_turn)
```

Task kinds:

```text
travel_arrival
sleep_tick
sleep_complete
explore_complete
wait_complete
trade_complete
need_threshold_crossed
need_damage
active_plan_step_complete
```

---

# 6. Backward compatibility migration

At tick start or state migration:

```python
def migrate_scheduled_action(agent: dict, world_turn: int) -> bool:
    action = agent.get("scheduled_action")
    if not action:
        return False
    if "ends_turn" in action:
        return False
    turns_remaining = int(action.get("turns_remaining", 0))
    turns_total = int(action.get("turns_total", max(1, turns_remaining)))
    action["started_turn"] = world_turn - max(0, turns_total - turns_remaining)
    action["ends_turn"] = world_turn + turns_remaining
    action["revision"] = int(action.get("revision", 0)) + 1
    return True
```

If migrated, schedule completion task.

---

# 7. Interruptions

Long actions must be interruptible by urgent events.

## 7.1. Helper

```python
def interrupt_action(agent: dict, runtime, reason: str) -> bool:
    action = agent.get("scheduled_action")
    if not action:
        return False
    if not action.get("interruptible", True):
        return False

    action["revision"] = int(action.get("revision", 0)) + 1
    agent["scheduled_action"] = None
    invalidate_brain_if_available(agent, runtime, reason)
    runtime.mark_agent_dirty(agent["id"])
    return True
```

## 7.2. Emission

When emission warning starts:

```text
for alive agents not in safe location:
  interrupt action
  force urgent shelter decision
```

This is O(N) only when emission warning happens.

## 7.3. Combat/location event

When combat starts in location:

```text
for agents in location.agents:
  interrupt action if needed
  invalidate/alert
```

## 7.4. Critical needs

When a critical threshold task fires:

```text
interrupt action
force need/survival decision
```

---

# 8. Lazy needs model

## 8.1. State shape

Add per agent:

```json
{
  "needs_state": {
    "hunger": {
      "base": 20,
      "updated_turn": 1000
    },
    "thirst": {
      "base": 30,
      "updated_turn": 1000
    },
    "sleepiness": {
      "base": 10,
      "updated_turn": 1000
    },
    "revision": 1
  }
}
```

Keep legacy fields for projections/UI:

```text
agent.hunger
agent.thirst
agent.sleepiness
```

but treat them as materialized snapshots.

## 8.2. Helpers

Add file:

```text
backend/app/games/zone_stalkers/needs/lazy_needs.py
```

API:

```python
def get_need(agent: dict, need_key: str, world_turn: int) -> float:
    ...

def materialize_needs(agent: dict, world_turn: int) -> dict[str, float]:
    ...

def set_need(agent: dict, need_key: str, value: float, world_turn: int) -> None:
    ...

def schedule_need_thresholds(state: dict, runtime, agent_id: str, agent: dict, world_turn: int) -> None:
    ...
```

## 8.3. Migration

```python
def ensure_needs_state(agent: dict, world_turn: int) -> bool:
    if "needs_state" in agent:
        return False
    agent["needs_state"] = {
        "hunger": {"base": float(agent.get("hunger", 0)), "updated_turn": world_turn},
        "thirst": {"base": float(agent.get("thirst", 0)), "updated_turn": world_turn},
        "sleepiness": {"base": float(agent.get("sleepiness", 0)), "updated_turn": world_turn},
        "revision": 1,
    }
    return True
```

## 8.4. Threshold scheduling

Thresholds:

```text
soft hunger/thirst/sleepiness
critical hunger/thirst/sleepiness
damage thresholds
```

Use revision tokens:

```json
{
  "kind": "need_threshold_crossed",
  "agent_id": "agent_1",
  "need": "thirst",
  "threshold": "critical",
  "needs_revision": 3
}
```

When task fires:

```text
if task.needs_revision != agent.needs_state.revision:
  ignore stale task
else:
  materialize need
  trigger invalidation/interruption
```

## 8.5. Critical damage

When need is critical:

```text
schedule recurring need_damage task
```

Task:

```json
{
  "kind": "need_damage",
  "agent_id": "agent_1",
  "need": "thirst",
  "needs_revision": 3
}
```

If agent drinks/eats/sleeps and revision changes:

```text
old damage tasks become stale
```

---

# 9. Projection and delta compatibility

Projections should still expose:

```text
hunger
thirst
sleepiness
```

as numbers.

When building projection/delta:

```python
materialized = materialize_needs(agent, world_turn)
```

But do not write materialized values back unless needed. For projection, compute only.

Delta should include need values only if:

```text
- need changed enough since last visible value;
- threshold crossed;
- agent selected/profile open;
- scheduled need task fired.
```

Minimal approach:

```text
include derived needs in agent delta when agent is dirty.
```

---

# 10. Tests

Add:

```text
backend/tests/test_zone_stalkers_scheduled_tasks.py
backend/tests/test_zone_stalkers_lazy_needs.py
```

Required tests:

```python
def test_travel_arrival_happens_at_ends_turn():
    ...

def test_travel_no_longer_decrements_turns_remaining_each_tick():
    ...

def test_legacy_turns_remaining_migrates_to_ends_turn():
    ...

def test_stale_scheduled_task_is_ignored_after_interruption():
    ...

def test_emission_interrupts_travel():
    ...

def test_lazy_need_value_increases_with_world_turn():
    ...

def test_drinking_resets_thirst_and_invalidates_old_threshold_tasks():
    ...

def test_critical_need_damage_task_damages_agent():
    ...

def test_projection_still_exposes_hunger_thirst_sleepiness():
    ...
```

Regression:

```text
get_rich E2E
kill_target E2E
emission survival tests
sleep tests
travel tests
```

---

# 11. Acceptance criteria

```text
[ ] Long actions store started_turn/ends_turn/revision.
[ ] Due tasks process completions.
[ ] Travel/sleep/explore/wait no longer require per-tick countdown updates.
[ ] UI can still show turns_remaining/progress.
[ ] Old saves/actions migrate.
[ ] Interruptions work.
[ ] Needs are computed lazily.
[ ] Need thresholds/damage are scheduled tasks.
[ ] Eating/drinking/sleeping still works.
[ ] Critical needs still affect NPC.
[ ] Existing hunt logic still works.
[ ] CPU work per tick drops when many NPCs are in long actions.
```

---

# 12. Manual acceptance

Run scenarios:

```text
1. 10 NPC traveling.
   Expected: tick does not touch all travel countdowns every tick.

2. NPC sleeping during emission warning.
   Expected: interrupted and seeks shelter.

3. NPC drinking before critical thirst.
   Expected: stale threshold/damage tasks ignored.

4. Hunter searching target.
   Expected: hunt still works.

5. Get-rich NPC.
   Expected: travel/explore/sell/eat/drink still works.
```

This PR is complete when time-based behavior remains correct while per-tick polling for actions/needs is removed.
