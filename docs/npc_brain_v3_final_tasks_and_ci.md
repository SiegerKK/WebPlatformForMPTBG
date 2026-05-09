# NPC Brain v3 — Final Full-Solution Tasks and GitHub Actions Test Setup

> Branch context: `copilot/implement-pr-5-for-npc-brain-v3`
>
> Goal:
>
> ```text
> Finish the full Brain v3 solution and prove it with end-to-end tests:
>
> 1. get_rich:
>    spawn → find/get money → sell → global goal completed → LEAVE_ZONE → has_left_zone
>
> 2. kill_stalker:
>    spawn → locate/track target → prepare if needed → engage → confirm kill
>    → global goal completed → LEAVE_ZONE → has_left_zone
>
> 3. Run the full backend/frontend checks in GitHub Actions.
> ```

---

# 1. Current status

The project is now close to the final v3 goal behavior.

Already implemented or mostly implemented:

```text
- ActivePlanV3 runtime.
- ActivePlan as source of truth.
- scheduled_action as ActivePlan child step.
- action_queue no longer primary for v3 bots.
- brain_v3_context.
- objective_decision trace.
- adapter_intent.
- memory_v3 bridge.
- strategic ActivePlan composition for FIND_ARTIFACTS / GET_MONEY_FOR_RESUPPLY.
- off-by-one fix for active_plan_step_completed logs.
- TargetBelief model.
- build_target_belief().
- hunt objective stages.
- hunt step kinds:
  ask_for_intel,
  search_target,
  start_combat,
  confirm_kill.
- hunt executor side-effects.
- LEAVE_ZONE plan/executor unit tests.
- first E2E tests.
```

But the current E2E tests still use shortcuts:

```text
get_rich E2E starts with artifact already in inventory.
kill_stalker E2E starts with already dead target and pre-written target_death_confirmed memory.
```

These prove the tail of the chain, not the full chain.

---

# 2. Remaining task A — real get_rich E2E without preloaded artifact

## Problem

Current `test_e2e_get_rich_from_spawn_to_leave_zone` starts with:

```python
hunter["inventory"].append({"id": "artifact_1", "type": "soul", "value": 2000})
```

This skips the actual artifact search loop.

## Required new test

Add a real full-path test:

```python
def test_e2e_get_rich_finds_artifact_sells_and_leaves_zone():
    ...
```

## Scenario

Create deterministic small world:

```text
loc_spawn
  safe start

loc_anomaly
  anomaly_activity high
  deterministic artifact spawn or guaranteed artifact discovery

loc_trader
  trader buys artifacts

loc_exit
  exit_zone = true
```

Agent:

```text
global_goal = get_rich
money = 0
wealth_goal_target = low, e.g. 1000
material_threshold = 0
has weapon/food/water enough
inventory has no starting artifact
```

## Expected chain

The test should verify the full loop:

```text
spawn
→ objective_decision FIND_ARTIFACTS or GET_MONEY_FOR_RESUPPLY
→ ActivePlan travel_to_location + explore_location
→ artifact appears or gets picked up
→ objective_decision SELL_ARTIFACTS
→ ActivePlan travel_to_location + trade_sell_item
→ liquid_wealth / money reaches wealth_goal_target
→ global_goal_completed
→ objective_decision LEAVE_ZONE
→ ActivePlan travel_to_exit + leave_zone
→ has_left_zone = true
```

## Required assertions

```python
assert hunter["global_goal_achieved"] is True
assert hunter["has_left_zone"] is True
assert any_memory(hunter, "global_goal_completed")
assert any_memory(hunter, "left_zone")
assert any_objective_decision(hunter, "LEAVE_ZONE")
assert any_objective_decision(hunter, "FIND_ARTIFACTS") or any_objective_decision(hunter, "GET_MONEY_FOR_RESUPPLY")
assert any_objective_decision(hunter, "SELL_ARTIFACTS")
```

Also assert ActivePlan lifecycle:

```python
assert any_active_plan_event(hunter, "active_plan_created")
assert any_active_plan_step(hunter, "travel_to_location")
assert any_active_plan_step(hunter, "explore_location")
assert any_active_plan_step(hunter, "trade_sell_item")
```

Do not make the test depend on exact turn numbers.

---

# 3. Remaining task B — real kill_stalker E2E with live target

## Problem

Current `test_e2e_kill_stalker_known_target_to_leave_zone` starts with:

```python
target["is_alive"] = False
```

and pre-writes:

```text
target_death_confirmed
```

This skips:

```text
track
engage
combat
confirm kill
```

## Required new test

Add:

```python
def test_e2e_kill_stalker_live_target_to_leave_zone():
    ...
```

## Scenario

World:

```text
loc_spawn
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
has medicine
```

Target:

```text
is_alive = true
location_id = loc_target
hp low enough or combat deterministic enough that hunter wins
```

Initial knowledge:

Option A — known target:

```text
memory_v3 target_last_known_location = loc_target
```

Option B — unknown target with intel:

```text
no target memory
trader at loc_trader can provide target_intel
```

For the first full E2E, use Option A. Add unknown/intel as a separate test later.

## Expected chain

```text
spawn
→ TRACK_TARGET
→ ActivePlan travel_to_location + search_target
→ target_seen / target_last_known_location
→ ENGAGE_TARGET
→ start_combat
→ combat resolves target dead
→ CONFIRM_KILL
→ target_death_confirmed
→ global_goal_completed
→ LEAVE_ZONE
→ has_left_zone
```

## Required assertions

```python
assert target["is_alive"] is False
assert hunter["global_goal_achieved"] is True
assert hunter["has_left_zone"] is True

assert any_memory(hunter, "target_seen")
assert any_memory(hunter, "target_last_known_location")
assert any_memory(hunter, "target_death_confirmed")
assert any_memory(hunter, "global_goal_completed")
assert any_memory(hunter, "left_zone")

assert any_objective_decision(hunter, "TRACK_TARGET")
assert any_objective_decision(hunter, "ENGAGE_TARGET")
assert any_objective_decision(hunter, "CONFIRM_KILL")
assert any_objective_decision(hunter, "LEAVE_ZONE")
```

## Required ActivePlan assertions

```python
assert any_active_plan_step(hunter, "search_target")
assert any_active_plan_step(hunter, "start_combat")
assert any_active_plan_step(hunter, "confirm_kill")
assert any_active_plan_step(hunter, "leave_zone")
```

---

# 4. Remaining task C — combat monitor / no premature confirm

## Problem

Current hunt executor has:

```text
STEP_START_COMBAT
STEP_CONFIRM_KILL
```

But a full system should not confirm kill before combat resolves.

If `STEP_START_COMBAT` is one-tick and the target is still alive, the plan may advance too early.

## Required behavior

Add one of these:

### Option A — explicit monitor step

Add:

```text
STEP_MONITOR_COMBAT = "monitor_combat"
```

Plan:

```text
ENGAGE_TARGET:
  1. start_combat
  2. monitor_combat
  3. confirm_kill
```

`monitor_combat` behavior:

```text
if combat still active:
  keep step running / scheduled
if target dead:
  complete step, continue to confirm_kill
if hunter wounded/fled:
  fail/repair to RETREAT_FROM_TARGET or RECOVER_AFTER_COMBAT
if target fled:
  fail/repair to TRACK_TARGET
```

### Option B — scheduled combat action

Make `STEP_START_COMBAT` create:

```text
scheduled_action.type = "combat"
```

and only complete when combat resolves.

## Required tests

```python
def test_engage_target_does_not_confirm_before_combat_resolves():
    ...
```

Expected:

```text
target alive
combat_interaction active
CONFIRM_KILL not executed yet
no target_death_confirmed memory yet
```

```python
def test_engage_target_confirms_after_combat_target_dead():
    ...
```

Expected:

```text
combat resolved
target dead
confirm_kill executes
target_death_confirmed memory exists
```

Priority:

```text
BLOCKER for true kill_stalker E2E.
```

---

# 5. Remaining task D — prepare-before-hunt E2E

## Problem

There are unit/stage tests for combat readiness, but we need a full scenario proving that the NPC prepares before attacking.

## Required test

```python
def test_e2e_kill_stalker_prepares_before_engage_when_no_ammo():
    ...
```

## Scenario

Hunter:

```text
global_goal = kill_stalker
kill_target_id = target
has weapon
has no compatible ammo
money enough to buy ammo
```

World:

```text
trader sells compatible ammo
target location known
```

Expected chain:

```text
PREPARE_FOR_HUNT
→ RESUPPLY_AMMO / trade_buy_item compatible ammo
→ TRACK_TARGET
→ ENGAGE_TARGET
→ CONFIRM_KILL
→ LEAVE_ZONE
```

## Assertions

```python
assert any_objective_decision(hunter, "PREPARE_FOR_HUNT")
assert any_objective_decision(hunter, "RESUPPLY_AMMO") or any_memory(hunter, "trade_buy")
assert objective_order(hunter, "PREPARE_FOR_HUNT", "ENGAGE_TARGET")
```

Implementation can be simpler:

```python
engage_turn = first_objective_turn(hunter, "ENGAGE_TARGET")
ammo_buy_turn = first_memory_turn(hunter, "trade_buy", item_type="ammo_9mm")
assert ammo_buy_turn < engage_turn
```

---

# 6. Remaining task E — target moved / target_not_found repair E2E

## Problem

The hunt system should not blindly retry stale target locations.

## Required test

```python
def test_e2e_kill_stalker_target_moved_repairs_tracking_plan():
    ...
```

## Scenario

Initial memory:

```text
target_last_known_location = loc_old
```

Actual target:

```text
target.location_id = loc_new
```

Hunter:

```text
starts at loc_spawn
goes to loc_old
search_target fails
writes target_not_found
```

Then either:

```text
new target intel points to loc_new
```

or target becomes visible/known through a deterministic event.

Expected chain:

```text
TRACK_TARGET loc_old
→ search_target
→ target_not_found
→ repair/re-evaluate
→ LOCATE_TARGET or TRACK_TARGET loc_new
→ ENGAGE_TARGET
→ CONFIRM_KILL
→ LEAVE_ZONE
```

## Assertions

```python
assert any_memory(hunter, "target_not_found")
assert any_active_plan_event(hunter, "active_plan_repair_requested") or any_objective_decision(hunter, "LOCATE_TARGET")
assert eventually hunter["has_left_zone"] is True
```

---

# 7. Remaining task F — unknown target → intel → hunt E2E

## Problem

Current known-target tests skip `LOCATE_TARGET`.

## Required test

```python
def test_e2e_kill_stalker_unknown_target_uses_intel_then_hunts():
    ...
```

## Scenario

Hunter:

```text
kill_target_id = target
no target_last_known_location memory
```

World:

```text
trader or co-located stalker can provide target_intel
target at loc_target
```

Expected chain:

```text
LOCATE_TARGET
→ ask_for_intel
→ target_intel / target_last_known_location
→ TRACK_TARGET
→ ENGAGE_TARGET
→ CONFIRM_KILL
→ LEAVE_ZONE
```

## Assertions

```python
assert any_objective_decision(hunter, "LOCATE_TARGET")
assert any_memory(hunter, "target_intel") or any_memory(hunter, "target_last_known_location")
assert any_objective_decision(hunter, "TRACK_TARGET")
assert any_objective_decision(hunter, "ENGAGE_TARGET")
assert any_objective_decision(hunter, "CONFIRM_KILL")
assert hunter["has_left_zone"] is True
```

---

# 8. Remaining task G — memory_v3 bridge tests for hunt events

## Problem

`legacy_bridge.py` now maps hunt events, but add direct tests for indexes/evidence.

