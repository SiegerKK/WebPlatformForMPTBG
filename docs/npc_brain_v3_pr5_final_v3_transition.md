# NPC Brain v3 — PR 5 Final Transition Plan: Remove v1/v2 Runtime Rudiments

> Branch: `copilot/implement-pr-5-for-npc-brain-v3`  
> Goal: fully finish the transition to NPC Brain v3 and close PR 5.
>
> Note:
>
> ```text
> Branch divergence with PR 4 can be ignored for this task.
> Focus only on making PR 5 a complete v3 transition.
> ```
>
> Current problem:
>
> ```text
> ActivePlan v3 is now partially integrated into runtime,
> but the codebase still exposes and sometimes relies on v1/v2-era concepts:
>
> - _run_bot_decision_v2 naming;
> - _v2_context;
> - brain_trace decision="new_intent";
> - objective decision still routed through intent-first wording;
> - scheduled_action/action_queue can still look like plan source of truth;
> - legacy memory timeline remains primary in some places;
> - PlanMonitor still owns some behavior that should belong to ActivePlan lifecycle;
> - tests still accept old v2 markers.
> ```
>
> PR 5 should close the migration:
>
> ```text
> Brain v3 = the only runtime decision system for bot NPCs.
> ```

---

# 1. Final Brain v3 invariants

After PR 5, the following must be true.

## 1.1. Objective is the reason

```text
ObjectiveDecision is the only high-level decision result.
```

The selected objective explains:

```text
why NPC is doing something
what alternatives existed
what needs/memory/constraints influenced the choice
```

## 1.2. ActivePlan is the runtime source of truth

```text
ActivePlanV3 is the persistent source of truth for long-running NPC behavior.
```

`schedule_action` is only:

```text
currently executing runtime step
```

`action_queue` is only:

```text
legacy compatibility fallback or should be empty for v3 bots
```

## 1.3. Intent is not a public decision concept

Intent may still exist internally as an execution adapter:

```text
Objective → ExecutionIntent → PlanStep
```

But the UI, memory and trace should not present intent as the primary decision.

Public wording should be:

```text
Selected objective: FIND_ARTIFACTS
Execution adapter: get_rich
ActivePlan step: travel_to_location
```

not:

```text
Selected intent: get_rich
```

## 1.4. Memory v3 is primary

`memory_v3` is the primary structured memory store.

Legacy `agent["memory"]` may temporarily remain as:

```text
human-readable story timeline
bridge source for old events
compatibility display
```

but not as the primary reasoning memory.

## 1.5. Debug UI is v3-first

NPC profile/debug export must show:

```text
current objective
active plan
current step
objective ranking
needs/constraints
memory_used
runtime action
```

Legacy/v2 fields must be hidden under raw debug or marked as legacy compatibility.

---

# 2. Required fix A — rename/remove v2 decision runtime API

## Problem

Core runtime still uses names like:

```text
_run_bot_decision_v2
_run_bot_decision_v2_inner
_v2_context
v2_decision
decision = new_intent
```

Even if the internal logic is already v3, the API/naming still tells future developers that v2 is the active system.

## Required change

Rename the runtime functions:

```text
_run_bot_decision_v2
→ _run_npc_brain_v3_decision

_run_bot_decision_v2_inner
→ _run_npc_brain_v3_decision_inner
```

If full rename is risky, add new v3-named functions and make old names short temporary wrappers:

```python
def _run_bot_decision_v2(...):
    # TODO remove after PR5
    return _run_npc_brain_v3_decision(...)
```

But for final PR 5 closure, preferred:

```text
remove old wrappers
update all call sites/tests
```

## Required context rename

Rename:

```text
agent["_v2_context"]
```

to:

```text
agent["brain_v3_context"]
```

or:

```text
agent["npc_brain_v3_context"]
```

Recommended:

```text
brain_v3_context
```

Update:

```text
backend runtime
frontend AgentProfileModal
exportNpcHistory.ts
tests
fixtures
compact export
```

## Required compatibility behavior

For old saves, add one-time migration:

```python
if "_v2_context" in agent and "brain_v3_context" not in agent:
    agent["brain_v3_context"] = agent.pop("_v2_context")
```

But after migration, new ticks should not write `_v2_context`.

## Required tests

```python
def test_tick_writes_brain_v3_context_not_v2_context():
    ...
```

Expected:

```text
agent["brain_v3_context"] exists
"_v2_context" not in agent after tick
```

## Priority

```text
BLOCKER
```

---

# 3. Required fix B — brain_trace must be objective/active-plan first

## Problem

`brain_trace` still emits:

```text
decision = "new_intent"
summary = "Выбрана цель ..., Адаптер intent ..."
```

The summary has improved, but the event kind is still intent-era.

## Required change

For v3 decision events:

```text
mode = "decision"
decision = "objective_decision"
```

For ActivePlan events:

```text
mode = "active_plan"
decision = active_plan_created / active_plan_step_started / ...
```

For plan monitor compatibility events:

```text
mode = "active_plan_monitor" or "runtime_monitor"
```

Do not use:

```text
decision = new_intent
```

except in a legacy fallback path that is clearly marked:

```text
mode = "legacy_decision"
decision = "legacy_new_intent"
```

## Required trace payload

Decision event should include:

```json
{
  "mode": "decision",
  "decision": "objective_decision",
  "active_objective": {...},
  "objective_scores": [...],
  "alternatives": [...],
  "adapter_intent": {
    "kind": "get_rich",
    "score": 0.42
  },
  "active_plan_runtime": {
    "active_plan_id": "...",
    "objective_key": "FIND_ARTIFACTS",
    "status": "active",
    "current_step_index": 0,
    "current_step_kind": "travel_to_location"
  }
}
```

`intent_kind` / `intent_score` may remain for compatibility, but must be secondary.

Recommended:

```text
keep intent_kind for now
add adapter_intent object
UI uses adapter_intent
```

## Required tests

Update old test:

```python
test_bot_decision_pipeline_writes_decision_brain_trace_event
```

Expected:

```text
decision event decision == "objective_decision"
active_objective exists
adapter_intent exists
```

No PR5 test should assert:

```text
decision == "new_intent"
```

## Priority

```text
BLOCKER
```

---

# 4. Required fix C — create ActivePlan for every actionable ObjectiveDecision

## Problem

PR5 partially creates ActivePlan, but final migration requires:

```text
Every valid actionable ObjectiveDecision creates or updates ActivePlanV3.
```

There should be no v3 bot path:

```text
ObjectiveDecision → scheduled_action directly
```

except explicit one-off actions that are wrapped as single-step ActivePlan.

## Required change

After objective selection and plan feasibility validation:

```python
active_plan = create_active_plan(objective_decision, world_turn, selected_plan)
save_active_plan(agent, active_plan)
```

Then start first step through ActivePlan:

```python
_start_or_continue_active_plan_step(...)
```

## One-step actions

Even simple actions should become one-step ActivePlans:

```text
RESTORE_WATER → consume_item
RESTORE_FOOD → consume_item
HEAL_SELF → consume_item
WAIT_IN_SHELTER → wait
REST → sleep_for_hours
```

This makes lifecycle/debug consistent.

## Required tests

```python
def test_every_actionable_objective_creates_active_plan():
    ...
```

Cases:

```text
RESTORE_WATER
GET_MONEY_FOR_RESUPPLY
FIND_ARTIFACTS
SELL_ARTIFACTS
RESUPPLY_FOOD
REST
WAIT_IN_SHELTER
```

Expected:

```text
active_plan_v3 exists
active_plan_v3.objective_key == selected objective
scheduled_action.active_plan_id == active_plan_v3.id
```

## Priority

```text
BLOCKER
```

---

# 5. Required fix D — scheduled_action must be a child of ActivePlan

## Problem

`schedule_action` still has old independent behavior.

After PR5 final migration:

```text
scheduled_action cannot be treated as a plan.
```

It is only:

```text
current runtime execution of active_plan_v3.current_step
```

## Required change

Every `scheduled_action` created by v3 bot must include:

```json
{
  "active_plan_id": "...",
  "active_plan_step_index": 0,
  "active_plan_objective_key": "FIND_ARTIFACTS"
}
```

