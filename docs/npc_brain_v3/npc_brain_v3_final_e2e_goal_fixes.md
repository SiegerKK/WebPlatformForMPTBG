# NPC Brain v3 — Remaining Fixes and Final E2E Scenario Tests

> Branch context: `copilot/implement-pr-5-for-npc-brain-v3`
>
> Purpose:
>
> ```text
> Проверка последних правок по v3/hunt и список того, что ещё нужно исправить,
> чтобы можно было тестировать цель "разбогатеть" и цель "убить NPC"
> как полноценные end-to-end сценарии:
>
> spawn
> → decision chain
> → ActivePlan lifecycle
> → memory_v3 / trace
> → global_goal_achieved
> → LEAVE_ZONE
> → has_left_zone
> ```
>
> Current conclusion:
>
> ```text
> PR5/v3 foundation is now good.
> get_rich path is close to testable end-to-end.
> kill_stalker path has useful pieces, but still needs TargetBelief and full E2E wiring.
> ```

---

# 1. What was implemented correctly

## 1.1. Strategic ActivePlan composition

`active_plan_composer.py` now composes strategic objectives:

```text
FIND_ARTIFACTS
GET_MONEY_FOR_RESUPPLY
```

from:

```text
travel_to_location
```

into:

```text
travel_to_location
explore_location
```

when the base plan only contains travel.

This fixes the worst post-PR5 issue where strategic objectives were often just one-step wrappers.

Expected behavior:

```text
FIND_ARTIFACTS:
  1. travel_to_location
  2. explore_location

GET_MONEY_FOR_RESUPPLY:
  1. travel_to_location
  2. explore_location
```

Atomic objectives can still be one-step:

```text
RESTORE_WATER → consume_item
RESTORE_FOOD → consume_item
REST → sleep_for_hours
RESUPPLY_AMMO → trade_buy_item
```

## 1.2. ActivePlan off-by-one was fixed

`active_plan_runtime.py` now captures completed step data before `advance_step()`:

```text
completed_step_index
completed_step_kind
steps_count
next_step_index
next_step_kind
```

This should remove broken summaries like:

```text
шаг 2/1, none
шаг 3/2, none
```

Expected summaries:

```text
ActivePlan FIND_ARTIFACTS: шаг 1/2 travel_to_location завершён.
ActivePlan FIND_ARTIFACTS: шаг 2/2 explore_location завершён.
ActivePlan FIND_ARTIFACTS: completed, 2/2 steps completed.
```

## 1.3. New hunt step kinds were added

`PlanStep` now has explicit hunt-related step kinds:

```text
ask_for_intel
search_target
start_combat
confirm_kill
```

This is the right direction. It means hunt can now be expressed through system mechanics instead of hardcoded direct goal completion.

## 1.4. Hunt executors were started

`executors.py` now has handlers for:

```text
_exec_ask_for_intel
_exec_search_target
_exec_start_combat
_exec_confirm_kill
```

This is good because the hunt pipeline can now eventually become:

```text
LOCATE_TARGET
→ ask_for_intel

TRACK_TARGET
→ travel_to_location
→ search_target

ENGAGE_TARGET
→ start_combat

CONFIRM_KILL
→ confirm_kill
```

## 1.5. Objective generator was expanded for hunt stages

`generator.py` now contains hunt objective keys and attempts to generate hunt-stage objectives based on:

```text
kill_stalker
combat_readiness
target_belief
target location
target visibility
target alive/dead status
```

This is the correct direction.

---

# 2. Critical remaining issue: TargetBelief is still missing/incomplete

## Problem

`ObjectiveGenerationContext` has:

```python
target_belief: TargetBelief | None = None
```

and `generator.py` now expects:

```python
target_belief = ctx.target_belief
```

But currently there does not appear to be a real implementation of:

```text
backend/app/games/zone_stalkers/decision/models/target_belief.py
backend/app/games/zone_stalkers/decision/target_beliefs.py
build_target_belief(...)
```

