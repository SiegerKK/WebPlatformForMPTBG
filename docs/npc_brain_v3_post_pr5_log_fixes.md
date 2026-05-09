# NPC Brain v3 — Post-PR5 Log Fixes: Exit Goal and Real Multi-Step ActivePlans

> Context: review of post-PR5 logs:
>
> - `stalker_Чувак_1_history.json`
> - `stalker_Чувак_1_full_debug.json`
>
> Current conclusion:
>
> ```text
> Brain v3 is active and works:
> - brain_v3_context is used;
> - active_plan_v3 exists;
> - scheduled_action is tagged as ActivePlan child step;
> - action_queue is empty;
> - compact export restores current_objective during active_plan_monitor.
> ```
>
> But the log exposes two important issues:
>
> ```text
> 1. get_rich / leave_zone completion is not fully wired.
> 2. Strategic objectives often produce one-step ActivePlans, so PR5 is not yet fully exploiting ActivePlan.
> ```
>
> Also there is a debug/trace issue:
>
> ```text
> active_plan_step_completed summaries have off-by-one output:
> "шаг 2/1, none", "шаг 3/2, none".
> ```

---

# 1. Why NPC did not leave the Zone after collecting money

## Observed state

From the full debug log:

```text
money = 6599
material_threshold = 5477
wealth_goal_target = 88060
global_goal = get_rich
current_goal = get_rich
global_goal_achieved = false
has_left_zone = false
```

So the NPC has passed `material_threshold`, but he has **not** reached `wealth_goal_target`.

## Interpretation

This is not a bug in this specific log.

`material_threshold` is a wealth gate / safety buffer. It means:

```text
"I have enough baseline resources to pursue my global goal more freely."
```

It does **not** mean:

```text
"I completed get_rich and should leave the Zone."
```

The actual final get_rich target is:

```text
wealth_goal_target = 88060
```

The NPC currently has:

```text
money = 6599
```

So he should not leave yet.

## Required clarification in code/docs/UI

The frontend/debug profile should make this distinction explicit:

```text
Material threshold:
  6599 / 5477 — passed

Wealth goal target:
  6599 / 88060 — not completed
```

Otherwise it looks like:

```text
NPC collected enough money but did not leave.
```

when actually:

```text
NPC collected enough money to pass the material gate, not enough to finish get_rich.
```

---

# 2. Real issue: completed get_rich must trigger LEAVE_ZONE

## Problem

Even though the current log is not a leave-zone bug, the code path is incomplete.

`needs.py` has logic:

```text
if global_goal_achieved and not has_left_zone:
    leave_zone = 1.0
```

But objective generation currently maps global objective mostly from `global_goal`.

For `global_goal = get_rich`, generator creates:

```text
FIND_ARTIFACTS
SELL_ARTIFACTS
```

It does not clearly create:

```text
LEAVE_ZONE
```

when:

```text
global_goal_achieved = true
has_left_zone = false
```

unless `global_goal` itself is already `"leave_zone"`.

## Required behavior

If any global goal is completed:

```text
agent.global_goal_achieved == true
agent.has_left_zone == false
```

then `LEAVE_ZONE` must become a high-priority objective regardless of the original global goal.

This applies to:

```text
get_rich completed
kill_stalker completed
unravel_zone_mystery completed
```

## Required objective generation rule

Add near the start of `generate_objectives()` after emergency/survival objectives or before global goal objective:

```python
if agent.get("global_goal_achieved") and not agent.get("has_left_zone"):
    _append_unique(
        result,
        Objective(
            key=OBJECTIVE_LEAVE_ZONE,
            source="global_goal_completed",
            urgency=max(0.9, float(need_result.scores.leave_zone)),
            expected_value=1.0,
            risk=0.2,
            time_cost=0.5,
            resource_cost=0.1,
            confidence=0.95,
            goal_alignment=1.0,
            memory_confidence=_objective_memory_refs_and_confidence(ctx, OBJECTIVE_LEAVE_ZONE)[1],
            reasons=("Глобальная цель выполнена — пора покинуть Зону",),
            source_refs=(f"global_goal_completed:{agent.get('global_goal')}",),
            metadata={"is_blocking": False, "completed_global_goal": agent.get("global_goal")},
        ),
    )
```

