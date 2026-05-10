# NPC Brain v3 — Mechanics Reference

NPC Brain v3 replaces fragmented, reactive behavior with a deterministic decision chain where goals, constraints, memory, and execution are explicitly modeled. This document describes the current architecture.

## Full Decision Pipeline

Each NPC tick runs the following pipeline in order:

1. **Agent state** — read hp, hunger, thirst, sleepiness, radiation, inventory, equipment, location, global goal.
2. **NeedEvaluationResult** — compute scores, ImmediateNeed, ItemNeed, liquidity_summary, combat_readiness.
3. **MemoryStore v3** — ingest new observations, update indexes, retrieve relevant records.
4. **BeliefState** — assemble world snapshot + memory retrieval into planner-ready context.
5. **Objective candidates** — generate all applicable objectives for the current situation.
6. **ObjectiveDecision** — score and select the best objective, applying blockers and anti-ping-pong rules.
7. **Objective → Intent adapter** — carry objective semantics into the execution layer.
8. **Planner** — build executable steps from the selected intent.
9. **ActivePlan v3** — source of truth for multi-step long-running execution. Handles continuation, repair, and abort.
10. **Trace / memory / debug UI export** — write `brain_trace`, profile panels, compact/full JSON.

### Core invariants

- **Objective = reason** for choosing behavior.
- **Intent = execution bridge** from decision layer to planner.
- **Memory v3 = structured, indexed, queryable** state extension.
- **ActivePlan v3 = source of truth** for long actions, continuation, repair, abort.

---

## Sleep System

Sleep runs in **30-minute intervals**. Partial effect is applied for interrupted sleep — progress is not fully lost. Sleep completes when `sleepiness = 0`.

### Safety rules

- Critical survival/context conditions can interrupt normal continuation.
- Unsafe sleep continuation is rejected (e.g., during emission risk).
- Post-death action state is not allowed to persist — `scheduled_action` and `action_queue` are cleared after death.

---

## Needs Model

### NeedEvaluationResult

`NeedEvaluationResult` is the primary evaluation output for decision/planner integration. It includes:

- `scores` — survival, material, and goal pressure scores.
- `ImmediateNeed` — urgent safety/survival context handling.
- `ItemNeed` — stock/equipment deficits and resupply pressure.
- `liquidity_summary` — available liquidity for trade decisions.
- `combat_readiness` — combat prerequisites check.

### ImmediateNeed and ItemNeed

- `ImmediateNeed` handles urgent safety/survival contexts (critical hp, critical thirst/hunger).
- `ItemNeed` models stock and equipment deficits and resupply pressure.
- `affordability_hint` reflects whether immediate purchase is feasible.

### Liquidity model

Sale candidates are classified as:

- `safe` — available for sale without risk.
- `risky` — sale degrades position.
- `emergency_only` — sell only if critical.
- `forbidden` — never sell.

Protection rules:
- Do not liquidate equipped weapon or armor.
- Protect compatible ammo required for equipped weapon.
- Protect last food, drink, or medicine stock needed for survival.

### Purchase and resupply rules

- Use **cheapest viable survival buy** for urgent restoration.
- Use `reserve_basic` buy mode for food/drink stock refill.
- Prevent unaffordable buy loops.
- If purchase is impossible, use **GET_MONEY fallback**.

### Regression policy

- Minor hunger/thirst below soft threshold must not degrade into wait-loop behavior.
- Critical hunger/thirst overrides non-critical strategic/economic actions.
- Resupply must not sell protected survival-critical resources for non-critical upgrades.

---

## MemoryStore v3

### Core entities

- `MemoryRecord` — a single memory entry with kind, layer, indexes, and retrieval score.
- `MemoryQuery` — a structured query for retrieving records.
- Indexed storage with retrieval scoring.

### Memory layers

```
working     — short-term active context
episodic    — events experienced by the NPC
semantic    — learned facts about the world
spatial     — location/route knowledge
social      — knowledge about other agents
threat      — known dangers and hazards
goal        — goal-related state and history
```

### Indexes

Records are indexed by:
- `by_layer`, `by_kind`, `by_location`, `by_entity`, `by_item_type`, `by_tag`.

### Legacy bridge

