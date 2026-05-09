# NPC Brain v3 — Hunt/Kill Stalker Goal Implementation Plan

> Branch context: after PR5 and post-PR5 log fixes.
>
> Goal:
>
> ```text
> Implement the system-level kill-stalker operation from
> docs/npc_brain_v3/07_post_pr5_kill_stalker_goal.md
> and verify how Brain v3 behaves on a complex long-running hostile objective.
> ```
>
> Current status:
>
> ```text
> Brain v3 infrastructure is now suitable for this task:
> - ObjectiveDecision exists;
> - ActivePlanV3 exists;
> - scheduled_action is a child runtime step;
> - action_queue is no longer the source of truth;
> - memory_v3 exists;
> - combat_readiness exists;
> - hunt objective placeholders exist.
> ```
>
> But the actual hunt operation is not yet implemented as a full system mechanic.

---

# 1. Verification of recent post-PR5 fixes

## 1.1. Multi-step ActivePlan composition

A new module exists:

```text
backend/app/games/zone_stalkers/decision/active_plan_composer.py
```

It currently composes strategic objectives:

```text
FIND_ARTIFACTS
GET_MONEY_FOR_RESUPPLY
```

by appending:

```text
explore_location
```

after:

```text
travel_to_location
```

when the base plan only contains travel.

This is a good minimum fix for the previous one-step ActivePlan issue.

## 1.2. Tests for composition exist

There are tests for:

```text
FIND_ARTIFACTS → travel_to_location + explore_location
GET_MONEY_FOR_RESUPPLY → travel_to_location + explore_location
SELL_ARTIFACTS remains travel_to_location + trade_sell_item
RESTORE_WATER can remain one-step consume_item
```

Good.

## 1.3. Off-by-one lifecycle summary is fixed

`active_plan_runtime.py` now captures:

```text
completed_step_index
completed_step_kind
steps_count
```

before advancing the ActivePlan. This should fix summaries like:

```text
шаг 2/1, none
шаг 3/2, none
```

Expected summary now:

```text
ActivePlan FIND_ARTIFACTS: шаг 1/2 travel_to_location завершён.
ActivePlan FIND_ARTIFACTS: completed, 2/2 steps completed.
```

## 1.4. Wealth progress export exists

`exportNpcHistory.ts` now exports:

```text
money
liquid_wealth
material_threshold
material_threshold_passed
wealth_goal_target
wealth_goal_reached
global_goal_achieved
```

Good. This clarifies that passing `material_threshold` is not the same as completing `get_rich`.

## 1.5. LEAVE_ZONE objective generation is partially covered

There is a test:

```text
test_completed_global_goal_adds_leave_zone_objective
```

It verifies:

```text
global_goal_achieved = true
has_left_zone = false
→ LEAVE_ZONE objective exists
source = global_goal_completed
```

Good.

## 1.6. Remaining post-PR5 concern

Make sure the code also sets:

```text
global_goal_achieved = true
```

when the actual completion condition is met.

For `get_rich`, this should be based on:

```text
liquid_wealth >= wealth_goal_target
```

or another explicitly documented wealth definition.

If this is not already implemented, add it before relying on `LEAVE_ZONE`.

---

# 2. Current hunt readiness assessment

## 2.1. Already available infrastructure

### Brain v3 decision stack

Available:

```text
NeedEvaluationResult
BeliefState
Objective candidates
ObjectiveDecision
Intent adapter
Planner
ActivePlanV3
brain_trace
memory_v3
debug UI/export
```

This is enough to build a long-running hostile operation.

### Combat readiness

There are tests for pre-hunt requirements:

```text
weapon urgency boosted for kill_stalker
equipped weapon/armor protected from liquidity
compatible ammo reserve protected
combat_readiness fields exist
combat_readiness appears in brain_trace
```

This means the system can already reason about whether the NPC is armed enough for a hunt.

### Target context

`context_builder.py` already includes a basic `known_targets` list from:

```text
agent.kill_target_id
state["agents"][kill_target_id]
```

