# NPC Brain v3 — PR 5 Remaining Fixes: ActivePlan Runtime Integration

> Branch: `copilot/implement-pr-5-for-npc-brain-v3`  
> Base: `copilot/implement-pr-4-npc-brain-v3`  
> Purpose: список оставшихся правок для доведения PR 5 до закрытия.
>
> Current status:
>
> ```text
> PR 5 already adds:
> - ActivePlanV3 model;
> - ActivePlanStep model;
> - active_plan_manager.py;
> - unit/scenario tests for model + manager.
> ```
>
> Main gap:
>
> ```text
> ActivePlanV3 exists as a library, but it is not yet the source of truth
> in the real tick/runtime pipeline.
> ```
>
> PR 5 should not stop at data model and helper tests.  
> It must make long-running NPC execution actually go through `active_plan_v3`.

---

# 1. Current PR 5 state

Current diff against PR 4 is small:

```text
backend/app/games/zone_stalkers/decision/models/active_plan.py
backend/app/games/zone_stalkers/decision/active_plan_manager.py
backend/tests/decision/v3/test_active_plan_v3.py
```

This is a good foundation, but it does not yet change:

```text
tick_rules.py
planner.py
brain_trace.py
AgentProfile runtime behavior
scheduled_action/action_queue ownership
```

So the game likely still behaves mostly like PR 4:

```text
ObjectiveDecision
→ Intent adapter
→ Plan
→ scheduled_action/action_queue
```

instead of the intended PR 5 chain:

```text
ObjectiveDecision
→ Intent adapter
→ Plan
→ ActivePlanV3
→ current ActivePlanStep
→ scheduled_action only as runtime execution detail
→ step completion/repair/resume
```

---

# 2. PR 5 target invariant

After PR 5:

```text
ActivePlanV3 is the source of truth for long-running behavior.
```

Meaning:

```text
Objective = why
Intent = execution bridge
Plan = initial step recipe
ActivePlanV3 = persistent runtime plan
scheduled_action = currently executing step only
action_queue = legacy fallback / should not be primary for v3 bots
```

The NPC should no longer look like a sequence of unrelated PR4 decisions:

```text
FIND_ARTIFACTS
travel
new decision
explore
new decision
sell
new decision
resupply
```

It should look like one persistent operation:

```text
ActivePlan FIND_ARTIFACTS:
  1. travel_to_location
  2. explore_location
  3. pickup_artifact
  4. travel_to_trader
  5. sell_artifacts

interrupted by emission:
  pause/repair shelter
  resume FIND_ARTIFACTS
```

---

# 3. Required fix A — integrate ActivePlan into `tick_zone_map`

## Problem

`tick_rules.py` currently initializes:

```python
agent.setdefault("active_plan_v3", None)
```

but the normal runtime still follows:

```text
scheduled_action processing
→ legacy PlanMonitor
→ if no scheduled_action, run _run_bot_decision_v2
```

`active_plan_v3` is not assessed or executed as the primary long-running plan.

## Required change

Add an ActivePlan phase to the bot runtime.

Recommended order inside `tick_zone_map`:

```text
1. Process scheduled_action runtime completion/progress.
2. For v3 bot with active_plan_v3:
   - assess active plan;
   - continue/repair/complete/abort;
   - start next step if no scheduled_action.
3. For v3 bot without active_plan_v3:
   - run objective decision;
   - build plan;
   - create ActivePlanV3;
   - start first step.
4. Legacy fallback only if ActivePlan unavailable/disabled.
```

## Pseudocode

```python
from app.games.zone_stalkers.decision.active_plan_manager import (
    assess_active_plan_v3,
    get_active_plan,
    save_active_plan,
    clear_active_plan,
    create_active_plan,
    repair_active_plan,
)

def _process_active_plan_v3(agent_id, agent, state, world_turn, events):
    active_plan = get_active_plan(agent)
    if active_plan is None:
        return "no_plan"

    operation, reason = assess_active_plan_v3(agent, state, world_turn)

    if operation == "continue":
        if not agent.get("scheduled_action"):
            _start_or_continue_active_plan_step(agent_id, agent, active_plan, state, world_turn, events)
        return "handled"

    if operation == "repair":
        repaired = repair_active_plan(agent, active_plan, reason or "repair", world_turn)
        save_active_plan(agent, repaired)
        _write_active_plan_trace(...)
        _write_active_plan_memory(...)
        return "handled"

    if operation == "complete":
        _write_active_plan_completed(...)
        clear_active_plan(agent)
        return "handled"

    if operation == "abort":
        _write_active_plan_aborted(...)
        clear_active_plan(agent)
        return "needs_redecision"
```