## Required goal-completion rule

Ensure `global_goal_achieved` is set when get_rich is completed.

For `get_rich`, recommended condition:

```text
liquid_wealth >= wealth_goal_target
```

where liquid wealth should probably be:

```text
money + inventory value
```

not equipped gear.

Alternative if design wants cash-only:

```text
money >= wealth_goal_target
```

But it must be explicit and consistent with docs/UI.

Recommended:

```text
money + inventory sell value >= wealth_goal_target
```

because an NPC carrying unsold artifacts is economically rich, but may still need to sell before leaving if exit requires cash.

## Required tests

### Test 1 — material threshold does not complete get_rich

```python
def test_get_rich_material_threshold_is_not_goal_completion():
    ...
```

Setup:

```text
money = 6599
material_threshold = 5477
wealth_goal_target = 88060
global_goal = get_rich
global_goal_achieved = false
```

Expected:

```text
LEAVE_ZONE is not selected only because material_threshold is passed.
NPC continues get_rich / sell_artifacts / find_artifacts.
```

### Test 2 — wealth target completes get_rich

```python
def test_get_rich_wealth_target_sets_global_goal_achieved():
    ...
```

Setup:

```text
money >= wealth_goal_target
global_goal = get_rich
```

Expected:

```text
global_goal_achieved = true
```

### Test 3 — completed global goal generates LEAVE_ZONE

```python
def test_completed_global_goal_generates_leave_zone_objective():
    ...
```

Setup:

```text
global_goal = get_rich
global_goal_achieved = true
has_left_zone = false
```

Expected:

```text
LEAVE_ZONE objective is generated.
LEAVE_ZONE wins unless immediate survival/emission blocks it.
```

### Test 4 — LEAVE_ZONE creates ActivePlan

```python
def test_leave_zone_objective_creates_active_plan():
    ...
```

Expected:

```text
active_plan_v3.objective_key = LEAVE_ZONE
scheduled_action.active_plan_objective_key = LEAVE_ZONE
```

---

# 3. Is one-step get_rich ActivePlan a bug?

## Short answer

For simple atomic objectives, one-step ActivePlans are fine.

Examples:

```text
RESTORE_WATER → consume_item
RESTORE_FOOD → consume_item
RESUPPLY_WEAPON → trade_buy_item
RESUPPLY_AMMO → trade_buy_item
REST → sleep_for_hours
```

But for strategic objectives, one-step ActivePlans are a problem.

Strategic objectives include:

```text
GET_MONEY_FOR_RESUPPLY
FIND_ARTIFACTS
SELL_ARTIFACTS
LEAVE_ZONE
HUNT_TARGET / future hunt decomposition
```

## What the log shows

The log has many sequences like:

```text
ActivePlan FIND_ARTIFACTS: 1/1 travel_to_location
completed
ActivePlan FIND_ARTIFACTS: 1/1 explore_location
completed
ActivePlan FIND_ARTIFACTS: 1/1 travel_to_location
completed
...
```

The full debug statistics show a strong pattern:

```text
FIND_ARTIFACTS plans:
  52 created
  all with steps_count = 1

GET_MONEY_FOR_RESUPPLY plans:
  7 created
  all with steps_count = 1

SELL_ARTIFACTS plans:
  7 created
  steps_count = 2
```

So `SELL_ARTIFACTS` already demonstrates a real multi-step ActivePlan:

```text
travel_to_location
trade_sell_item
```

But `FIND_ARTIFACTS` and `GET_MONEY_FOR_RESUPPLY` are still behaving like one-step wrappers around old planner output.

## Expected behavior

### FIND_ARTIFACTS

Should normally create:

```text
ActivePlan FIND_ARTIFACTS:
  1. travel_to_location
  2. explore_location
```

If the NPC is already at the target location:

```text
ActivePlan FIND_ARTIFACTS:
  1. explore_location
```

That one-step case is valid.

### GET_MONEY_FOR_RESUPPLY

Should usually be a higher-level money operation.

Minimum MVP:

```text
ActivePlan GET_MONEY_FOR_RESUPPLY:
  1. travel_to_anomaly
  2. explore_location
```

If artifact is found, after exploration either:

```text
Option A:
  repair/extend same ActivePlan:
    3. travel_to_trader
    4. trade_sell_item
```

or:

```text
Option B:
  complete money-search plan,
  then create SELL_ARTIFACTS plan.
```

Preferred for PR5 final behavior:

```text
Option A for get_rich loop:
  ActivePlan GET_MONEY_FOR_RESUPPLY/FIND_ARTIFACTS can expand to sell step
  if artifact is found.
```

But minimum acceptable:

```text
FIND_ARTIFACTS and GET_MONEY_FOR_RESUPPLY must at least combine travel + explore
when target is not current location.
```

### SELL_ARTIFACTS

Already correct:

```text
ActivePlan SELL_ARTIFACTS:
  1. travel_to_location
  2. trade_sell_item
```

### LEAVE_ZONE

Should create:

```text
ActivePlan LEAVE_ZONE:
  1. travel_to_exit
  2. leave_zone
```

If already at exit:

```text
ActivePlan LEAVE_ZONE:
  1. leave_zone
```

---

# 4. Required fix: strategic ActivePlan composition

## Problem

PR5 currently wraps selected planner result into ActivePlan.

If planner returns one step now and expects next decision after completion, ActivePlan becomes one-step too.

That preserves old PR4 behavior:

```text
decision
→ one step
→ complete
→ new decision
→ one step
```

instead of PR5 behavior:

```text
decision
→ multi-step ActivePlan
→ step lifecycle
→ repair/resume
```

## Required change

Add an ActivePlan composition layer after plan generation:

```text
ObjectiveDecision + Plan + AgentContext + BeliefState
→ composed ActivePlan steps
```

Recommended module:

```text
backend/app/games/zone_stalkers/decision/active_plan_composer.py
```

or extend `create_active_plan()` with objective-specific composition.

Preferred:

```text
new module active_plan_composer.py
```

because `active_plan_manager.py` should manage lifecycle, not planning semantics.

## API

```python
def compose_active_plan_steps(
    *,
    objective_key: str,
    base_plan: Plan,
    agent: dict[str, Any],
    state: dict[str, Any],
    world_turn: int,
) -> list[ActivePlanStep]:
    ...
```

`create_active_plan()` should accept either:

```text
composed steps
```

or be called after composition.

## Composition rules

### FIND_ARTIFACTS

If base plan starts with `travel_to_location` to anomaly target:

```text
travel_to_location(target)
explore_location(target)
```

If already at target and base plan is `explore_location`:

```text
explore_location(current_location)
```

If target unknown:

```text
use base plan as fallback
```

### GET_MONEY_FOR_RESUPPLY

If money strategy is artifact search:

```text
travel_to_location(anomaly)
explore_location(anomaly)
```

Optional later extension:

```text
if artifact found after explore:
  repair/extend plan with SELL_ARTIFACTS steps
```

### SELL_ARTIFACTS

Keep current behavior:

```text
travel_to_trader
trade_sell_item
```

### LEAVE_ZONE

Add once implemented:

```text
travel_to_exit
leave_zone
```

### RESUPPLY_* / RESTORE_* / REST / HEAL

One-step is fine.

## Tests

```python
def test_find_artifacts_composes_travel_and_explore_steps():
    ...
```

Expected:

```text
active_plan_v3.objective_key = FIND_ARTIFACTS
steps = [
  travel_to_location,
  explore_location
]
```

```python
def test_get_money_for_resupply_composes_travel_and_explore_steps():
    ...
```

Expected:

```text
active_plan_v3.objective_key = GET_MONEY_FOR_RESUPPLY
steps include travel_to_location and explore_location
```

```python
def test_sell_artifacts_remains_travel_then_sell():
    ...
```

Expected:

```text
steps = [
  travel_to_location,
  trade_sell_item
]
```

```python
def test_atomic_restore_water_can_be_one_step():
    ...
```

Expected:

```text
RESTORE_WATER may have one consume_item step
```

---

# 5. Required fix: off-by-one lifecycle summary/payload