If a v3 bot has scheduled_action without these fields:

```text
treat it as legacy/imported action
wrap it into ActivePlan or clear/rebuild it
```

## Migration

At tick start:

```python
def _migrate_legacy_scheduled_action_to_active_plan(agent, world_turn):
    if agent has scheduled_action and no active_plan_v3:
        create ActivePlanV3 with objective_key="LEGACY_RUNTIME_ACTION"
        one step matching scheduled_action
        tag scheduled_action
```

Alternative:

```text
clear legacy scheduled_action and re-run Brain v3 decision
```

Recommended for deterministic migration:

```text
wrap legacy scheduled_action into temporary ActivePlan
```

## Required tests

```python
def test_v3_scheduled_action_is_tagged_with_active_plan():
    ...
```

```python
def test_legacy_scheduled_action_is_wrapped_or_rebuilt():
    ...
```

## Priority

```text
BLOCKER
```

---

# 6. Required fix E — remove action_queue as active source for v3 bots

## Problem

`action_queue` is a v1/v2-era plan continuation mechanism.

With ActivePlan:

```text
action_queue duplicates ActivePlan.steps
```

## Required final rule

For v3 bot NPCs:

```text
action_queue must be empty during normal operation.
```

Allowed exceptions:

```text
legacy save migration
debug fallback
non-v3/human agents
```

## Required change

When ActivePlan is created:

```python
agent["action_queue"] = []
```

When ActivePlan step starts:

```python
do not push future steps into action_queue
```

When ActivePlan completes/aborts:

```python
agent["action_queue"] = []
```

When old code tries to use action_queue for v3 bot:

```text
redirect through ActivePlan or ignore
```

## Required tests

```python
def test_v3_active_plan_owns_steps_and_action_queue_stays_empty():
    ...
```

Expected:

```text
active_plan_v3.steps has long plan
action_queue == []
scheduled_action is current step only
```

## Priority

```text
BLOCKER
```

---

# 7. Required fix F — ActivePlan step lifecycle must be complete

## Current partial behavior

Current PR5 already starts steps, tags scheduled actions and advances step on scheduled action completion.

Need to finish all lifecycle edges.

## Required behavior

### Pending → Running

When current step starts:

```text
step.status = running
step.started_turn = world_turn
active_plan.updated_turn = world_turn
trace active_plan_step_started
memory active_plan_step_started
```

### Running → Completed

When scheduled action completes:

```text
step.status = completed
step.completed_turn = world_turn
active_plan.current_step_index += 1
trace active_plan_step_completed
memory active_plan_step_completed
```

### Running → Failed

When scheduled action aborts or executor fails:

```text
step.status = failed
step.failure_reason = reason
active_plan.status = repairing or aborted
trace active_plan_step_failed
memory active_plan_step_failed
```

### Completed plan

When all steps completed:

```text
active_plan.status = completed
trace active_plan_completed
memory active_plan_completed
clear active_plan_v3 or keep terminal based on chosen policy
```

Recommended policy:

```text
clear active_plan_v3 after writing completed memory/trace
```

### Aborted plan

When unrecoverable:

```text
active_plan.status = aborted
abort_reason set
trace active_plan_aborted
memory active_plan_aborted
clear active_plan_v3
```

## Required tests

```python
def test_active_plan_full_completion_writes_trace_memory_and_clears_plan():
    ...
```

```python
def test_active_plan_step_failure_enters_repair_or_abort():
    ...
```

## Priority

```text
BLOCKER
```

---

# 8. Required fix G — ActivePlan monitor replaces old PlanMonitor as primary

## Problem

Old `PlanMonitor` still handles scheduled_action validity and writes `plan_monitor_*` events.

PR5 should not leave old PlanMonitor as the primary authority.

## Required final behavior

For v3 bots with ActivePlan:

```text
ActivePlan monitor decides:
- continue
- repair
- abort
- complete
```

Old `assess_scheduled_action_v3` can remain as a low-level runtime action validator, but its result must feed ActivePlan:

```text
scheduled_action invalid
→ active_plan_step_failed / active_plan_repair_requested
```

not:

```text
clear scheduled_action/action_queue and run standalone redecision
```

## Required change

When scheduled action monitor returns abort for v3 active plan:

```text
do not just clear scheduled_action/action_queue
instead:
  mark current ActivePlanStep failed
  call repair_active_plan(...)
  keep objective context
```

Only if repair fails:

```text
active_plan_aborted
then Brain v3 re-evaluates
```

## Required tests

```python
def test_plan_monitor_abort_becomes_active_plan_repair_for_v3_bot():
    ...
```

Expected:

```text
active_plan_v3 still exists or is cleanly aborted with active_plan_aborted
not silent legacy plan_monitor_abort only
```

## Priority

```text
HIGH
```

---

# 9. Required fix H — repair behavior must not endlessly retry the same broken step

## Problem

Some repair reasons currently reset current step to `pending`.

That can create:

```text
broken step
→ repair
→ same step pending
→ broken again
→ repair
→ max repairs
```

This is acceptable only as last-resort fallback, not primary behavior.

## Required behavior by repair reason

### emission_interrupt

Already partially implemented.

Required final behavior:

```text
insert shelter substeps or pause plan
after emission ended, resume original objective
```

Add full tick-level resume test.

### target_location_empty

Required final behavior:

```text
mark current target invalid
either:
  - replace current target with alternative location;
  - or abort current plan and request new ObjectiveDecision;
but do not retry same empty location.
```

### trader_unavailable

Required final behavior:

```text
try alternative trader from world/memory;
if none, abort/re-evaluate;
do not retry same missing trader.
```

### supplies_consumed_mid_plan

Required final behavior:

```text
insert RESTORE/RESUPPLY substeps if possible;
if impossible, abort/re-evaluate.
```

## Required tests

```python
def test_target_location_empty_does_not_retry_same_location_forever():
    ...
```

```python
def test_trader_unavailable_chooses_alternative_or_aborts():
    ...
```

```python
def test_supplies_consumed_mid_plan_inserts_resupply_or_aborts():
    ...
```

## Priority

```text
HIGH
```

---

# 10. Required fix I — memory_v3 should be used for repair checks

## Problem

Repair checks currently inspect mostly legacy memory.

But final Brain v3 should use:

```text
memory_v3
```

for structured evidence.

## Required change

For repair checks:

### confirmed empty

Check both:

```text
legacy memory
memory_v3 records
```

Memory v3 kinds:

```text
location_empty
target_not_found
confirmed_empty
```

### trader unavailable

Check:

```text
trader_location_known stale/contradicted
trader_not_found
trader_dead
```

### target/source stale

Check:

```text
last_accessed_turn
confidence
status active/stale/archived
```

## Required helper

```python
def _memory_v3_has_confirmed_empty(agent, location_id):
    ...
```

Should check records by location if indexes exist, fallback to scan records.

## Required tests

```python
def test_active_plan_repair_detects_confirmed_empty_from_memory_v3():
    ...
```

## Priority

```text
HIGH
```

---

# 11. Required fix J — active_plan lifecycle must be visible in memory_v3

## Problem

ActivePlan lifecycle events are written through `_add_memory`, but legacy bridge may not map them cleanly into `memory_v3`.

## Required change

Update legacy bridge mapping for:

```text
active_plan_created
active_plan_step_started
active_plan_step_completed
active_plan_step_failed
active_plan_repair_requested
active_plan_repaired
active_plan_paused
active_plan_resumed
active_plan_aborted
active_plan_completed
```

Recommended layers:

```text
active_plan_created        → goal
active_plan_step_started   → episodic
active_plan_step_completed → episodic
active_plan_step_failed    → goal/threat
active_plan_repair_*       → goal
active_plan_aborted        → goal/threat
active_plan_completed      → goal
```

Tags:

```text
active_plan
objective:<key>
step:<kind>
repair:<reason>
```

Entity/location/item extraction should still work.

## Required tests

```python
def test_active_plan_memory_events_bridge_to_memory_v3():
    ...
```

Expected:

```text
memory_v3 has kind=active_plan_created
memory_v3 has kind=active_plan_completed
```

## Priority

```text
MEDIUM/HIGH
```

---

# 12. Required fix K — final debug/export naming cleanup

## Problem

Frontend/export may still expose v2 names:

```text
_v2_context
intent as primary
new_intent
legacy decision labels
```

## Required change

Update frontend to use:

```text
brain_v3_context
adapter_intent
current_objective
active_plan_v3
```

Raw debug may still show old fields only if old save data contains them.

## Compact export

`npc_history_v1` should probably remain as schema name for export compatibility, but payload should include:

```text
brain_v3_context
current_objective
current_runtime
active_plan_v3
```

Not:

```text
_v2_context
```

## Required tests/manual checks

Manual:

```text
Open NPC profile:
- Objective is primary.
- ActivePlan is primary runtime block.
- scheduled_action appears only under current runtime step.
- intent appears as adapter.
- no visible "_v2_context" outside Raw Debug.
```

## Priority

```text
MEDIUM
```

---

# 13. Required fix L — documentation update inside canonical docs only

## Problem

Do not reintroduce root-level temporary files.

Current PR5 added:

```text
docs/npc_brain_v3_pr5_remaining_fixes.md
```

This violates the documentation reorganization.

## Required change

Merge relevant PR5 final behavior into:

```text
docs/npc_brain_v3/05_pr5_active_plan.md
```

Then delete:

```text
docs/npc_brain_v3_pr5_remaining_fixes.md
```

Update `05_pr5_active_plan.md` with:

```text
1. Final runtime order.
2. ActivePlan as source of truth.
3. scheduled_action as runtime step.
4. action_queue as legacy fallback only.
5. Brain v3 context naming.
6. ActivePlan lifecycle events.
7. Repair behavior.
8. memory_v3 repair checks.
9. frontend/debug/export behavior.
10. final acceptance tests.
```

## Priority

```text
HIGH
```

---

# 14. Required fix M — remove duplicate/old constants and dead code from tick_rules

## Problem

Current `tick_rules.py` now contains a lot of accumulated migration code.

There are visible duplication risks:

```text
_OBJECTIVE_MEMORY_USED_FOR appears near ActivePlan helpers and later again.
old v2 comments remain.
legacy intent labels remain public.
```

## Required cleanup

1. Remove duplicate constants.
2. Move ActivePlan helpers out of `tick_rules.py` into a dedicated module:

```text
backend/app/games/zone_stalkers/decision/active_plan_runtime.py
```

Recommended moved functions:

```text
_active_plan_step_label
_active_plan_trace_payload
_write_active_plan_memory_event
_write_active_plan_trace_event
_tag_scheduled_action_with_active_plan
_scheduled_action_matches_active_step
_finish_active_plan
_start_or_continue_active_plan_step
_process_active_plan_v3
_on_active_plan_scheduled_action_completed
```

3. Keep `tick_rules.py` as orchestration only.

## Why

PR5 should reduce complexity, not make `tick_rules.py` a huge mixed v1/v2/v3 file.

## Priority

```text
MEDIUM/HIGH
```

Not strictly required for behavior, but strongly recommended to make the final v3 transition maintainable.

---

# 15. Required fix N — final test suite for v3-only behavior

Add a dedicated test file:

```text
backend/tests/decision/v3/test_brain_v3_final_transition.py
```

Must cover:

```text
1. No new _v2_context after tick.
2. No decision=new_intent for v3 objective decisions.
3. ObjectiveDecision creates ActivePlan.
4. scheduled_action is tagged with active_plan_id.
5. action_queue stays empty for v3 bot with active plan.
6. ActivePlan step completion advances plan.
7. ActivePlan completion writes memory/trace and clears plan.
8. ActivePlan abort writes memory/trace and clears plan.
9. ActivePlan repair does not silently lose objective context.
10. memory_v3 receives ActivePlan lifecycle entries.
11. profile/export-compatible fields use brain_v3_context/current_objective.
```

Update old tests that still assert v2 markers.

## Priority

```text
BLOCKER
```

---

# 16. Final acceptance checklist for closing PR 5