## Important

If an agent has an active plan and it is valid, do not run a full new `_run_bot_decision_v2` each tick.

The active plan should continue unless:

```text
- current step completed;
- blocking emergency appears;
- plan assumptions break;
- objective replacement is justified.
```

## Priority

```text
BLOCKER
```

---

# 4. Required fix B — create ActivePlan from real ObjectiveDecision

## Problem

`create_active_plan(objective_decision, world_turn, plan)` exists, but it is not called from the real PR4 objective pipeline.

## Required change

In `_run_bot_decision_v2_inner()` or equivalent PR4 decision function, after:

```text
generate_objectives
choose_objective
objective_to_intent
build_plan
plan feasibility validation
```

create and save the ActivePlan:

```python
active_plan = create_active_plan(
    objective_decision=objective_decision,
    world_turn=world_turn,
    plan=selected_plan,
)
save_active_plan(agent, active_plan)
```

Then start first step:

```python
_start_or_continue_active_plan_step(...)
```

or return enough data for caller to start it.

## Preserve PR4 trace

The PR4 trace should still show:

```text
active_objective
objective_scores
alternatives
memory_used
adapter intent
```

But now also include:

```text
active_plan_id
active_plan_status
active_plan_current_step
active_plan_steps_count
```

## Priority

```text
BLOCKER
```

---

# 5. Required fix C — ActivePlanStep → scheduled_action bridge

## Problem

`ActivePlanV3.steps` are currently passive data. The game executor still uses `scheduled_action`.

PR 5 must define how an `ActivePlanStep` starts actual execution.

## Required behavior

When current step is `pending` and no `scheduled_action` exists:

```text
1. Convert ActivePlanStep to scheduled_action using existing plan/action bridge.
2. Mark step.status = running.
3. Set step.started_turn = world_turn.
4. Save active_plan_v3.
5. Write trace/memory event.
```

When scheduled action finishes:

```text
1. Mark current step completed.
2. Set completed_turn.
3. Advance current_step_index.
4. If next step exists, start next step.
5. If no next step, complete active plan.
```

When scheduled action aborts/fails:

```text
1. Mark current step failed or request repair.
2. Store failure_reason.
3. Save active_plan_v3.
4. Do not silently lose plan context.
```

## Implementation guidance

Add a bridge helper:

```python
def start_active_plan_step(
    agent_id: str,
    agent: dict[str, Any],
    active_plan: ActivePlanV3,
    state: dict[str, Any],
    world_turn: int,
    events: list[dict[str, Any]],
) -> bool:
    ...
```

It should reuse the existing PlanStep → scheduled_action logic as much as possible.

If existing code only knows how to schedule a `Plan` or `PlanStep`, create a tiny Plan wrapper:

```python
Plan(
    intent_kind="active_plan_step",
    steps=[current_step_as_plan_step],
    ...
)
```

But do not let this create a second action_queue as source of truth.

## Priority

```text
BLOCKER
```

---

# 6. Required fix D — scheduled_action completion must advance ActivePlan

## Problem

Existing scheduled action processing can complete:

```text
travel
explore
sleep
trade
consume
```

but ActivePlan is not advanced when that happens.

## Required change

Where `_process_scheduled_action(...)` detects completion, add hook:

```python
_on_scheduled_action_completed(agent_id, agent, sched, state, world_turn, events)
```

If `agent["active_plan_v3"]` exists:

```python
active_plan = get_active_plan(agent)
step = active_plan.current_step

if _scheduled_action_matches_active_step(sched, step):
    active_plan.advance_step(world_turn)
    save_active_plan(agent, active_plan)
    write active_plan_step_completed trace/memory

    if active_plan.is_complete:
        write active_plan_completed trace/memory
        clear_active_plan(agent)
    else:
        start next step if no scheduled_action
```

## Matching rule

Use robust matching:

```text
scheduled_action.type
scheduled_action.target_id/final_target_id/item_type
step.kind
step.payload target_id/location_id/item_category/item_type
```

Do not require perfect equality if legacy scheduled_action has slightly different keys, but do require enough to avoid advancing wrong plan step.

## Priority