It includes:

```text
agent_id
name
is_alive
location_id
```

This is a good minimal starting point.

### Hunt objective placeholders

Objective generator already defines reserved hunt objectives:

```text
LOCATE_TARGET
PREPARE_FOR_HUNT
TRACK_TARGET
INTERCEPT_TARGET
AMBUSH_TARGET
ENGAGE_TARGET
CONFIRM_KILL
RETREAT_FROM_TARGET
RECOVER_AFTER_COMBAT
```

It also currently generates at least:

```text
HUNT_TARGET
PREPARE_FOR_HUNT
LOCATE_TARGET
ENGAGE_TARGET
```

for `global_goal = kill_stalker`.

---

# 3. Main gap

The current code has hunt placeholders, but not a full hunt operation.

Currently missing:

```text
1. TargetBelief / target state model.
2. Target memory retrieval from memory_v3.
3. Objective generation based on target knowledge and combat readiness.
4. Objective → ActivePlan composition for hunt stages.
5. Execution steps for tracking/intercept/ambush/engage/confirm kill.
6. Repair when target moved.
7. Kill confirmation.
8. Retreat/recovery after combat.
9. End condition for kill_stalker global goal.
10. E2E tests showing the full v3 chain.
```

---

# 4. Required implementation A — TargetBelief

## Goal

Create a structured belief model for the target.

Recommended file:

```text
backend/app/games/zone_stalkers/decision/models/target_belief.py
```

## Model

```python
@dataclass(frozen=True)
class TargetBelief:
    target_id: str
    is_known: bool
    is_alive: bool | None
    last_known_location_id: str | None
    location_confidence: float
    last_seen_turn: int | None
    visible_now: bool
    co_located: bool
    equipment_known: bool
    combat_strength: float | None
    combat_strength_confidence: float
    route_hints: tuple[str, ...]
    source_refs: tuple[str, ...]
```

## Builder

Recommended file:

```text
backend/app/games/zone_stalkers/decision/target_beliefs.py
```

API:

```python
def build_target_belief(
    *,
    agent_id: str,
    agent: dict[str, Any],
    state: dict[str, Any],
    world_turn: int,
    belief_state: BeliefState,
) -> TargetBelief:
    ...
```

## Data sources

Use:

```text
1. visible entities / same location;
2. state["agents"][kill_target_id] if allowed by simulation visibility;
3. legacy memory;
4. memory_v3 records;
5. target intel from trader/dialogue.
```

Important:

```text
Do not give omniscient target location to the hunter unless this is explicitly allowed.
```

If `state["agents"][target_id].location_id` is used, mark source as:

```text
source = omniscient_debug
```

or restrict it to debug/test mode.

## Memory kinds

Read from memory_v3:

```text
target_seen
target_last_known_location
target_not_found
target_route_observed
target_equipment_seen
target_combat_strength_observed
target_death_confirmed
target_intel
```

---

# 5. Required implementation B — write target memories

## Goal

The NPC must build knowledge of the target through system events.

## Add memory writes

### When target is visible

If target is in same location or visible according to visibility rules:

```text
target_seen
target_last_known_location
target_equipment_seen
target_combat_strength_observed
```

Memory details:

```json
{
  "target_id": "agent_2",
  "location_id": "loc_x",
  "hp": 80,
  "weapon_type": "shotgun",
  "armor_type": "leather_jacket",
  "observed_turn": 12345
}
```

### When target is not found at expected location

After searching a target location:

```text
target_not_found
```

Details:

```json
{
  "target_id": "agent_2",
  "location_id": "loc_x",
  "expected_from_memory_id": "mem_target_last_seen",
  "confirmed_empty": true
}
```

### When target dies

When combat or world rules kill the target:

```text
target_death_confirmed
```

Details:

```json
{
  "target_id": "agent_2",
  "killer_id": "agent_1",
  "location_id": "loc_x",
  "cause": "combat"
}
```

## Bridge

Ensure these kinds go to memory_v3 with useful indexes:

```text
by_entity[target_id]
by_location[location_id]
by_kind[target_seen]
by_tag[hunt]
```

---

# 6. Required implementation C — objective generation for hunt

## Current

Generator currently emits generic hunt objectives for `kill_stalker`.

This is not enough because it does not choose stage based on target belief.

## Required stage logic

Generate objectives based on `TargetBelief` and `combat_readiness`.

### 6.1. If target unknown

```text
LOCATE_TARGET
```

Reason:

```text
Нужно узнать местоположение цели
```

Plan examples:

```text
ask trader for intel
travel to last social/contact location
search known hubs
```

### 6.2. If target last known location exists but not co-located

```text
TRACK_TARGET
```

or:

```text
INTERCEPT_TARGET
```

depending on confidence and route hints.

### 6.3. If target co-located and hunter combat-ready

```text
ENGAGE_TARGET
```

or:

```text
AMBUSH_TARGET
```

if ambush position/mechanics available.

### 6.4. If target co-located but hunter not ready

```text
PREPARE_FOR_HUNT
```

Blockers:

```text
no_weapon
low_ammo
low_hp
no_medkit
target_too_strong
```

### 6.5. If target dead but not confirmed

```text
CONFIRM_KILL
```

### 6.6. If hunter hurt after combat

```text
RETREAT_FROM_TARGET
RECOVER_AFTER_COMBAT
```

## Scoring

Add target-specific scoring factors:

```text
target_location_confidence
combat_readiness
target_strength_risk
distance/time_cost
memory_confidence
goal_alignment
```

---

# 7. Required implementation D — hunt ActivePlan composition

## Goal

Add hunt-specific composition to:

```text
active_plan_composer.py
```

or create:

```text
hunt_plan_composer.py
```

## Required plan shapes

### LOCATE_TARGET

If trader/intel source known:

```text
ActivePlan LOCATE_TARGET:
  1. travel_to_trader
  2. ask_for_intel
```

If search location known:

```text
ActivePlan LOCATE_TARGET:
  1. travel_to_location
  2. search_for_target
```

### TRACK_TARGET

```text
ActivePlan TRACK_TARGET:
  1. travel_to_last_known_location
  2. search_for_target
  3. update_target_memory
```

### INTERCEPT_TARGET

```text
ActivePlan INTERCEPT_TARGET:
  1. travel_to_intercept_location
  2. wait/observe
  3. engage_or_repair
```

### PREPARE_FOR_HUNT

Depending on blockers:

```text
1. resupply_weapon / buy weapon
2. resupply_ammo / buy ammo
3. heal_self / buy medicine
4. return_to_hunt
```

### ENGAGE_TARGET

```text
1. travel_to_target_if_needed
2. start_combat
3. monitor_combat_until_resolved
4. confirm_kill
```

### CONFIRM_KILL

```text
1. travel_to_last_combat_location
2. inspect_target_status
3. write target_death_confirmed
```

### RETREAT_FROM_TARGET / RECOVER_AFTER_COMBAT

```text
1. flee_to_safe_location
2. heal/rest/resupply
3. decide whether to resume hunt
```

---

# 8. Required implementation E — new PlanStep kinds

Add explicit step kinds instead of overloading generic explore/travel too much.

Recommended additions:

```text
STEP_SEARCH_TARGET
STEP_ASK_TARGET_INTEL
STEP_TRACK_TARGET
STEP_INTERCEPT_TARGET
STEP_START_COMBAT
STEP_MONITOR_COMBAT
STEP_CONFIRM_KILL
STEP_RETREAT_FROM_TARGET
```

Add executor support for each.

## Minimal MVP alternative

If adding all step kinds is too much, add only:

```text
search_target
ask_for_intel
start_combat
confirm_kill
```

Use existing:

```text
travel_to_location
wait
trade_buy_item
consume_item
```

for the rest.

---

# 9. Required implementation F — combat integration

## Current risk

The project already has combat interactions, but hunt objective must connect to them through a clear step.

## Required behavior