Also `AgentContext` still has only:

```text
known_targets
```

but not a structured:

```text
target_belief
```

## Why this matters

Without `TargetBelief`, the generator can only make very shallow hunt decisions.

The system cannot reliably distinguish:

```text
target unknown
target known by memory
target visible now
target co-located
target moved
target dead but unconfirmed
target too strong
```

So the hunt pipeline cannot reliably transition:

```text
LOCATE_TARGET
→ TRACK_TARGET
→ ENGAGE_TARGET
→ CONFIRM_KILL
→ LEAVE_ZONE
```

## Required fix

Add:

```text
backend/app/games/zone_stalkers/decision/models/target_belief.py
backend/app/games/zone_stalkers/decision/target_beliefs.py
```

Recommended model:

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

Recommended builder:

```python
def build_target_belief(
    *,
    agent_id: str,
    agent: dict[str, Any],
    state: dict[str, Any],
    world_turn: int,
    belief_state: BeliefState,
) -> TargetBelief | None:
    ...
```

Then pass it into:

```python
ObjectiveGenerationContext(..., target_belief=target_belief)
```

inside the real Brain v3 decision pipeline.

## Required tests

```python
def test_target_belief_from_visible_colocated_target():
    ...
```

Expected:

```text
visible_now = true
co_located = true
last_known_location_id = current location
is_alive = true
source_refs include visible target
```

```python
def test_target_belief_from_memory_v3_last_known_location():
    ...
```

Expected:

```text
visible_now = false
co_located = false
last_known_location_id = loc_b
location_confidence > 0
source_refs include memory:<id>
```

```python
def test_target_belief_from_target_death_confirmed_memory():
    ...
```

Expected:

```text
is_alive = false
source_refs include target_death_confirmed memory
```

---

# 3. Critical remaining issue: target memory writes need bridge/index validation

## Current state

`_exec_search_target()` writes legacy memory events:

```text
target_seen
target_last_known_location
target_combat_strength_observed
target_equipment_seen
target_not_found
```

`_exec_confirm_kill()` writes:

```text
target_death_confirmed
hunt_failed
```

This is good.

## Missing validation

Need to verify that these legacy memory events correctly bridge into `memory_v3` with:

```text
kind = target_seen
kind = target_last_known_location
kind = target_not_found
kind = target_equipment_seen
kind = target_combat_strength_observed
kind = target_death_confirmed
```

and with useful indexes:

```text
entity_ids contains target_id
location_id is filled
tags include hunt / target / target:<id>
```

## Required fix

Update `memory/legacy_bridge.py` if needed.

Required mapping:

```text
target_seen                         → layer=social or threat, kind=target_seen
target_last_known_location           → layer=spatial/social, kind=target_last_known_location
target_not_found                     → layer=spatial, kind=target_not_found
target_equipment_seen                → layer=threat/social, kind=target_equipment_seen
target_combat_strength_observed      → layer=threat, kind=target_combat_strength_observed
target_death_confirmed               → layer=goal/social, kind=target_death_confirmed
hunt_failed                          → layer=goal, kind=hunt_failed
global_goal_completed                → layer=goal, kind=global_goal_completed
```

## Required tests

```python
def test_target_seen_memory_bridges_to_memory_v3():
    ...
```

```python
def test_target_not_found_memory_bridges_to_memory_v3_with_location():
    ...
```

```python
def test_target_death_confirmed_memory_bridges_to_memory_v3_with_entity_id():
    ...
```

---

# 4. Critical remaining issue: kill_stalker completion is not proven

## Problem

`_exec_confirm_kill()` writes `target_death_confirmed` if target is dead.

But we also need a systemic transition:

```text
target_death_confirmed
→ global_goal_achieved = true
→ global_goal_completed memory
→ LEAVE_ZONE objective
→ ActivePlan LEAVE_ZONE
→ has_left_zone = true
```

Search did not show a clear guaranteed rule:

```text
target_death_confirmed + kill_target_id
→ global_goal_achieved
```

