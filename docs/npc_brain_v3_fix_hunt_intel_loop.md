# NPC Brain v3 — Fix Hunt Intel Loop: LOCATE_TARGET → ask_for_intel повторяется бесконечно

> Context:
>
> По логу NPC `Поцик 1` с целью:
>
> ```text
> global_goal = kill_stalker
> kill_target_id = agent_debug_2
> ```
>
> NPC застрял в цикле:
>
> ```text
> LOCATE_TARGET
> → ask_for_intel
> → ActivePlan completed
> → LOCATE_TARGET
> → ask_for_intel
> → ActivePlan completed
> ...
> ```
>
> При этом он уже купил информацию у торговца:
>
> ```text
> action_kind = intel_from_trader
> target_agent_id = agent_debug_2
> location_id = loc_G5
> summary = цель сейчас в «Южный блокпост»
> ```
>
> Но `TargetBelief.last_known_location_id` остался `null`, поэтому Brain v3 продолжил считать:
>
> ```text
> "Местоположение цели неизвестно — собираю разведданные"
> ```

---

# 1. Root cause

## 1.1. Информация покупается, но не становится TargetBelief

`ask_for_intel` / trader-intel записывает legacy memory примерно такого вида:

```json
{
  "action_kind": "intel_from_trader",
  "observed": "agent_location",
  "location_id": "loc_G5",
  "target_agent_id": "agent_debug_2",
  "source_agent_id": "trader_debug_0",
  "confidence": 0.69
}
```

Но `build_target_belief()` читает только memory_v3 records с kind:

```text
target_seen
target_last_known_location
target_intel
```

Запись `intel_from_trader` не учитывается как знание о цели.

## 1.2. legacy_bridge не мапит intel_from_trader в target_intel

В `memory/legacy_bridge.py` есть mappings для:

```text
target_seen
target_last_known_location
target_not_found
target_moved
target_equipment_seen
target_combat_strength_observed
target_death_confirmed
target_intel
```

Но нет mapping для:

```text
intel_from_trader
```

Поэтому bridge сохраняет запись как:

```text
kind = intel_from_trader
```

а не как:

```text
kind = target_intel
```

В результате `TargetBelief` её игнорирует.

## 1.3. LOCATE_TARGET не проверяет postcondition

`ActivePlan LOCATE_TARGET` считает `ask_for_intel` успешным, даже если после шага:

```text
TargetBelief.last_known_location_id == null
```

То есть сама операция разведки завершается “успешно” без результата.

## 1.4. Нет anti-loop guard

Если торговец не дал полезной информации или информация не распознана, NPC может каждый тик снова строить:

```text
LOCATE_TARGET → ask_for_intel
```

у того же источника, без cooldown/exhausted-source logic.

---

# 2. Expected behavior

После покупки информации у торговца:

```text
target_agent_id = agent_debug_2
location_id = loc_G5
confidence = 0.69
```

на следующем tick должно быть:

```text
TargetBelief:
  target_id = agent_debug_2
  is_known = true
  last_known_location_id = loc_G5
  location_confidence ≈ 0.69
  source_refs = [memory:<target_intel_record>]
```

Затем objective generation должна выбрать:

```text
TRACK_TARGET
```

а не снова:

```text
LOCATE_TARGET
```

План:

```text
ActivePlan TRACK_TARGET:
  1. travel_to_location loc_G5
  2. search_target
```

---

# 3. Required fix A — map intel_from_trader to target_intel in legacy_bridge

## File

```text
backend/app/games/zone_stalkers/memory/legacy_bridge.py
```

## Change

Add mapping:

```python
"intel_from_trader": (
    LAYER_SOCIAL,
    "target_intel",
    ("target", "intel", "social", "trader"),
),
```

Recommended placement: near the existing target/hunt mappings.

## Why target_intel, not target_last_known_location?

`intel_from_trader` is an intelligence report, not direct observation.

Canonical model:

```text
trader report → target_intel
direct sighting → target_seen / target_last_known_location
```

`TargetBelief` may still use `target_intel.location_id` as `last_known_location_id`, but source semantics remain correct.

## Required details

Ensure the resulting `MemoryRecord` has:

```text
kind = target_intel
location_id = effects.location_id
entity_ids contains target_agent_id
details.target_agent_id preserved
details.source_agent_id preserved
confidence preserved
tags include target/intel/trader
```

`_extract_entity_ids()` already supports:

```text
target_agent_id
source_agent_id
```

so mapping may be enough.

---

# 4. Required fix B — write canonical target_intel directly from ask_for_intel

## Files

Likely one or both:

```text
backend/app/games/zone_stalkers/rules/tick_rules.py
backend/app/games/zone_stalkers/decision/executors.py
```

Relevant functions:

```text
_bot_buy_hunt_intel_from_trader
_bot_ask_colocated_stalkers_about_agent
_exec_ask_for_intel
```

## Problem

Currently the system writes:

```text
intel_from_trader
```

but the hunt system should also get a canonical memory event.

## Required behavior

When trader/stalker provides target location intel, write canonical event:

```text
action_kind = target_intel
```

or, if we want to preserve direct tracking semantics:

```text
action_kind = target_last_known_location
source = intel
```

Recommended: write `target_intel`.

## Suggested memory write

```python
_add_memory(
    agent,
    world_turn,
    state,
    "observation",
    f"📍 Разведданные о цели: «{target_name}»",
    {
        "action_kind": "target_intel",
        "target_id": target_agent_id,
        "target_agent_id": target_agent_id,
        "target_agent_name": target_name,
        "location_id": intel_location_id,
        "source_agent_id": source_agent_id,
        "source_agent_name": source_name,
        "confidence": confidence,
        "price_paid": price_paid,
    },
    summary=(
        f"Получил сведения: цель «{target_name}» может быть в "
        f"«{location_name}»."
    ),
)
```

## Compatibility

Keep the old event if useful:

```text
intel_from_trader
```

but add canonical `target_intel`.

Alternative:

```text
Replace intel_from_trader with target_intel entirely.
```

Preferred for now:

```text
write both:
  intel_from_trader — human/story compatibility
  target_intel — Brain v3 reasoning
```

---

# 5. Required fix C — TargetBelief should support intel_from_trader alias defensively

## File

```text
backend/app/games/zone_stalkers/decision/target_beliefs.py
```

## Current behavior

TargetBelief location update checks kinds:

```python
if kind in {"target_seen", "target_last_known_location", "target_intel"} and rec_loc:
    ...
```

## Required change

Add alias:

```python
if kind in {
    "target_seen",
    "target_last_known_location",
    "target_intel",
    "intel_from_trader",
} and rec_loc:
    ...
```

## Why still do this if bridge is fixed?

Because old saves/logs may already contain `kind = intel_from_trader`.

This makes the system migration-safe.

## Also support details fields

Make sure target id matching supports:

```python
details.get("target_id")
details.get("target_agent_id")
```

and location supports:

```python
rec.get("location_id")
details.get("location_id")
details.get("target_location_id")
```

Recommended helper:

```python
def _target_id_from_details(details: dict[str, Any]) -> str:
    return str(
        details.get("target_id")
        or details.get("target_agent_id")
        or details.get("agent_id")
        or ""
    )

def _location_from_record(rec: dict[str, Any], details: dict[str, Any]) -> str | None:
    return (
        rec.get("location_id")
        or details.get("location_id")
        or details.get("target_location_id")
        or details.get("last_known_location_id")
    )
```

---

# 6. Required fix D — LOCATE_TARGET postcondition

## Problem

`ask_for_intel` can complete even if no useful intel was produced.

## Required behavior

`LOCATE_TARGET` should be considered successful only if, after the relevant step:

```text
TargetBelief.last_known_location_id != null
```

or memory_v3 contains:

```text
target_intel
target_last_known_location
target_seen
```

for this `kill_target_id`.

## Where to implement

Possible options:

### Option A — in executor

In `_exec_ask_for_intel`, after asking/buying:

```python
if no intel was written:
    step.payload["_hunt_intel_failed"] = True
```

Then ActivePlan runtime can mark step failed.

### Option B — in ActivePlan runtime

After completing `STEP_ASK_FOR_INTEL` for objective `LOCATE_TARGET`, rebuild TargetBelief and verify postcondition.

### Option C — in plan monitor/repair

If next decision sees same `LOCATE_TARGET` immediately after completed `LOCATE_TARGET`, detect no-progress loop and fail/repair.

Recommended:

```text
A + C:
- executor marks whether intel was obtained;
- objective generator/repair has anti-loop guard.
```

## Suggested executor result

When intel found:

```python
step.payload["_intel_found"] = True
step.payload["_intel_location_id"] = intel_location_id
```

When not found:

```python
step.payload["_intel_found"] = False
step.payload["_intel_failure_reason"] = "no_source_or_no_intel"
```

Then `execute_plan_step()` should still complete the step, but memory/ActivePlan should know whether it produced useful result.

For stricter behavior:

```text
If _intel_found is False:
  active_plan_step_failed
  reason = no_intel_found
  repair = try another intel source / fallback search
```

---

# 7. Required fix E — anti-loop guard for repeated LOCATE_TARGET ask_for_intel

## Problem

The log shows repeated one-step plans:

```text
LOCATE_TARGET:
  ask_for_intel
completed
LOCATE_TARGET:
  ask_for_intel
completed
...
```

## Required rule

Do not ask the same source about the same target every tick.

Add cooldown:

```text
same agent
same target_id
same source_agent_id
same location_id
same action_kind target_intel / intel_from_trader / no_intel_found
within N turns
→ source considered recently used
```

Recommended cooldown:

```text
HUNT_INTEL_SOURCE_COOLDOWN_TURNS = 180
```

or shorter for tests:

```text
30–60 turns
```

## Required memory event for failed source

If no intel was obtained:

```text
action_kind = target_intel_source_exhausted
target_id = ...
source_agent_id = ...
location_id = current_location
reason = no_intel
```

Map this to memory_v3:

```text
kind = target_intel_source_exhausted
layer = social
tags = target/intel/exhausted
```

## Objective generation behavior

When `LOCATE_TARGET` is considered but recent same-source intel attempt exists:

```text
- choose another known intel source if available;
- otherwise choose fallback search objective;
- otherwise wait only with cooldown, not every tick.
```

Possible fallback objectives:

```text
SEARCH_INFORMATION
TRACK_TARGET if low-confidence location exists
HUNT_TARGET generic only if no other option
IDLE with cooldown reason
```

Do not produce immediate `LOCATE_TARGET → ask_for_intel` again.

---

# 8. Required fix F — LOCATE_TARGET plan should avoid one-step ask_for_intel if already at exhausted source

## Current observed behavior

After first real plan:

```text
LOCATE_TARGET:
  travel_to_location
  ask_for_intel
```

subsequent plans became:

```text
LOCATE_TARGET:
  ask_for_intel
```

because NPC was already at trader.

That is fine only if there is a new source or meaningful new query.

## Required planner behavior

If NPC is at a trader and target intel is already known:

```text
do not plan ask_for_intel
TRACK_TARGET should win
```

If NPC is at a trader and recently asked same trader:

```text
do not plan ask_for_intel again
```

If no intel known and source not exhausted:

```text
ask_for_intel is valid
```

## Suggested planner guard

In hunt planning / `_plan_hunt_target`:

```python
if objective_key == "LOCATE_TARGET":
    if target_belief.last_known_location_id:
        # Should not be LOCATE anymore.
        return None or TRACK_TARGET-like plan
    if _recently_asked_same_intel_source(...):
        return None
```

The objective generator should ideally prevent `LOCATE_TARGET`, but planner should be defensive too.

---

# 9. Required fix G — use target-specific memories in memory_used

## Problem

In the stuck log, `memory_used` for `LOCATE_TARGET` mostly contains ActivePlan lifecycle entries:

```text
active_plan_step_completed
active_plan_completed
active_plan_step_started
active_plan_created
```

It does not show the useful intelligence memory.

## Required behavior

For hunt objectives, `memory_used` should include target-related memories first:

```text
target_intel
target_last_known_location
target_seen
target_not_found
target_moved
target_death_confirmed
```

## Fix

When building objective candidates for hunt, include `TargetBelief.source_refs` in objective `source_refs`.

For example:

```python
source_refs = (
    "global_goal:kill_stalker",
    *target_belief.source_refs,
)
```

Then `create_active_plan()` will extract `memory_refs`, and debug UI will show relevant target memories.

## Also filter generic active_plan lifecycle noise

Do not let recent ActivePlan lifecycle records dominate `memory_used` for target reasoning.

Possible rule:

```text
For hunt objectives, prefer memory kinds:
  target_*
  intel_*
  combat_*
  global_goal_*
over:
  active_plan_*
```

---

# 10. Required tests

## 10.1. bridge maps intel_from_trader to target_intel

```python
def test_intel_from_trader_bridges_to_target_intel_memory_v3():
    ...
```

Setup legacy memory entry:

```python
{
    "effects": {
        "action_kind": "intel_from_trader",
        "target_agent_id": "target_1",
        "location_id": "loc_target",
        "source_agent_id": "trader_1",
        "confidence": 0.69,
    }
}
```

Expected memory_v3 record:

```text
kind = target_intel
location_id = loc_target
entity_ids includes target_1
tags include target/intel/trader
confidence = 0.69
```

## 10.2. TargetBelief reads trader intel

```python
def test_target_belief_reads_trader_intel_as_last_known_location():
    ...
```

Setup memory_v3:

```text
kind = target_intel
details.target_agent_id = target_1
location_id = loc_target
confidence = 0.69
```

Expected:

```text
belief.last_known_location_id == loc_target
belief.location_confidence == 0.69
belief.source_refs contains memory:<record_id>
```

## 10.3. TargetBelief reads old intel_from_trader alias

```python
def test_target_belief_reads_legacy_intel_from_trader_alias():
    ...
```

Setup memory_v3:

```text
kind = intel_from_trader
details.target_agent_id = target_1
location_id = loc_target
```

Expected:

```text
belief.last_known_location_id == loc_target
```

## 10.4. ask_for_intel writes canonical target_intel

```python
def test_ask_for_intel_writes_target_intel_memory():
    ...
```

Setup:

```text
hunter co-located with trader
target exists at loc_target
hunter.kill_target_id = target_1
hunter.money >= HUNT_INTEL_PRICE
```

Execute `STEP_ASK_FOR_INTEL`.

Expected:

```text
legacy memory has action_kind=target_intel
or memory_v3 has kind=target_intel
target location captured
```

## 10.5. after intel, LOCATE_TARGET becomes TRACK_TARGET

```python
def test_after_buying_intel_next_objective_is_track_target():
    ...
```

Scenario:

```text
turn 1:
  LOCATE_TARGET / ask_for_intel writes target_intel

turn 2:
  build_target_belief sees location
  generate objectives
```

Expected:

```text
TRACK_TARGET exists and scores above LOCATE_TARGET
LOCATE_TARGET is not selected
```

## 10.6. no repeated ask_for_intel with same source

```python
def test_locate_target_does_not_repeat_same_intel_source_every_tick():
    ...
```

Setup:

```text
hunter at trader
no useful intel returned
target_intel_source_exhausted written
```

Run next tick.

Expected:

```text
does not create another LOCATE_TARGET ask_for_intel with same source
```

## 10.7. regression test from Poцик 1 log pattern

```python
def test_hunt_intel_loop_regression_poцик_1():
    ...
```

Minimal reproduction:

```text
hunter global_goal = kill_stalker
target id known
hunter at trader
ask_for_intel writes intel_from_trader with target_agent_id and location_id
next tick should choose TRACK_TARGET
```

Expected:

```text
not LOCATE_TARGET
active_plan_v3.objective_key == TRACK_TARGET
first/next step target_id == loc_G5
```

---

# 11. Acceptance criteria

This bug is fixed when:

```text
[ ] intel_from_trader maps to target_intel in memory_v3.
[ ] ask_for_intel writes canonical target_intel or target_last_known_location.
[ ] TargetBelief reads target_intel and legacy intel_from_trader.
[ ] After intel is bought, last_known_location_id is not null.
[ ] Next selected objective becomes TRACK_TARGET, not LOCATE_TARGET.
[ ] ActivePlan TRACK_TARGET travels to intel location.
[ ] Repeated same-source ask_for_intel loop is prevented.
[ ] memory_used for hunt objectives includes target intel/source refs.
[ ] Regression test reproduces Поцик 1 pattern and passes.
```

---

# 12. Expected fixed log pattern

Before fix:

```text
2263: intel_from_trader loc_G5
2264: LOCATE_TARGET ask_for_intel
2265: LOCATE_TARGET ask_for_intel
2266: LOCATE_TARGET ask_for_intel
...
hunt_target_belief.last_known_location_id = null
```

After fix:

```text
2263:
  target_intel / intel_from_trader
  target_id = agent_debug_2
  location_id = loc_G5

2264:
  hunt_target_belief.last_known_location_id = loc_G5
  hunt_target_belief.location_confidence = 0.69
  source_refs = [memory:<target_intel>]

ObjectiveDecision:
  TRACK_TARGET

ActivePlan TRACK_TARGET:
  1. travel_to_location loc_G5
  2. search_target
```

No repeated `LOCATE_TARGET → ask_for_intel` loop.