The live bridge from legacy `_add_memory` into memory_v3:
- Performs lazy import of old memory on first access.
- Skips transient noise entries (e.g., `sleep_interval_applied`).
- Uses real `agent_id` (not display name) in stored records.
- Extracts and stores `entity_ids` for structured retrieval.
- Hunt intel from social sources is canonicalized on bridge:
  - `intel_from_trader` → `target_intel`
  - `intel_from_stalker` → `target_intel`
- Canonical `target_intel` preserves target/source entity references, location, confidence, and source tags.

### Retrieval and lifecycle

- Retrieval scoring drives ranked memory usage.
- `last_accessed_turn` is updated on each read.
- Decay and consolidation maintain memory quality and relevance over time.

### Target memory taxonomy

Memory kinds used for hunt/kill operations:

```
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

## BeliefState

BeliefState combines a world snapshot and memory retrieval into a planner-ready context.

Mandatory planner lookups resolved by BeliefState:
- `find_trader`
- `find_food`
- `find_water`
- `avoid_threat`

`brain_trace.memory_used` exposes the retrieved records used for the current decision/planning cycle.

### TargetBelief

For hunt/kill operations, `TargetBelief` is built in the runtime and passed into objective generation.

`TargetBelief.last_known_location_id` may be derived from:
- `target_seen`
- `target_last_known_location`
- `target_intel` (including migrated `intel_from_trader` / `intel_from_stalker`)

If social intel has already resolved to a target location, the next belief rebuild must expose a non-null `last_known_location_id`, so objective generation promotes tracking rather than repeating generic intel collection.

---

## Objective Layer

### Objective model

Each objective has:

```
key, source, reason, urgency, expected_value, risk, time_cost,
resource_cost, confidence, memory_confidence, goal_alignment,
source_refs, blockers, metadata
```

### Canonical objectives

```
RESTORE_FOOD, RESTORE_WATER, HEAL_SELF, REST
RESUPPLY_FOOD, RESUPPLY_DRINK, RESUPPLY_AMMO, RESUPPLY_MEDICINE, RESUPPLY_WEAPON, RESUPPLY_ARMOR
GET_MONEY_FOR_RESUPPLY
FIND_ARTIFACTS, SELL_ARTIFACTS
REACH_SAFE_SHELTER, WAIT_IN_SHELTER
LOCATE_TARGET, TRACK_TARGET, VERIFY_LEAD, GATHER_INTEL
ENGAGE_TARGET, CONFIRM_KILL, RETREAT_FROM_TARGET, RECOVER_AFTER_COMBAT
LEAVE_ZONE
```

### Threshold split and objective sources

- Soft thresholds exist for food, water, and sleep.
- Objective source semantics are split as:
  - `immediate_need` — critical/urgent survival need.
  - `soft_need` — below soft threshold but not critical.
  - `recovery_need` — recovery after deficit.

### Scoring and selection

- Objective scoring uses urgency, value, risk, time, resource, and confidence alignment terms.
- Blockers reduce score or disqualify infeasible alternatives.
- Maintenance-vs-strategic anti-ping-pong protects strategic continuity.
- Feasibility gate rejects actionable objective outcomes that collapse to wait-only plans.

### Objective → Intent adapter

The adapter carries objective semantics to the execution layer.

**Forced resupply category** is mandatory for `RESUPPLY_*`:
- `RESUPPLY_FOOD` → only food
- `RESUPPLY_DRINK` → only drink/water
- `RESUPPLY_AMMO` → compatible ammo only
- `RESUPPLY_MEDICINE` → medicine only
- `RESUPPLY_WEAPON` → weapon
- `RESUPPLY_ARMOR` → armor

### Decision memory writes

Each decision writes to memory_v3:
- `action_kind = objective_decision`
- `objective_key`, `objective_score`, `objective_source`, `objective_reason`
- `adapter_intent_kind`, `plan_step`

`current_goal` is derived from the selected objective trajectory.

---

## ActivePlan v3

`ActivePlanV3` is the single persistent runtime source of truth for long-running NPC behavior.

### Runtime ownership

- `ObjectiveDecision` explains **why** the NPC acts.
- The adapter intent is an internal execution bridge only.
- `ActivePlanV3` owns the multi-step operation.
- `scheduled_action` is only the currently executing child runtime step.
- `action_queue` is legacy compatibility only and stays empty for v3 bots during normal operation.

### Tick order

1. Migrate legacy save/runtime remnants (`_v2_context`, untagged `scheduled_action`).
2. Process current `scheduled_action`.
3. Evaluate/continue/repair/abort/complete `active_plan_v3`.
4. Only if no valid active plan exists, run NPC Brain v3 objective selection.
5. Create/save a new `ActivePlanV3` and start its first step.

### Naming and public debug semantics

- `brain_v3_context` replaces legacy `_v2_context`.
- Decision trace event is `decision="objective_decision"`.
- Public debug/export surfaces: current objective, objective ranking, adapter intent, active plan, current runtime step, memory used.

### ActivePlan model fields

```
id, objective_key, status, created_turn, updated_turn,
steps, current_step_index,
source_refs, memory_refs,
repair_count, abort_reason
```

### Step lifecycle states

```
pending → running → completed / failed / skipped
```

### Lifecycle rules

**Creation** — every actionable objective creates an `ActivePlanV3`, including one-step plans:
- restore water/food, heal self, wait in shelter, rest/sleep.

**Execution**
- Mark step `running`, set `started_turn`.
- Tag `scheduled_action` with `active_plan_id`, `active_plan_step_index`, `active_plan_objective_key`.
- When complete: mark step `completed`, set `completed_turn`, advance `current_step_index`.
- When validation fails: mark step `failed`, request repair or abort.

**Completion**
- Advance `current_step_index` until all steps done.
- Complete and clear the plan.

### Repair behavior

Key repair cases:

| Repair trigger | Action |
|---|---|
| `emission_interrupt` | Insert shelter/wait substeps or equivalent safe interruption flow |
| `target_location_empty` | Do not retry the same broken target; re-evaluate or replace target |
| `trader_unavailable` | Pick alternative trader if possible, otherwise abort/re-evaluate |
| `supplies_consumed_mid_plan` | Insert restore/resupply or abort/re-evaluate |
| `hunt_intel_no_progress` | Do not allow `LOCATE_TARGET → ask_for_intel` to complete repeatedly without producing usable target location evidence |

### Memory and evidence integration

- `memory_refs` preserve the evidence chain from `source_refs` (`memory:*`).
- Repair checks use both legacy memory and `memory_v3` where relevant.
- ActivePlan lifecycle events are bridged into memory_v3:
  - `active_plan_created`, `active_plan_step_started`, `active_plan_step_completed`
  - `active_plan_step_failed`, `active_plan_repair_requested`, `active_plan_repaired`
  - `active_plan_aborted`, `active_plan_completed`

### ENGAGE_TARGET execution

`ENGAGE_TARGET` runs as:
1. `start_combat`
2. `monitor_combat`
3. `confirm_kill`

`monitor_combat` blocks kill confirmation while combat is still active.

### LEAVE_ZONE

`LEAVE_ZONE` is a first-class ActivePlan step:
1. `travel_to_location` (exit) if needed.
2. `leave_zone` terminal step — sets `has_left_zone = true`, performs runtime cleanup.

---

## Hunt Intel Loop Prevention

The hunt pipeline must not get stuck in:

```
LOCATE_TARGET → ask_for_intel → ActivePlan completed → LOCATE_TARGET → ask_for_intel → ...
```

Correct flow after useful intel:

1. Trader/stalker provides target location intel.
2. Memory bridge stores canonical `target_intel`.
3. `TargetBelief.last_known_location_id` becomes known on the next tick.
4. Objective generation selects `TRACK_TARGET`.
5. ActivePlan becomes `travel_to_location → search_target`.

If useful intel was not produced, repair logic prevents repeating the same no-progress intel step.

---

## Current Limitations

- `action_queue` remains as legacy compatibility only; v3 bots should not use it.
- Full hunt operation (intercept, ambush, stakeout) is planned but not yet implemented — see [`../future/combat_encounter_system.md`](../future/combat_encounter_system.md).
- Brain trace is written for all v3 NPCs by default; gating to selected agents is a planned CPU optimization — see [`../optimization/planned/`](../optimization/planned/README.md).