## Required fix

Add a goal completion evaluator.

Recommended module:

```text
backend/app/games/zone_stalkers/decision/global_goal_completion.py
```

API:

```python
def evaluate_global_goal_completion(
    agent_id: str,
    agent: dict[str, Any],
    state: dict[str, Any],
    world_turn: int,
    *,
    add_memory: Callable[..., None],
) -> None:
    ...
```

Call it once per tick before objective generation, or immediately after relevant action completion.

## Rules

### get_rich

```text
if global_goal == "get_rich"
and liquid_wealth >= wealth_goal_target:
    global_goal_achieved = true
    write global_goal_completed memory
```

### kill_stalker

```text
if global_goal == "kill_stalker"
and kill_target_id exists
and target_death_confirmed memory exists for kill_target_id:
    global_goal_achieved = true
    write global_goal_completed memory
```

Also acceptable:

```text
target.is_alive == false
and target_death_confirmed memory exists
```

Do not complete kill_stalker solely because the target is dead somewhere in world state unless hunter confirmed it.

### leave_zone

```text
if global_goal_achieved == true
and has_left_zone == false:
    generate LEAVE_ZONE
```

This part is already partially implemented in objective generation.

## Required tests

```python
def test_get_rich_liquid_wealth_reaches_target_sets_global_goal_achieved():
    ...
```

```python
def test_target_death_confirmed_sets_kill_stalker_global_goal_achieved():
    ...
```

```python
def test_completed_kill_stalker_generates_leave_zone_objective():
    ...
```

---

# 5. Critical remaining issue: LEAVE_ZONE execution must be end-to-end

## Problem

Objective generation can produce `LEAVE_ZONE`, but we need proof that:

```text
LEAVE_ZONE
→ intent leave_zone
→ plan leave_zone
→ scheduled_action / execution
→ has_left_zone = true
```

## Required behavior

Recommended plan:

```text
ActivePlan LEAVE_ZONE:
  1. travel_to_exit
  2. leave_zone
```

MVP acceptable:

```text
if already at exit:
  1. leave_zone
else:
  1. travel_to_exit
  2. leave_zone
```

## Required PlanStep

If not already available, add:

```text
STEP_LEAVE_ZONE = "leave_zone"
```

Executor:

```python
def _exec_leave_zone(...):
    agent["has_left_zone"] = True
    agent["scheduled_action"] = None
    agent["active_plan_v3"] = None
    agent["action_queue"] = []
    write memory action_kind="left_zone"
    return event_type="agent_left_zone"
```

If there is already a legacy leave-zone helper, wrap it as a PlanStep executor.

## Required tests

```python
def test_leave_zone_active_plan_sets_has_left_zone():
    ...
```

```python
def test_completed_get_rich_eventually_leaves_zone():
    ...
```

```python
def test_completed_kill_stalker_eventually_leaves_zone():
    ...
```

---

# 6. Important issue: hunt objective selection must not be omniscient

## Problem

`context_builder.py` currently puts `kill_target_id` into `known_targets` using direct world state:

```text
agent_id
name
is_alive
location_id
```

This is convenient, but for actual gameplay it risks omniscience:

```text
hunter always knows target location from state["agents"][target_id].location_id
```

## Required policy

Choose explicitly:

### Option A — debug omniscience only

Only expose live target location when:

```text
state.debug_omniscient_targets == true
```

or in tests.

### Option B — use memory-first knowledge

For normal NPC logic:

```text
target location comes from:
- same location visibility;
- target_seen memory;
- target_last_known_location memory;
- target_intel;
- route observation.
```

The actual world state may be used only to verify current co-location or death after interaction.

Recommended:

```text
Use memory-first for normal decisions.
Use world-state exact target location only when co-located/visible or debug flag is enabled.
```

## Required test

```python
def test_hunter_does_not_know_target_location_without_visibility_or_memory():
    ...
```

Expected:

```text
target exists in state at loc_b
hunter at loc_a
no target memory
debug omniscience disabled

TargetBelief:
  last_known_location_id is None
Objective:
  LOCATE_TARGET
not TRACK_TARGET directly to loc_b
```

---

# 7. Important issue: ENGAGE_TARGET must monitor combat resolution

## Current state

`_exec_start_combat()` starts a combat interaction.

But the full ActivePlan behavior must be:

```text
start_combat
→ monitor_combat while combat is unresolved
→ confirm kill / retreat / track moved target
```

Currently there is no explicit:

```text
STEP_MONITOR_COMBAT
```

and no proof that ActivePlan waits for combat resolution instead of immediately advancing after `STEP_START_COMBAT`.

## Why this matters

`execute_plan_step()` currently advances immediately for one-tick actions including:

```text
STEP_START_COMBAT
STEP_CONFIRM_KILL
```

That means an `ENGAGE_TARGET` plan could start combat and then immediately move to confirmation, before the combat system has resolved.

## Required fix

Add:

```text
STEP_MONITOR_COMBAT
```

or make `STEP_START_COMBAT` create a scheduled/running combat action that does not complete until combat resolves.

Recommended plan:

```text
ENGAGE_TARGET:
  1. start_combat
  2. monitor_combat
  3. confirm_kill
```

`monitor_combat` should:

```text
if combat active:
  keep step running
if target dead:
  complete and continue to confirm_kill
if hunter wounded/fled:
  request RETREAT/RECOVER
if target fled:
  repair as target_moved / target_not_found
```

## Required tests

```python
def test_engage_target_does_not_confirm_before_combat_resolves():
    ...
```

```python
def test_engage_target_after_combat_target_dead_moves_to_confirm_kill():
    ...
```

---

# 8. Important issue: prepare-for-hunt currently maps to generic resupply

## Current state

`PREPARE_FOR_HUNT` maps to `INTENT_RESUPPLY`.

That is useful but too generic.

## Required behavior

`PREPARE_FOR_HUNT` should preserve hunt blockers:

```text
no_weapon
low_ammo
low_hp
no_medicine
target_too_strong
```

and build a plan from them:

```text
no_weapon → RESUPPLY_WEAPON / buy weapon
low_ammo → RESUPPLY_AMMO / buy ammo
low_hp → HEAL_SELF / buy medicine / rest
no_medicine → RESUPPLY_MEDICINE
target_too_strong → improve armor/weapon or retreat
```

## Required fix

Either:

### Option A — keep generic resupply, but pass forced category list

In `objective_to_intent()`:

```python
metadata["hunt_prepare_blockers"] = blockers
metadata["forced_resupply_category"] = first_missing_category
```

### Option B — add a dedicated plan builder

```text
INTENT_PREPARE_FOR_HUNT
```

Preferred for clarity.

## Required test

```python
def test_prepare_for_hunt_no_ammo_buys_compatible_ammo():
    ...
```

```python
def test_prepare_for_hunt_no_weapon_buys_weapon_not_food():
    ...
```

---

# 9. Important issue: hunt repair should use target memory, not generic location_empty only

## Required repair reasons

```text
target_moved
target_not_found
target_too_strong
combat_failed
target_dead_unconfirmed
```

## Required behavior

### target_not_found

```text
write target_not_found memory
lower confidence of previous target_last_known_location
repair to LOCATE_TARGET or another target_last_known_location
```

### target_moved

```text
if new target_seen exists:
  replace current target location
else:
  switch to LOCATE_TARGET
```

### target_too_strong

```text
switch to PREPARE_FOR_HUNT or RETREAT_FROM_TARGET
```

### combat_failed

```text
RETREAT_FROM_TARGET
RECOVER_AFTER_COMBAT
then optionally resume TRACK_TARGET
```

## Required tests

```python
def test_track_target_not_found_repairs_to_locate_target():
    ...
```

```python
def test_target_moved_replaces_active_plan_target_location():
    ...
```

```python
def test_target_too_strong_switches_to_prepare_for_hunt():
    ...
```