PR5 can be closed only when:

```text
[ ] Bot NPC runtime no longer writes _v2_context.
[ ] Bot NPC runtime writes brain_v3_context.
[ ] Brain trace decision event is objective_decision, not new_intent.
[ ] Intent is displayed/stored only as adapter_intent.
[ ] Every actionable objective creates ActivePlanV3.
[ ] ActivePlanV3 owns long-running behavior.
[ ] scheduled_action is tagged child runtime step of ActivePlan.
[ ] action_queue is not used as plan source for v3 bots.
[ ] ActivePlan step lifecycle is complete: pending/running/completed/failed.
[ ] ActivePlan completion writes trace/memory and clears/terminates plan.
[ ] ActivePlan abort writes trace/memory and clears/terminates plan.
[ ] ActivePlan repair preserves objective context.
[ ] Emission repair shelters NPC and then resumes or re-evaluates original objective.
[ ] target_location_empty does not retry same broken target forever.
[ ] trader_unavailable does not retry same missing trader forever.
[ ] supplies_consumed_mid_plan inserts subplan or re-evaluates.
[ ] Repair checks use memory_v3 where relevant.
[ ] ActivePlan lifecycle memory bridges into memory_v3.
[ ] Frontend profile is v3-first.
[ ] Compact export is v3-first.
[ ] Root-level temporary docs are removed.
[ ] Canonical docs/npc_brain_v3/05_pr5_active_plan.md is updated.
[ ] Existing PR1–PR4 tests pass.
[ ] New final v3 transition tests pass.
```

---

# 17. Recommended implementation order

1. Rename `_v2_context` → `brain_v3_context`.
2. Rename `_run_bot_decision_v2*` → `_run_npc_brain_v3_decision*`.
3. Change brain_trace decision from `new_intent` to `objective_decision`.
4. Add `adapter_intent` payload object.
5. Enforce ActivePlan creation for every actionable objective.
6. Enforce scheduled_action child tags for every v3 runtime action.
7. Remove action_queue as source for v3 bots.
8. Complete step lifecycle: failed/aborted/completed cases.
9. Route old PlanMonitor aborts into ActivePlan repair.
10. Improve repair behavior for non-emission reasons.
11. Add memory_v3 repair checks.
12. Add legacy bridge mappings for active_plan lifecycle.
13. Move ActivePlan runtime helpers out of `tick_rules.py`.
14. Update frontend/export naming to v3-first.
15. Update canonical docs only.
16. Add final transition tests.
17. Run full backend/frontend test/build.

---

# 18. What can remain after PR 5

Some internal compatibility may remain, but it must be clearly secondary.

Allowed:

```text
agent["memory"] as human-readable timeline
intent adapter as internal execution bridge
legacy scheduled_action migration for old saves
raw debug showing legacy fields if old data exists
```

Not allowed:

```text
_v2_context as active runtime field
new_intent as v3 decision event
action_queue as active plan source for v3 bots
v2_decision as new decision memory kind
intent as public reason for bot decisions
legacy PlanMonitor clearing v3 plan without ActivePlan repair/abort
```

---

# 19. Final expected runtime after PR 5

## New decision

```text
Brain v3 selects objective:
  FIND_ARTIFACTS

Creates ActivePlan:
  id=plan_123
  objective_key=FIND_ARTIFACTS
  steps:
    0 travel_to_location
    1 explore_location
    2 collect_artifact
    3 travel_to_trader
    4 sell_artifacts

Starts current runtime action:
  scheduled_action.type=travel
  scheduled_action.active_plan_id=plan_123
  scheduled_action.active_plan_step_index=0
```

## During execution

```text
No new objective_decision every tick.

Trace:
  active_plan_step_started
  active_plan_continue
  active_plan_step_completed
```

## Interruption

```text
Emission warning:
  ActivePlan repair requested
  shelter substeps inserted
  objective context preserved
```

## Completion

```text
Last step completed:
  active_plan_completed trace
  active_plan_completed memory
  memory_v3 bridged record
  active_plan_v3 cleared/terminal
```

This is the finished migration to Brain v3.
