# NPC Brain v3 — Targeted PR 1 / PR 2 fixes: emission wake-up and death cleanup

> Branch checked: `copilot/npc-brain-v3-pr-2`  
> Context: logs from end of PR 1 / beginning of PR 2 showed two concrete issues:
>
> 1. NPC can die from emission while still sleeping after an emission warning.
> 2. Dead NPC can keep `scheduled_action`.
>
> Scope of this document is intentionally narrow.  
> Do NOT use this document to make decisions about:
>
> - old `sleep-abort-loop` logs;
> - resupply/liquidity behavior;
> - purchase scoring;
> - PR 2 ImmediateNeed/ItemNeed logic;
> - PR 3 memory architecture.

---

## 1. Confirmed current behavior

### 1.1. Emission warning while NPC is sleeping

In the `Поцик 2` log, NPC had selected sleep, then received an emission warning:

```text
world_turn = 99154
title = "⚠️ Скоро выброс!"
turns_until = 13
emission_scheduled_turn = 99167
```

Then at the scheduled emission turn:

```text
world_turn = 99167
title = "💀 Смерть"
cause = "emission"
terrain = "plain"
summary = "Погиб от выброса..."
```

At the same time, `brain_trace` still showed:

```text
current_thought = "Продолжаю sleep — action_still_valid."
decision = continue
scheduled_action_type = sleep
```

Meaning:

```text
PlanMonitor did not consider emission threat a reason to interrupt sleep.
```

### 1.2. Dead NPC can keep scheduled_action

In the same final state:

```text
is_alive = false
scheduled_action = {
  "type": "sleep",
  ...
}
```

That is an invalid state invariant.

Expected invariant:

```text
If is_alive == false:
  scheduled_action == None
  action_queue == []
```

Later, with `ActivePlan`, the invariant becomes:

```text
active_plan_v3.status in ("aborted", "failed") or active_plan_v3 == None
```

But for PR 1 / PR 2, clearing `scheduled_action` and `action_queue` is enough.

---

## 2. Current code status in PR 2 branch

### 2.1. `PlanMonitor` only checks hp/thirst/hunger

Current `assess_scheduled_action_v3()` checks:

```text
critical_hp
critical_thirst
critical_hunger
```

It does not check:

```text
emission_active
emission_imminent memory
dangerous terrain
safe shelter state
```

Therefore it can return:

```text
continue / action_still_valid
```

for `sleep` while an emission is imminent.

### 2.2. `_is_emission_threat()` already exists

`tick_rules.py` already has helper:

```python
def _is_emission_threat(agent, state) -> bool:
    ...
```

It detects:

```text
state["emission_active"] == true
or latest emission_imminent memory not superseded by emission_ended
```

So the project already has part of the needed logic.

### 2.3. Emergency interrupt currently does not cover sleep

`_process_scheduled_action()` has an emergency interrupt for long-running actions with emission threat, but it currently targets travel/exploration style actions, not sleep.

This explains why sleeping NPC can continue sleeping through warning turns.

### 2.4. Death cleanup is not centralized

There are multiple death paths:

```text
starvation_or_thirst
emission
combat / other damage paths
```

But cleanup of runtime state is not guaranteed in one place.

We need one helper and use it everywhere an agent dies.

---

## 3. Desired behavior

### 3.1. Sleeping during emission warning

If a bot stalker is sleeping and receives an `emission_imminent` warning or emission becomes active:

```text
sleep must be interrupted
scheduled_action must be cleared
action_queue must be cleared if relevant
decision pipeline should run on same tick if possible
```

Expected next decision:

```text
if current location is shelter:
  wait_in_shelter

if current location is dangerous terrain:
  flee_emission / travel to shelter
```

### 3.2. Emergency flee must remain protected

If the current scheduled action is already:

```text
emergency_flee == true
```

it must not be interrupted by the emission logic.

This is already a PR 1 invariant and must remain true.

### 3.3. Partial sleep effect must be preserved

If NPC slept for some intervals before emission warning:

```text
sleep_intervals_applied must remain meaningful
brain_trace/memory should mention partial sleep if sleep is interrupted
```

Do not roll back partial sleep effects.

### 3.4. Dead agent cleanup

Any death should result in:

```python
agent["is_alive"] = False
agent["scheduled_action"] = None
agent["action_queue"] = []
agent["action_used"] = False
```

Do not run bot decisions for dead agents.

---

## 4. Recommended implementation approach

There are two possible implementation points.

### Option A — Add emission check to `PlanMonitor`

Pros:

```text
- consistent with PR 1 "monitor active scheduled_action every tick";
- brain_trace uses plan_monitor mode;
- same abort event format as hunger/thirst/hp.
```

Cons:

```text
- `PlanMonitor` is in decision package and currently does not know terrain/shelter semantics.
```