## Required tests

```python
def test_target_seen_memory_bridges_to_memory_v3_with_entity_and_location():
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

Expected:

```text
memory_v3.records contains kind target_seen / target_not_found / target_death_confirmed
record.entity_ids contains target id
record.location_id is filled when applicable
record.tags contains target/tracking/death/etc.
```

---

# 9. Remaining task H — docs cleanup

## Problem

Temporary docs appeared again.

Currently seen in diff:

```text
docs/npc_brain_v3/npc_brain_v3_final_e2e_goal_fixes.md
docs/npc_brain_v3_hunt_goal_implementation_plan.md
docs/npc_brain_v3_post_pr5_log_fixes.md
```

This violates the docs organization rule.

## Required cleanup

Merge relevant content into canonical docs:

```text
docs/npc_brain_v3/05_pr5_active_plan.md
docs/npc_brain_v3/06_final_decision_chain_examples.md
docs/npc_brain_v3/07_post_pr5_kill_stalker_goal.md
```

Then delete temporary files:

```text
docs/npc_brain_v3/npc_brain_v3_final_e2e_goal_fixes.md
docs/npc_brain_v3_hunt_goal_implementation_plan.md
docs/npc_brain_v3_post_pr5_log_fixes.md
```

Final docs should still follow the 8-doc structure:

```text
docs/npc_brain_v3/00_overview.md
docs/npc_brain_v3/01_pr1_foundation_sleep_survival.md
docs/npc_brain_v3/02_pr2_needs_liquidity_resupply.md
docs/npc_brain_v3/03_pr3_memory_beliefs.md
docs/npc_brain_v3/04_pr4_objectives_debug_ui.md
docs/npc_brain_v3/05_pr5_active_plan.md
docs/npc_brain_v3/06_final_decision_chain_examples.md
docs/npc_brain_v3/07_post_pr5_kill_stalker_goal.md
```

---

# 10. GitHub Actions setup

## Goal

Add CI so GitHub Actions runs:

```text
backend unit/integration tests
backend v3 E2E tests
frontend TypeScript/Vite build
```

This is important because the final v3 scenario tests are large and can regress easily.

## Existing project commands

Backend dependencies:

```text
backend/requirements.txt
```

Frontend commands:

```json
{
  "build": "tsc && vite build"
}
```

## Required workflow file

Create:

```text
.github/workflows/ci.yml
```

## Recommended workflow

```yaml
name: CI