---

# 10. Important issue: current hunt docs should be updated with implementation reality

`docs/npc_brain_v3/07_post_pr5_kill_stalker_goal.md` currently describes the desired operation at a high level.

After implementation, update it with:

```text
TargetBelief model
memory_v3 target kinds
hunt objective generation table
hunt ActivePlan examples
hunt repair table
combat monitor behavior
global goal completion rule
E2E test matrix
known limitations
```

Do not keep separate root-level temp docs.

---

# 11. Final E2E tests we should add

Yes, we can and should create several final scenario tests.

These should not only test helper functions. They should run repeated ticks and verify the whole Brain v3 chain.

Recommended file:

```text
backend/tests/decision/v3/test_e2e_brain_v3_goals.py
```

## 11.1. E2E get_rich from spawn to leave zone

### Purpose

Verify the full v3 loop:

```text
spawn
→ get_rich objective
→ artifact search ActivePlan
→ artifact pickup
→ sell artifact
→ global_goal_achieved
→ LEAVE_ZONE
→ has_left_zone
```

### Scenario setup

Use a deterministic small world:

```text
loc_spawn
  safe
  connected to loc_anomaly and loc_trader and loc_exit

loc_anomaly
  anomaly_activity high
  contains deterministic artifact or guaranteed artifact spawn

loc_trader
  trader buys artifacts

loc_exit
  exit location
```

Agent:

```text
global_goal = get_rich
money = 0
wealth_goal_target = low, e.g. 1000
material_threshold = 0 or low
has weapon/food/water enough
```

### Expected assertions

At the end:

```text
agent.global_goal_achieved == true
agent.has_left_zone == true
agent.active_plan_v3 is None or terminal
agent.action_queue == []
```

Memory/trace must include:

```text
objective_decision FIND_ARTIFACTS or GET_MONEY_FOR_RESUPPLY
active_plan_created
active_plan_step_completed travel_to_location
active_plan_step_completed explore_location
artifact pickup memory/event
SELL_ARTIFACTS objective_decision
trade_sell_item
global_goal_completed
LEAVE_ZONE objective_decision
left_zone memory/event
```

### Test skeleton

```python
def test_e2e_get_rich_from_spawn_to_leave_zone():
    state = make_e2e_get_rich_state()
    for _ in range(500):
        state, events = tick_zone_map(state)
        agent = state["agents"]["hunter"]
        if agent.get("has_left_zone"):
            break

    assert agent["has_left_zone"] is True
    assert agent["global_goal_achieved"] is True
    assert any_memory(agent, "global_goal_completed")
    assert any_memory(agent, "left_zone")
    assert any_objective_decision(agent, "LEAVE_ZONE")
```

---

## 11.2. E2E kill_stalker target unknown → intel → track → kill → leave zone

### Purpose

Verify the full hunt operation:

```text
spawn
→ LOCATE_TARGET
→ ask_for_intel
→ target_last_known_location
→ TRACK_TARGET
→ search_target
→ ENGAGE_TARGET
→ combat
→ CONFIRM_KILL
→ global_goal_achieved
→ LEAVE_ZONE
→ has_left_zone
```

### Scenario setup

World:

```text
loc_spawn
loc_trader
loc_target
loc_exit
```

Hunter:

```text
global_goal = kill_stalker
kill_target_id = "target"
has weapon
has compatible ammo
has armor
has medkit
no target memory initially
```

Target:

```text
is_alive = true
location_id = loc_target
weaker than hunter for deterministic kill
```

Trader:

```text
at loc_trader
has target intel or can reveal loc_target
```

### Expected assertions

At the end:

```text
target.is_alive == false
hunter.global_goal_achieved == true
hunter.has_left_zone == true
```

Memory must include:

```text
target_intel
target_last_known_location
target_seen
target_combat_strength_observed
target_death_confirmed
global_goal_completed
left_zone
```

Brain trace / objective decisions must include:

```text
LOCATE_TARGET
TRACK_TARGET
ENGAGE_TARGET
CONFIRM_KILL
LEAVE_ZONE
```

ActivePlan steps must include:

```text
ask_for_intel
travel_to_location
search_target
start_combat
confirm_kill
leave_zone
```

### Test skeleton

```python
def test_e2e_kill_stalker_unknown_target_to_leave_zone():
    state = make_e2e_kill_stalker_state(target_known=False)

    for _ in range(1000):
        state, events = tick_zone_map(state)
        hunter = state["agents"]["hunter"]
        target = state["agents"]["target"]
        if hunter.get("has_left_zone"):
            break

    assert target["is_alive"] is False
    assert hunter["global_goal_achieved"] is True
    assert hunter["has_left_zone"] is True

    assert any_memory(hunter, "target_intel")
    assert any_memory(hunter, "target_last_known_location")
    assert any_memory(hunter, "target_death_confirmed")
    assert any_memory(hunter, "global_goal_completed")
    assert any_memory(hunter, "left_zone")

    assert any_objective_decision(hunter, "LOCATE_TARGET")
    assert any_objective_decision(hunter, "TRACK_TARGET")
    assert any_objective_decision(hunter, "ENGAGE_TARGET")
    assert any_objective_decision(hunter, "CONFIRM_KILL")
    assert any_objective_decision(hunter, "LEAVE_ZONE")
```

---

## 11.3. E2E kill_stalker target known → track → kill → leave zone

### Purpose

Same as above, but skip intel stage.

Initial memory:

```text
target_last_known_location = loc_target
```

Expected:

```text
TRACK_TARGET
ENGAGE_TARGET
CONFIRM_KILL
LEAVE_ZONE
```

No required `LOCATE_TARGET`.

```python
def test_e2e_kill_stalker_known_target_to_leave_zone():
    ...
```

---

## 11.4. E2E kill_stalker not ready → prepare → hunt → leave zone

### Purpose

Verify preparation is systemic.

Hunter starts:

```text
no ammo or no weapon
target known
```

Expected chain:

```text
PREPARE_FOR_HUNT
→ RESUPPLY_AMMO / RESUPPLY_WEAPON
→ TRACK_TARGET
→ ENGAGE_TARGET
→ CONFIRM_KILL
→ LEAVE_ZONE
```

```python
def test_e2e_kill_stalker_prepares_before_engage():
    ...
```

Assertions:

```text
No ENGAGE_TARGET before combat_readiness is sufficient.
RESUPPLY_* objective appears before ENGAGE_TARGET.
```

---

## 11.5. E2E kill_stalker target moved → repair → continue

### Purpose

Verify ActivePlan repair.

Setup:

```text
hunter has target_last_known_location = loc_b
target starts at loc_b but moves to loc_c before hunter arrives
```

Expected:

```text
TRACK_TARGET to loc_b
search_target writes target_not_found
repair target_not_found / target_moved
LOCATE_TARGET or TRACK_TARGET loc_c
ENGAGE_TARGET
CONFIRM_KILL
LEAVE_ZONE
```

```python
def test_e2e_kill_stalker_target_moved_repairs_plan():
    ...
```

---

## 11.6. E2E emergency interruption during hunt

### Purpose

Verify v3 can handle interruption while hunting.

Setup:

```text
hunter is tracking target
emission starts
```

Expected:

```text
ActivePlan repair/pause
REACH_SAFE_SHELTER / WAIT_IN_SHELTER
after emission:
  resume TRACK_TARGET or re-evaluate hunt objective
eventually:
  target_death_confirmed
  LEAVE_ZONE
```

```python
def test_e2e_kill_stalker_emission_interrupts_then_resumes_hunt():
    ...
```

---

# 12. Test helpers needed for E2E

Create helper functions:

```text
backend/tests/decision/v3/e2e_helpers.py
```

Recommended helpers:

```python
def run_until(state, predicate, max_ticks=1000):
    for _ in range(max_ticks):
        state, events = tick_zone_map(state)
        if predicate(state, events):
            return state, events
    raise AssertionError("condition not reached")

def any_memory(agent, action_kind):
    return any(
        m.get("effects", {}).get("action_kind") == action_kind
        for m in agent.get("memory", [])
    )

def memories(agent, action_kind):
    return [
        m for m in agent.get("memory", [])
        if m.get("effects", {}).get("action_kind") == action_kind
    ]

def any_objective_decision(agent, objective_key):
    return any(
        m.get("effects", {}).get("action_kind") == "objective_decision"
        and m.get("effects", {}).get("objective_key") == objective_key
        for m in agent.get("memory", [])
    )

def active_plan_events(agent, event_kind):
    return [
        ev for ev in agent.get("brain_trace", {}).get("events", [])
        if ev.get("mode") == "active_plan"
        and ev.get("decision") == event_kind
    ]
```

Important:

```text
Do not make E2E tests depend on every exact intermediate turn.
Assert stage order and final invariant instead.
```

---

# 13. Current readiness score

## get_rich E2E readiness

```text
~75–85%
```

Ready:

```text
FIND_ARTIFACTS/GET_MONEY_FOR_RESUPPLY multi-step composition
SELL_ARTIFACTS multi-step
wealth progress export
LEAVE_ZONE objective generation after global_goal_achieved
ActivePlan lifecycle
```

Still missing/prove:

```text
global_goal_achieved is actually set when wealth target reached
LEAVE_ZONE execution sets has_left_zone
full E2E test from spawn to exit
```

## kill_stalker E2E readiness

```text
~45–60%
```

Ready:

```text
kill_target_id
basic known_targets
combat_readiness
hunt objective placeholders
hunt step kinds
hunt step executors started
ActivePlan lifecycle
```

Still missing/prove:

```text
TargetBelief model/builder
target memory_v3 bridge/index validation
stage selection from target belief
non-omniscient target knowledge
combat monitor / no premature confirm
kill confirmation → global_goal_achieved
LEAVE_ZONE execution after kill
full E2E tests
```

---

# 14. Minimal fixes before writing final E2E tests

Before writing the full final E2E tests, finish these blockers:

```text
[ ] Add TargetBelief model and build_target_belief().
[ ] Pass target_belief into ObjectiveGenerationContext in real runtime.
[ ] Ensure target memory events bridge to memory_v3 with entity/location indexes.
[ ] Add global_goal_completion evaluator for get_rich and kill_stalker.
[ ] Add/verify LEAVE_ZONE PlanStep executor that sets has_left_zone.
[ ] Ensure ENGAGE_TARGET does not confirm kill before combat resolves.
[ ] Add prepare-for-hunt category-specific behavior.
[ ] Add target_not_found / target_moved repair rules.
```

Then add E2E tests:

```text
[ ] test_e2e_get_rich_from_spawn_to_leave_zone
[ ] test_e2e_kill_stalker_unknown_target_to_leave_zone
[ ] test_e2e_kill_stalker_known_target_to_leave_zone
[ ] test_e2e_kill_stalker_prepares_before_engage
[ ] test_e2e_kill_stalker_target_moved_repairs_plan
[ ] test_e2e_kill_stalker_emission_interrupts_then_resumes_hunt
```

---

# 15. Definition of done for "full v3 goal behavior"

This work is complete when both final scenarios pass:

## get_rich

```text
spawn
→ find artifacts / get money
→ sell artifacts
→ wealth goal reached
→ global_goal_completed
→ LEAVE_ZONE
→ has_left_zone
```

## kill_stalker

```text
spawn
→ locate/track target
→ prepare if needed
→ engage via combat system
→ confirm kill
→ global_goal_completed
→ LEAVE_ZONE
→ has_left_zone
```

and both scenarios show:

```text
ObjectiveDecision is the reason
ActivePlanV3 is the runtime source of truth
scheduled_action is child step
action_queue is empty
memory_v3 contains evidence
brain_trace is readable
compact export shows current objective/runtime
```