## Problem

The log contains many summaries like:

```text
ActivePlan FIND_ARTIFACTS: active_plan_step_completed (шаг 2/1, none)
ActivePlan SELL_ARTIFACTS: active_plan_step_completed (шаг 3/2, none)
```

This happens because trace/memory payload is written after:

```text
active_plan.advance_step(...)
```

At that point:

```text
current_step_index points to next step or beyond the end
current_step is None
```

So the completed step is lost in the summary.

## Required change

Capture completed step data before advancing:

```python
completed_step_index = active_plan.current_step_index
completed_step = active_plan.current_step
completed_step_kind = completed_step.kind if completed_step else "unknown"
steps_count = len(active_plan.steps)

active_plan.advance_step(world_turn)

write_active_plan_step_completed(
    step_index=completed_step_index,
    step_kind=completed_step_kind,
    human_step_number=completed_step_index + 1,
    steps_count=steps_count,
)
```

Do not use:

```text
active_plan.current_step_index + 1
active_plan.current_step
```

after advance for completed-step logs.

## Trace/memory payload

For step completion event, payload should include:

```json
{
  "action_kind": "active_plan_step_completed",
  "active_plan_id": "...",
  "objective_key": "FIND_ARTIFACTS",
  "completed_step_index": 0,
  "completed_step_number": 1,
  "completed_step_kind": "travel_to_location",
  "steps_count": 2,
  "next_step_index": 1,
  "next_step_kind": "explore_location"
}
```

Keep old fields if needed for compatibility:

```json
{
  "step_index": 0,
  "step_kind": "travel_to_location"
}
```

But these must refer to completed step, not next step.

## Plan completion summary

For `active_plan_completed`, summary should be:

```text
ActivePlan FIND_ARTIFACTS: completed, 2/2 steps completed.
```

not:

```text
шаг 3/2, none
```

## Tests

```python
def test_active_plan_step_completed_logs_completed_step_not_next_step():
    ...
```

Expected:

```text
summary contains "шаг 1/2 travel_to_location"
not "шаг 2/2 explore_location"
not "none"
```

```python
def test_active_plan_completed_summary_has_no_off_by_one():
    ...
```

Expected:

```text
summary contains "2/2 steps completed"
not "шаг 3/2"
```

---

# 6. Required fix: reduce lifecycle spam in compact history

## Problem

The compact story is now readable, but ActivePlan lifecycle entries are too noisy:

```text
active_plan_created
active_plan_step_started
active_plan_step_completed
active_plan_completed
```

For one-step plans this creates four timeline entries for one action.

After multi-step composition, the issue becomes less severe, but still worth improving.

## Required change

In compact export timeline, group ActivePlan lifecycle entries by `active_plan_id`.

Recommended compact representation:

```text
ActivePlan FIND_ARTIFACTS completed
  ✓ travel_to_location
  ✓ explore_location
```

or:

```json
{
  "category": "decision",
  "title": "ActivePlan FIND_ARTIFACTS completed",
  "objective_key": "FIND_ARTIFACTS",
  "active_plan_id": "...",
  "steps": [
    {"kind": "travel_to_location", "status": "completed"},
    {"kind": "explore_location", "status": "completed"}
  ]
}
```

## Keep full debug unchanged

Full debug should still contain raw lifecycle events.

Compact history should group them for readability.

## Priority

```text
MEDIUM
```

Not required for behavior, but important for the new debug UX.

---

# 7. Required fix: clarify wealth progress in frontend

## Problem

Profile currently shows money and goals, but does not clearly distinguish:

```text
material_threshold
wealth_goal_target
```

In the reviewed log, this caused the question:

```text
Why did NPC not leave after collecting money?
```

## Required UI addition

In NPC profile Goals / Brain panel:

```text
Wealth progress:
  Liquid wealth: 6599
  Material threshold: 5477 — passed
  Wealth goal target: 88060 — not reached
```

If `global_goal_achieved`:

```text
Global goal completed: yes
Next objective should be LEAVE_ZONE
```

If not:

```text
Global goal completed: no
```

## Required compact export addition

In `agent` or `npc_brain` section:

```json
"wealth_progress": {
  "money": 6599,
  "liquid_wealth": 8099,
  "material_threshold": 5477,
  "material_threshold_passed": true,
  "wealth_goal_target": 88060,
  "wealth_goal_reached": false,
  "global_goal_achieved": false
}
```

Use the same wealth definition as backend.

## Tests/manual checks

Manual profile check:

```text
NPC with money > material_threshold but < wealth_goal_target:
  UI says material threshold passed, wealth target not reached.
```

---

# 8. Required fix: global-goal completion memory

## Problem

When `global_goal_achieved` becomes true, this should be visible in memory and brain trace.

## Required behavior

When get_rich completes:

```text
write memory:
  action_kind = global_goal_completed
  global_goal = get_rich
  wealth_goal_target = ...
  liquid_wealth = ...

then objective generation should select LEAVE_ZONE.
```

## Memory v3 bridge

Map:

```text
global_goal_completed → goal layer
```

Tags:

```text
global_goal
goal:get_rich
completion
```

## Test

```python
def test_global_goal_completed_memory_written_when_get_rich_target_reached():
    ...
```

Expected:

```text
legacy memory has action_kind=global_goal_completed
memory_v3 has kind=global_goal_completed
next objective = LEAVE_ZONE
```

---

# 9. Acceptance checklist

This follow-up is complete when:

```text
[ ] Passing material_threshold does not imply leaving the Zone.
[ ] UI/export clearly show material_threshold vs wealth_goal_target.
[ ] get_rich completion sets global_goal_achieved when wealth target is reached.
[ ] global_goal_achieved + not has_left_zone generates LEAVE_ZONE objective.
[ ] LEAVE_ZONE creates ActivePlan.
[ ] FIND_ARTIFACTS normally composes travel + explore into one ActivePlan.
[ ] GET_MONEY_FOR_RESUPPLY normally composes travel + explore into one ActivePlan.
[ ] SELL_ARTIFACTS stays travel + sell.
[ ] Atomic actions may remain one-step ActivePlans.
[ ] active_plan_step_completed logs completed step, not next/none step.
[ ] active_plan_completed has no "шаг 3/2" or "шаг 2/1" off-by-one.
[ ] Compact history groups ActivePlan lifecycle events or at least becomes less noisy.
[ ] Tests cover strategic multi-step plan composition.
[ ] Tests cover get_rich → global_goal_achieved → LEAVE_ZONE.
```

---

# 10. Priority

## Blockers

```text
1. Strategic ActivePlan composition for FIND_ARTIFACTS / GET_MONEY_FOR_RESUPPLY.
2. Off-by-one lifecycle trace/memory.
3. get_rich completion → LEAVE_ZONE objective path.
```

## High

```text
4. UI/export wealth progress clarification.
5. global_goal_completed memory.
```

## Medium

```text
6. Compact history grouping of ActivePlan lifecycle events.
```

---

# 11. Expected behavior after fixes

## get_rich not completed

```text
money = 6599
material_threshold = 5477
wealth_goal_target = 88060

Result:
  material gate passed
  global_goal_achieved = false
  continue get_rich loop
```

## get_rich completed

```text
liquid_wealth >= wealth_goal_target

Result:
  global_goal_achieved = true
  objective = LEAVE_ZONE
  ActivePlan LEAVE_ZONE:
    1. travel_to_exit
    2. leave_zone
```

## strategic artifact search

```text
Objective = FIND_ARTIFACTS

ActivePlan:
  1. travel_to_location
  2. explore_location
```

## money-for-resupply

```text
Objective = GET_MONEY_FOR_RESUPPLY

ActivePlan:
  1. travel_to_anomaly
  2. explore_location
  optional later:
    3. travel_to_trader
    4. sell_artifacts
```

## lifecycle logs

```text
ActivePlan FIND_ARTIFACTS: step 1/2 travel_to_location completed.
ActivePlan FIND_ARTIFACTS: step 2/2 explore_location completed.
ActivePlan FIND_ARTIFACTS completed: 2/2 steps completed.
```

No more:

```text
шаг 2/1, none
шаг 3/2, none
```