on:
  pull_request:
    branches:
      - "**"
  push:
    branches:
      - main
      - master
      - copilot/**
  workflow_dispatch:

jobs:
  backend-tests:
    name: Backend tests
    runs-on: ubuntu-latest

    defaults:
      run:
        working-directory: .

    steps:
      - name: Checkout
        uses: actions/checkout@v4

      - name: Set up Python
        uses: actions/setup-python@v5
        with:
          python-version: "3.11"
          cache: "pip"
          cache-dependency-path: backend/requirements.txt

      - name: Install backend dependencies
        run: |
          python -m pip install --upgrade pip
          pip install -r backend/requirements.txt

      - name: Run backend tests
        env:
          PYTHONPATH: backend
        run: |
          pytest backend/tests

  backend-v3-e2e:
    name: Backend NPC Brain v3 E2E
    runs-on: ubuntu-latest

    defaults:
      run:
        working-directory: .

    steps:
      - name: Checkout
        uses: actions/checkout@v4

      - name: Set up Python
        uses: actions/setup-python@v5
        with:
          python-version: "3.11"
          cache: "pip"
          cache-dependency-path: backend/requirements.txt

      - name: Install backend dependencies
        run: |
          python -m pip install --upgrade pip
          pip install -r backend/requirements.txt

      - name: Run NPC Brain v3 E2E tests
        env:
          PYTHONPATH: backend
        run: |
          pytest backend/tests/decision/v3/test_e2e_brain_v3_goals.py -q

  frontend-build:
    name: Frontend build
    runs-on: ubuntu-latest

    defaults:
      run:
        working-directory: frontend

    steps:
      - name: Checkout
        uses: actions/checkout@v4

      - name: Set up Node
        uses: actions/setup-node@v4
        with:
          node-version: "20"
          cache: "npm"
          cache-dependency-path: frontend/package-lock.json

      - name: Install frontend dependencies
        run: npm ci

      - name: Build frontend
        run: npm run build
```

## If `frontend/package-lock.json` is missing

If the repo does not have `frontend/package-lock.json`, either:

```text
1. commit package-lock.json and use npm ci;
```

or change workflow to:

```yaml
      - name: Install frontend dependencies
        run: npm install
```

and remove/adjust cache dependency path.

Preferred:

```text
commit package-lock.json
use npm ci
```

## Optional: split fast and slow tests

If full backend suite becomes too slow, keep:

```text
backend-tests
```

for normal tests and:

```text
backend-v3-e2e
```

for explicit E2E tests.

Later we can add marker:

```python
@pytest.mark.e2e
```

and run:

```bash
pytest backend/tests -m "not e2e"
pytest backend/tests -m e2e
```

But do not require this now unless tests are slow.

---

# 11. GitHub Actions acceptance criteria

CI setup is complete when:

```text
[ ] .github/workflows/ci.yml exists.
[ ] Pull requests trigger backend tests.
[ ] Pull requests trigger backend v3 E2E tests.
[ ] Pull requests trigger frontend build.
[ ] Backend workflow installs backend/requirements.txt.
[ ] Frontend workflow runs npm ci or npm install consistently.
[ ] PYTHONPATH is set so tests can import app.* from backend.
[ ] CI fails if any E2E goal test fails.
```

---

# 12. Final definition of done for full solution

The full solution is complete when all of the following are true.

## get_rich

```text
[ ] E2E starts without preloaded artifact.
[ ] NPC chooses FIND_ARTIFACTS or GET_MONEY_FOR_RESUPPLY.
[ ] NPC travels to anomaly.
[ ] NPC explores.
[ ] NPC obtains artifact or money through world mechanics.
[ ] NPC sells artifact.
[ ] NPC reaches wealth_goal_target.
[ ] global_goal_completed memory is written.
[ ] LEAVE_ZONE objective is selected.
[ ] has_left_zone becomes true.
```

## kill_stalker

```text
[ ] E2E starts with live target.
[ ] NPC can locate or track target.
[ ] NPC can prepare if not combat-ready.
[ ] NPC engages through combat system.
[ ] NPC does not confirm kill before combat resolves.
[ ] target_death_confirmed memory is written only after target death.
[ ] kill_stalker sets global_goal_achieved.
[ ] global_goal_completed memory is written.
[ ] LEAVE_ZONE objective is selected.
[ ] has_left_zone becomes true.
```

## v3 invariants

```text
[ ] ObjectiveDecision is the reason.
[ ] ActivePlanV3 is the runtime source of truth.
[ ] scheduled_action is only current ActivePlan child step.
[ ] action_queue is empty for v3 bots.
[ ] memory_v3 contains relevant evidence.
[ ] brain_trace is readable.
[ ] compact export shows current objective/runtime.
```

## CI

```text
[ ] GitHub Actions passes backend tests.
[ ] GitHub Actions passes NPC Brain v3 E2E tests.
[ ] GitHub Actions passes frontend build.
```

---

# 13. Recommended implementation order

1. Add/finish combat monitor or scheduled combat runtime.
2. Add real get_rich E2E without preloaded artifact.
3. Add real kill_stalker E2E with live target.
4. Add prepare-before-hunt E2E.
5. Add target moved/not found repair E2E.
6. Add unknown target → intel E2E.
7. Add memory_v3 bridge/index tests for hunt events.
8. Clean temporary docs.
9. Add GitHub Actions CI.
10. Run CI and fix failures.