```text
BLOCKER
```

---

# 7. Required fix E — make `active_plan_v3` the owner of long-running state

## Problem

If PR 5 keeps all three as independent sources:

```text
active_plan_v3
scheduled_action
action_queue
```

the system becomes more confusing than PR4.

## Required rule

For v3 bots:

```text
active_plan_v3 = persistent plan source of truth
scheduled_action = current runtime action only
action_queue = legacy fallback only
```

## Required change

When creating ActivePlan:

```text
clear old action_queue unless explicitly derived from active plan
```

When active plan starts a step:

```text
scheduled_action is created from current ActivePlanStep
```

When scheduled action finishes:

```text
ActivePlan advances
```

When active plan aborts/completes:

```text
scheduled_action cleared if it belongs to active plan
action_queue cleared if it belongs to active plan
```

## Debug expectation

Agent dump should show:

```json
{
  "active_plan_v3": {
    "objective_key": "FIND_ARTIFACTS",
    "status": "active",
    "current_step_index": 1,
    "steps": [...]
  },
  "scheduled_action": {
    "type": "explore_anomaly_location",
    "active_plan_id": "...",
    "active_plan_step_index": 1
  },
  "action_queue": []
}
```

## Priority

```text
BLOCKER
```

---

# 8. Required fix F — normalize PlanStep payload contract

## Problem

`active_plan_manager.py` checks fields like:

```text
step.payload.trader_id
step.payload.location_id
step.payload.required_item
```

But real planner steps often use:

```text
target_id
final_target_id
item_category
item_type
preferred_item_types
forced_resupply_category
reason
```

So runtime repair checks may not trigger.

## Required canonical payload fields

Normalize or support aliases.

### Travel/explore target

Canonical:

```text
location_id
```

Accepted aliases:

```text
target_id
final_target_id
```

Helper:

```python
def _step_location_id(step_or_payload):
    return payload.get("location_id") or payload.get("target_id") or payload.get("final_target_id")
```

### Trade

Canonical:

```text
trader_id
trader_location_id
item_category
item_type
forced_resupply_category
```

If trader id is unknown but trader location is known, repair check should be location-based.

### Consume

Canonical:

```text
item_id
item_type
required_item_type
```

Accepted aliases:

```text
required_item
```

### Memory target

Canonical:

```text
memory_ref
location_id
entity_id
```

## Required changes

1. Update planner to output canonical fields where possible.
2. Update active_plan_manager to support aliases safely.
3. Update tests to use real planner-like payloads, not only synthetic payloads.

## Required tests

```python
def test_active_plan_repair_uses_target_id_alias_for_location():
    ...
```

```python
def test_active_plan_supply_check_uses_item_type_alias():
    ...
```

```python
def test_active_plan_trader_check_uses_trader_location_when_trader_id_missing():
    ...
```

## Priority

```text
HIGH
```

---

# 9. Required fix G — fill `memory_refs` from objective source refs

## Problem

`create_active_plan()` copies:

```python
source_refs = list(objective.source_refs)
```

but leaves:

```python
memory_refs = []
```

PR 5 requires `memory_refs` as evidence chain.

## Required change

In `create_active_plan()`:

```python
source_refs = list(objective.source_refs) if objective.source_refs else []
memory_refs = [
    ref.removeprefix("memory:")
    for ref in source_refs
    if isinstance(ref, str) and ref.startswith("memory:")
]
```

Then:

```python
return ActivePlanV3(
    ...
    source_refs=source_refs,
    memory_refs=memory_refs,
)
```

## Required tests

```python
def test_create_active_plan_extracts_memory_refs_from_source_refs():
    ...
```

Input:

```text
source_refs = ("memory:mem_trader_1", "world:loc_a")
```

Expected:

```text
source_refs = ["memory:mem_trader_1", "world:loc_a"]
memory_refs = ["mem_trader_1"]
```

## Priority

```text
HIGH
```

---

# 10. Required fix H — real pause/repair/resume behavior

## Problem

Current `repair_active_plan()` mostly resets the current step to pending.

This is not enough for PR 5 runtime behavior.

## Required repair behavior by reason

### `emission_interrupt`

Expected:

```text
1. Pause current plan.
2. Create emergency shelter objective/step.
3. Execute shelter behavior.
4. After emission ends, resume original active plan from same or repaired step.
```

Implementation options:

```text
Option A:
  ActivePlan status = paused
  agent has temporary emergency active plan
  original plan stored under paused_plan_v3

Option B:
  ActivePlan status = repairing
  insert emergency shelter steps before current step
  after shelter steps, resume current step

Option C:
  Abort current scheduled_action, keep active_plan_v3 active with repair reason,
  run one-off emergency scheduled_action, then resume.
```

Recommended for PR 5 MVP:

```text
Option B: insert repair substeps into current ActivePlan.
```

Example:

```text
FIND_ARTIFACTS:
  0 travel_to_anomaly completed
  1 explore_anomaly running

emission_interrupt:
  insert:
    travel_to_shelter
    wait_in_shelter

after shelter:
  continue step 1 or re-evaluate anomaly step
```

### `target_location_empty`

Expected:

```text
1. Mark current location memory as confirmed_empty/stale.
2. Find alternative target/source.
3. Replace current explore/travel step.
4. Continue plan.
```

For PR 5 MVP, acceptable:

```text
request re-evaluation and create new FIND_ARTIFACTS plan,
but preserve active plan history and repair_count.
```

### `trader_unavailable`

Expected:

```text
1. Try remembered/known alternative trader.
2. Replace trader/travel step.
3. If none found, request objective re-evaluation.
```

### `supplies_consumed_mid_plan`

Expected:

```text
1. Insert RESTORE/RESUPPLY substeps.
2. After supply step, resume original plan.
```

## Required tests

```python
def test_emission_interrupt_inserts_shelter_steps_or_pauses_plan():
    ...
```

```python
def test_after_emission_plan_resumes_original_objective():
    ...
```

```python
def test_confirmed_empty_repair_reselects_alternative_location():
    ...
```

```python
def test_resupply_subplan_then_resume_original_plan():
    ...
```

## Priority

```text
HIGH
```

---

# 11. Required fix I — ActivePlan brain_trace events

## Problem

Debug UI can show `active_plan_v3`, but backend does not write explanatory trace events for ActivePlan lifecycle.

## Required events

Add trace event helpers, for example:

```python
write_active_plan_trace(
    agent,
    world_turn=world_turn,
    event="active_plan_created",
    active_plan=active_plan,
    reason=...,
    summary=...,
    state=state,
)
```

Supported event names:

```text
active_plan_created
active_plan_continue
active_plan_step_started
active_plan_step_completed
active_plan_repair_requested
active_plan_repaired
active_plan_paused
active_plan_resumed
active_plan_aborted
active_plan_completed
```

## Trace payload should include

```text
active_plan_id
objective_key
status
current_step_index
current_step_kind
steps_count
repair_count
reason
source_refs
memory_refs
```

## Example summary

```text
ActivePlan FIND_ARTIFACTS: шаг 2/5 explore_location продолжается.
```

```text
ActivePlan FIND_ARTIFACTS: ремонт из-за emission_interrupt, добавлен shelter subplan.
```

## Priority

```text
HIGH
```

---

# 12. Required fix J — ActivePlan memory timeline events

## Problem

The profile MemoryTimeline will be much more useful if ActivePlan lifecycle is visible as story events.

## Required memory events

Use `_add_memory(...)` or equivalent bridge-aware memory write.

Recommended action kinds:

```text
active_plan_created
active_plan_step_started
active_plan_step_completed
active_plan_repair_requested
active_plan_repaired
active_plan_paused
active_plan_resumed
active_plan_aborted
active_plan_completed
```

## Required fields

```json
{
  "action_kind": "active_plan_step_completed",
  "active_plan_id": "...",
  "objective_key": "FIND_ARTIFACTS",
  "step_index": 1,
  "step_kind": "explore_location",
  "repair_count": 0,
  "reason": "completed"
}
```

## Memory v3 bridge

Add mappings in legacy bridge if needed:

```text
active_plan_created → goal/episodic
active_plan_step_completed → episodic
active_plan_repair_requested → goal/threat depending on reason
active_plan_completed → goal
active_plan_aborted → goal/threat
```

## Priority

```text
MEDIUM/HIGH
```

---

# 13. Required fix K — ActivePlan frontend/profile verification

## Current state

Frontend already has `RuntimeActionPanel` and reads `active_plan_v3`.

But once backend actually populates ActivePlan, verify it displays:

```text
objective_key
status
current_step_index
steps
step status
repair_count
abort_reason
source_refs
memory_refs
```