### Option B — Extend `_process_scheduled_action()` emergency interrupt to include sleep

Pros:

```text
- `_is_emission_threat()` already exists in tick_rules.py;
- terrain/emission logic already lives there;
- minimal change.
```

Cons:

```text
- bypasses `PlanMonitor` reason taxonomy unless trace/memory is added manually.
```

### Recommended for now

Use a hybrid minimal approach:

```text
1. Keep _is_emission_threat() in tick_rules.py.
2. Add sleep to the scheduled-action emergency interrupt.
3. Write PlanMonitor-compatible trace/memory/event when sleep is interrupted by emission.
4. Later PR 5 can move this into ActivePlan continuity.
```

This keeps scope small and avoids moving terrain/emission logic into `plan_monitor.py` prematurely.

---

## 5. Patch 1 — interrupt sleep on emission threat

### 5.1. Update scheduled action emergency interrupt

In `_process_scheduled_action()` or just before calling it, make sure this condition covers sleep:

```python
if (
    action_type in ("explore_anomaly_location", "travel", "sleep")
    and not sched.get("emergency_flee")
    and _is_emission_threat(agent, state)
):
    ...
```

### 5.2. If current location is already safe

If location is safe shelter terrain, interruption still makes sense:

```text
sleep should be interrupted
next decision should likely choose wait_in_shelter
```

Why interrupt even in shelter?

```text
because emission is a high-priority world event;
NPC should not sleep through it unless we explicitly design "sleep through safe emission" later.
```

For now, safer behavior is:

```text
wake up → wait_in_shelter
```

### 5.3. Preserve emergency flee

Do not interrupt:

```python
sched.get("emergency_flee") == True
```

This avoids cancel/reschedule loops.

---

## 6. Patch 2 — trace/memory/event for emission sleep interrupt

When sleep is interrupted because of emission threat, write a trace similar to PlanMonitor abort:

```text
mode = plan_monitor
decision = abort
reason = emission_threat
scheduled_action_type = sleep
summary = "Прерываю sleep из-за угрозы выброса. Успел поспать X ч."
```

### Event

Emit:

```json
{
  "event_type": "plan_monitor_aborted_action",
  "payload": {
    "agent_id": "...",
    "scheduled_action_type": "sleep",
    "reason": "emission_threat",
    "dominant_pressure": {
      "key": "emission",
      "value": 1.0
    },
    "current_location_id": "...",
    "turns_remaining": 123,
    "sleep_intervals_applied": 4,
    "sleep_progress_turns": 12
  }
}
```

### Memory

Add dedup-protected memory:

```text
action_kind = plan_monitor_abort
reason = emission_threat
scheduled_action_type = sleep
dominant_pressure = {key: emission, value: 1.0}
```

Dedup signature:

```python
{
    "reason": "emission_threat",
    "scheduled_action_type": "sleep",
    "cancelled_final_target": None,
}
```

Use existing `should_write_plan_monitor_memory_event()`.

---

## 7. Patch 3 — centralize death cleanup

Add helper in `tick_rules.py`:

```python
def _mark_agent_dead(
    *,
    agent_id: str,
    agent: dict,
    state: dict,
    world_turn: int,
    cause: str,
    memory_title: str,
    memory_effects: dict,
    memory_summary: str,
    events: list[dict],
) -> None:
    agent["is_alive"] = False
    agent["scheduled_action"] = None
    agent["action_queue"] = []
    agent["action_used"] = False

    _add_memory(
        agent,
        world_turn,
        state,
        "observation",
        memory_title,
        memory_effects,
        summary=memory_summary,
    )

    events.append({
        "event_type": "agent_died",
        "payload": {
            "agent_id": agent_id,
            "cause": cause,
        },
    })
```

Then replace direct death code in:

```text
starvation_or_thirst death path
emission death path
any other local death path in tick_rules.py if present
```

### Important

If death happens during scheduled action, cleanup should remove it immediately.

### Future PR 5 note

Later, with `ActivePlan`, this helper should update:

```python
agent["active_plan_v3"]["status"] = "aborted" or "failed"
```

But do not add this now unless active plan lifecycle exists.

---

## 8. Patch 4 — prevent brain_trace from saying continue after death

If the agent dies during the tick:

```text
brain_trace should not remain as:
  "Продолжаю sleep — action_still_valid"
```

Minimum options:

### Option A

When `_mark_agent_dead()` is called, overwrite trace:

```python
agent["brain_trace"] = {
    "schema_version": 1,
    "turn": world_turn,
    "world_time": ...,
    "mode": "system",
    "current_thought": "Погиб; дальнейшие решения не принимаются.",
    "events": [
        {
            "turn": world_turn,
            "world_time": ...,
            "mode": "system",
            "decision": "no_op",
            "summary": memory_summary,
            "reason": cause,
        }
    ],
}
```