When `ENGAGE_TARGET` executes:

```text
1. Verify target is co-located.
2. Verify target is alive.
3. Verify hunter has weapon/ammo.
4. Create combat_interaction.
5. ActivePlan step remains running while combat is unresolved.
6. On combat end:
   - if target dead → CONFIRM_KILL / global_goal_achieved;
   - if hunter wounded/fled → RETREAT/RECOVER;
   - if target fled → TRACK_TARGET repair.
```

## Required memory

Combat should write:

```text
target_engaged
target_wounded
target_fled
target_death_confirmed
hunter_wounded
hunt_failed
```

---

# 10. Required implementation G — repair behavior for target movement

## Goal

ActivePlan repair must support target moving.

## Repair reasons

```text
target_moved
target_not_found
target_too_strong
combat_failed
target_dead_unconfirmed
```

## Required behavior

### target_moved

```text
1. update target_last_known_location if new location known;
2. replace current travel/search step;
3. continue TRACK_TARGET.
```

### target_not_found

```text
1. write target_not_found memory;
2. reduce confidence of old target_last_known_location;
3. generate LOCATE_TARGET or TRACK_TARGET with another source.
```

### target_too_strong

```text
1. abort ENGAGE_TARGET;
2. choose PREPARE_FOR_HUNT or RETREAT_FROM_TARGET.
```

---

# 11. Required implementation H — global goal completion for kill_stalker

## Goal

When target is confirmed dead:

```text
global_goal_achieved = true
```

if:

```text
agent.global_goal == "kill_stalker"
agent.kill_target_id == target_id
target_death_confirmed memory exists
```

Then the usual completed-goal logic should apply:

```text
global_goal_achieved + not has_left_zone → LEAVE_ZONE
```

## Required memory

```text
global_goal_completed
target_death_confirmed
```

## Tests

```python
def test_kill_stalker_target_death_confirms_global_goal():
    ...
```

Expected:

```text
target is dead
target_death_confirmed memory exists
agent.global_goal_achieved is true
next objective is LEAVE_ZONE
```

---

# 12. Required implementation I — debug UI for hunt

Extend NPC Brain profile to show hunt context.

## New panel section

Inside `NpcBrainPanel` or separate `HuntTargetPanel`:

```text
Hunt target:
  target id/name
  alive/unknown/dead
  last known location
  confidence
  visible now
  combat readiness
  current hunt stage
  blockers
```

## Compact export

Add:

```json
"hunt": {
  "target_id": "...",
  "target_name": "...",
  "target_alive": true,
  "last_known_location_id": "...",
  "location_confidence": 0.72,
  "visible_now": false,
  "combat_readiness": {...},
  "current_hunt_objective": "TRACK_TARGET"
}
```

---

# 13. Test scenarios to implement

## 13.1. Target unknown → gather intel

Setup:

```text
hunter.global_goal = kill_stalker
hunter.kill_target_id = target
no target memory
trader known
```

Expected:

```text
Objective = LOCATE_TARGET
ActivePlan:
  travel_to_trader
  ask_for_intel
memory target_intel written
```

## 13.2. Target last known → track

Setup:

```text
memory_v3 target_last_known_location loc_b
hunter at loc_a
target maybe moved
```

Expected:

```text
Objective = TRACK_TARGET
ActivePlan:
  travel_to_location loc_b
  search_target
```

## 13.3. Target moved → repair

Setup:

```text
ActivePlan TRACK_TARGET to loc_b
target_not_found at loc_b
new target_seen at loc_c
```

Expected:

```text
repair reason target_moved/target_not_found
ActivePlan target step changes to loc_c
```

## 13.4. Not enough ammo → prepare

Setup:

```text
target known
hunter has weapon but ammo low
```

Expected:

```text
Objective = PREPARE_FOR_HUNT
ActivePlan includes resupply ammo
No ENGAGE_TARGET yet
```

## 13.5. Target co-located and ready → engage

Setup:

```text
hunter and target in same location
hunter has weapon/ammo/meds
```

Expected:

```text
Objective = ENGAGE_TARGET
ActivePlan:
  start_combat
  monitor_combat
```

## 13.6. Target killed → confirm kill → leave zone

Setup:

```text
combat resolved
target.is_alive = false
hunter.kill_target_id = target
```

Expected:

```text
target_death_confirmed memory
global_goal_achieved = true
next Objective = LEAVE_ZONE
```

## 13.7. Failed ambush → retreat/recover

Setup:

```text
target stronger than hunter
hunter wounded
```

Expected:

```text
Objective = RETREAT_FROM_TARGET or RECOVER_AFTER_COMBAT
ActivePlan:
  flee_to_safe_location
  heal/rest
```

---

# 14. Implementation order

## Stage 1 — Target belief and memory

```text
1. Add TargetBelief model.
2. Add build_target_belief().
3. Write target_seen / target_last_known_location memories.
4. Add memory_v3 indexing/bridge mappings for target records.
5. Add tests for target belief from visible target and memory.
```

## Stage 2 — Hunt objective generation

```text
1. Extend ObjectiveGenerationContext with target_belief or build inside generator.
2. Generate LOCATE/TRACK/PREPARE/ENGAGE/CONFIRM based on target state.
3. Add blockers and scoring.
4. Add objective tests.
```

## Stage 3 — Hunt planning and ActivePlan composition

```text
1. Add hunt plan composition.
2. Add new step kinds.
3. Add executors for search_target, ask_for_intel, start_combat, confirm_kill.
4. Add ActivePlan tests.
```

## Stage 4 — Combat and repair

```text
1. Connect ENGAGE_TARGET to combat_interactions.
2. Monitor combat resolution.
3. Add target_moved / target_not_found repair.
4. Add retreat/recovery.
```

## Stage 5 — Completion and debug

```text
1. target_death_confirmed → global_goal_achieved.
2. Completed goal → LEAVE_ZONE.
3. Add HuntTargetPanel / compact export hunt section.
4. Add E2E scenario tests.
```

---

# 15. Acceptance criteria

This work is complete when:

```text
[ ] NPC with kill_stalker goal does not attack blindly.
[ ] Unknown target leads to LOCATE_TARGET.
[ ] Known target location leads to TRACK_TARGET.
[ ] Combat blockers lead to PREPARE_FOR_HUNT.
[ ] Co-located ready hunter leads to ENGAGE_TARGET.
[ ] ENGAGE_TARGET starts combat through system mechanics.
[ ] Target moving triggers ActivePlan repair, not stale retry.
[ ] Target not found writes memory and lowers confidence.
[ ] Target death writes target_death_confirmed.
[ ] target_death_confirmed completes kill_stalker global goal.
[ ] Completed kill_stalker leads to LEAVE_ZONE.
[ ] Debug profile shows hunt target belief/stage.
[ ] Compact export includes hunt context.
[ ] E2E tests demonstrate full Brain v3 chain.
```

---

# 16. Current project readiness

## Ready

```text
Brain v3 runtime infrastructure: ready
ActivePlan lifecycle: ready
Objective system: ready
Memory v3 storage: ready
Combat readiness prerequisites: partially ready
Target ID field: ready
Target basic context: ready
Debug/export framework: ready
```

## Not ready yet

```text
TargetBelief model: missing
Target memory writes: missing/incomplete
Hunt objective stage selection: incomplete
Hunt ActivePlan composition: missing
Hunt-specific step executors: missing
Target movement repair: missing
Combat integration from ENGAGE_TARGET: missing/incomplete
Kill confirmation: missing
Hunt debug panel: missing
E2E hunt tests: missing
```

## Summary

The project is now ready to start implementing the kill-stalker operation.

But the operation itself is not just a few scoring tweaks. It should be implemented as a proper system layer on top of PR5:

```text
TargetBelief
→ Hunt objectives
→ Hunt ActivePlan
→ Hunt executors
→ Combat monitor
→ Target memory repair
→ Kill confirmation
→ LEAVE_ZONE
```