## Required UI behavior

When `active_plan_v3` exists:

```text
RuntimeActionPanel should show ActivePlan first.
scheduled_action should appear as current execution step, not as separate goal.
```

Example:

```text
ActivePlan v3
Objective: FIND_ARTIFACTS
Status: active
Current step: 2/5 explore_location
Repair count: 1

Steps:
✅ travel_to_location
🔄 explore_location
⏳ trade_sell_item
```

## Compact export

`npc_history_v1` should include:

```text
agent.active_plan_v3
npc_brain.current_runtime.active_plan
story_timeline active_plan events
```

## Priority

```text
MEDIUM
```

---

# 14. Required fix L — tick-level integration tests

## Problem

Current PR 5 tests mostly call model/manager directly. They do not prove the game loop uses ActivePlan.

## Required tests

Add tests through `tick_zone_map`.

### 14.1. Objective decision creates ActivePlan

```python
def test_tick_objective_decision_creates_active_plan_v3():
    ...
```

Expected:

```text
after tick:
  agent.active_plan_v3 is not None
  active_plan_v3.objective_key == selected objective
  active_plan_v3.steps not empty
  scheduled_action linked to active plan first step
```

### 14.2. Scheduled action completion advances ActivePlan

```python
def test_tick_scheduled_action_completion_advances_active_plan_step():
    ...
```

Expected:

```text
current_step_index increases
previous step status = completed
next step starts or waits pending
```

### 14.3. ActivePlan continues without re-deciding every tick

```python
def test_tick_active_plan_continue_skips_new_objective_decision():
    ...
```

Expected:

```text
active_plan_v3 remains same id
no new objective_decision memory every tick
brain_trace shows active_plan_continue / plan_monitor
```

### 14.4. Emission repairs/pauses ActivePlan

```python
def test_tick_emission_interrupt_repairs_active_plan():
    ...
```

Expected:

```text
repair_count increments
repair reason = emission_interrupt
shelter behavior scheduled
original objective preserved
```

### 14.5. After emission, plan resumes

```python
def test_tick_after_emission_resumes_original_active_plan():
    ...
```

Expected:

```text
objective_key remains original
plan continues after shelter
```

### 14.6. Confirmed empty triggers repair/reselection

```python
def test_tick_confirmed_empty_repairs_artifact_plan():
    ...
```

Expected:

```text
current target invalidated
repair_count increments
alternative target chosen or objective re-evaluated
```

### 14.7. Resupply subplan then resume

```python
def test_tick_resupply_subplan_then_resume_original_plan():
    ...
```

Expected:

```text
supply need interrupts
resupply step completes
original active plan resumes
```

### 14.8. Completed active plan clears itself

```python
def test_tick_completed_active_plan_clears_and_writes_memory():
    ...
```

Expected:

```text
active_plan_v3 is None or status completed then cleared
memory has active_plan_completed
```

### 14.9. Aborted plan clears and re-evaluates

```python
def test_tick_aborted_active_plan_re_evaluates_objective():
    ...
```

Expected:

```text
active_plan_v3 removed
new objective decision can happen
memory has active_plan_aborted
```

## Priority

```text
BLOCKER
```

---

# 15. Required fix M — prevent duplicate decision spam

## Problem

Before PR 5, NPC can write many `objective_decision` entries because each decision tick is separate.

After PR 5, if a plan is active, the NPC should not keep writing new objective decisions every tick.

## Required rule

If `active_plan_v3.status == active` and no blocking repair/replacement is required:

```text
do not call full objective selection;
do not write new objective_decision memory;
write active_plan_continue trace/memory only if meaningful or rate-limited.
```

## Dedup/rate limit

For memory timeline:

```text
active_plan_continue should be deduped or sampled.
```

Recommended:

```text
write active_plan_continue memory only when:
- step starts;
- step completes;
- repair/abort/completion happens;
- or every N turns if needed for debug, but not every tick.
```

## Priority

```text
HIGH
```

---

# 16. Required fix N — death/left-zone cleanup

## Problem

PR 1/2 already cleaned scheduled_action for dead NPCs. PR 5 must do the same for ActivePlan.

## Required behavior

When NPC dies:

```text
active_plan_v3 should be cleared or marked aborted/completed terminal.
scheduled_action cleared.
action_queue cleared.
memory event active_plan_aborted reason=death.
```

When NPC leaves zone:

```text
active_plan_v3 cleared or marked completed if objective was LEAVE_ZONE.
```

## Required tests

```python
def test_dead_npc_clears_active_plan_v3():
    ...
```

```python
def test_leave_zone_completes_or_clears_active_plan_v3():
    ...
```

## Priority

```text
HIGH
```

---

# 17. Required fix O — update PR5 documentation after implementation

After implementation, update:

```text
docs/npc_brain_v3/05_pr5_active_plan.md
```

Add final details:

```text
1. Runtime integration order in tick_zone_map.
2. ActivePlanStep → scheduled_action bridge.
3. Step completion advancement.
4. Repair strategies implemented in PR5 MVP.
5. Memory/trace events.
6. Frontend/debug behavior.
7. Known limitations left after PR5.
```

Do not leave the doc only as high-level contract if the code now has concrete behavior.

## Priority

```text
MEDIUM
```

---

# 18. Acceptance checklist for closing PR 5

PR 5 can be closed when:

```text
[ ] ActivePlanV3 is created from real ObjectiveDecision in runtime.
[ ] active_plan_v3 is saved on agent and visible in full/compact export.
[ ] scheduled_action is linked to active_plan_id/current_step_index.
[ ] ActivePlanStep pending → running when scheduled_action starts.
[ ] scheduled_action completion advances ActivePlan step.
[ ] completed ActivePlan writes memory/trace and clears or marks terminal.
[ ] aborted ActivePlan writes memory/trace and clears or marks terminal.
[ ] ActivePlan continue prevents repeated objective_decision spam.
[ ] emission interrupt repairs/pauses plan and shelters NPC.
[ ] after emission, original objective can resume or be re-evaluated.
[ ] confirmed_empty can trigger repair/reselection.
[ ] supplies_consumed_mid_plan can trigger repair/subplan.
[ ] trader_unavailable can trigger repair/reselection.
[ ] memory_refs are filled from objective source_refs.
[ ] brain_trace has active_plan lifecycle events.
[ ] memory timeline has active_plan lifecycle entries.
[ ] frontend RuntimeActionPanel shows active_plan_v3 meaningfully.
[ ] tick-level tests prove runtime integration.
[ ] existing PR1/PR2/PR3/PR4 tests still pass.
```

---

# 19. Recommended implementation order

1. Fill `memory_refs` in `create_active_plan`.
2. Normalize payload helpers in `active_plan_manager`.
3. Add ActivePlan lifecycle trace/memory helper functions.
4. Add ActivePlan processing phase in `tick_zone_map`.
5. Create ActivePlan after PR4 objective decision.
6. Implement ActivePlanStep → scheduled_action bridge.
7. Hook scheduled_action completion to `active_plan.advance_step`.
8. Add ActivePlan repair/abort/completion memory events.
9. Add basic repair strategies:
   - emission_interrupt;
   - target_location_empty;
   - trader_unavailable;
   - supplies_consumed_mid_plan.
10. Prevent repeated objective decision spam while ActivePlan is valid.
11. Add tick-level tests.
12. Update frontend/manual check.
13. Update `05_pr5_active_plan.md`.

---

# 20. What is not required in PR 5

Do not implement the full kill-stalker operation in PR 5.

Out of scope:

```text
full TargetBelief
ambush/intercept AI
social consequences
revenge/reputation
advanced combat tactics
Redis/vector memory backend
```

PR 5 should provide the plan lifecycle infrastructure that will support those mechanics later.

Full kill-stalker mechanics remain in:

```text
docs/npc_brain_v3/07_post_pr5_kill_stalker_goal.md
```

---

# 21. Final expected behavior after PR 5

## Before PR 5

```text
NPC decides FIND_ARTIFACTS.
Starts travel.
Later makes another decision.
Starts explore.
Later makes another decision.
Sells artifact.
```

## After PR 5

```text
NPC creates ActivePlan FIND_ARTIFACTS:
  0 travel_to_anomaly
  1 explore_anomaly
  2 collect_artifact
  3 travel_to_trader
  4 sell_artifacts

Turn N:
  step 0 running

Turn N+X:
  step 0 completed
  step 1 running

Emission:
  plan repaired/paused
  shelter behavior inserted/executed

After emission:
  original FIND_ARTIFACTS plan resumes or repairs target

Completion:
  active_plan_completed memory
  active_plan_v3 cleared/terminal
```

This is the real PR 5 milestone.