### Option B

Append a system death event to existing trace.

Recommended:

```text
Option B for now, because it preserves previous context.
```

But make sure `current_thought` is updated:

```text
"Погиб; дальнейшие решения не принимаются."
```

---

## 9. Tests to add

### 9.1. Sleep interrupted by emission warning

```python
def test_sleep_is_interrupted_by_emission_warning():
    state = make_state_with_sleeping_bot_on_plain()
    state["emission_scheduled_turn"] = state["world_turn"] + 10

    # Add emission_imminent memory manually or advance until warning.
    bot["memory"].append({
        "world_turn": state["world_turn"],
        "type": "observation",
        "title": "⚠️ Скоро выброс!",
        "effects": {
            "action_kind": "emission_imminent",
            "turns_until": 10,
            "emission_scheduled_turn": state["world_turn"] + 10,
        },
    })

    new_state, events = tick_zone_map(state)

    bot = new_state["agents"][bot_id]
    assert bot["scheduled_action"] is None
    assert any(e["event_type"] == "plan_monitor_aborted_action" for e in events)
    assert bot["brain_trace"]["events"][-1]["reason"] == "emission_threat"
```

### 9.2. Emergency flee not interrupted

```python
def test_emergency_flee_not_interrupted_by_emission_warning():
    bot["scheduled_action"] = {
        "type": "travel",
        "turns_remaining": 5,
        "turns_total": 10,
        "emergency_flee": True,
    }
    add_emission_imminent_memory(bot)

    new_state, events = tick_zone_map(state)

    assert new_state["agents"][bot_id]["scheduled_action"] is not None
```

### 9.3. Emission death clears scheduled action

```python
def test_emission_death_clears_scheduled_action():
    bot["scheduled_action"] = {
        "type": "sleep",
        "turns_remaining": 100,
        "turns_total": 360,
    }
    state["emission_active"] = False
    state["emission_scheduled_turn"] = state["world_turn"]
    bot["location_id"] = dangerous_plain_location

    new_state, events = tick_zone_map(state)

    bot = new_state["agents"][bot_id]
    assert bot["is_alive"] is False
    assert bot["scheduled_action"] is None
    assert bot["action_queue"] == []
```

### 9.4. Starvation/thirst death clears scheduled action

```python
def test_survival_death_clears_scheduled_action():
    bot["hp"] = 1
    bot["hunger"] = 100
    bot["thirst"] = 100
    bot["scheduled_action"] = {
        "type": "sleep",
        "turns_remaining": 100,
        "turns_total": 360,
    }

    # Set world_minute so survival degradation applies.
    state["world_minute"] = 59

    new_state, events = tick_zone_map(state)

    bot = new_state["agents"][bot_id]
    assert bot["is_alive"] is False
    assert bot["scheduled_action"] is None
    assert bot["action_queue"] == []
```

### 9.5. Death trace is not continue sleep

```python
def test_death_updates_brain_trace_from_continue_to_system_death():
    ...
    assert "Погиб" in bot["brain_trace"]["current_thought"]
    assert bot["brain_trace"]["mode"] == "system"
```

---

## 10. Acceptance criteria

This targeted fix is complete when:

```text
[ ] Sleeping bot wakes/aborts when emission_imminent memory exists.
[ ] Sleeping bot wakes/aborts when emission_active is true.
[ ] Emergency flee remains uninterruptible.
[ ] Emission-dead agent has scheduled_action=None and action_queue=[].
[ ] Starvation/thirst-dead agent has scheduled_action=None and action_queue=[].
[ ] BrainTrace does not claim "continue sleep" after death.
[ ] Existing PR 1 sleep tests still pass.
[ ] Existing PR 2 immediate/item/liquidity tests still pass.
```

---

## 11. What not to change in this patch

Do not change:

```text
- purchase scoring;
- liquidity policy;
- ImmediateNeed / ItemNeed behavior;
- old sleep-abort-loop historical log interpretation;
- PR 3 memory architecture;
- PR 4 Objective scoring;
- PR 5 ActivePlan.
```

This patch is only about:

```text
emission interruption of sleep
death cleanup
brain_trace consistency on death
```

---

## 12. PR ownership

### PR 1-level fix

```text
sleep interrupted by emission warning
sleep interrupted by active emission
PlanMonitor/scheduled-action safety
death cleanup for scheduled_action
```

### PR 2-level compatibility

```text
ensure PR 2 branch keeps these guarantees while adding ImmediateNeed/ItemNeed/liquidity
```

### PR 5 future improvement

Later, when `ActivePlan` exists:

```text
emission threat should pause/abort ActivePlan,
not merely clear scheduled_action.
```

But do not wait for PR 5 to fix current death/sleep inconsistency.
