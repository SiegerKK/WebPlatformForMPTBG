# PR5 — ActivePlan v3

## Final runtime role

`ActivePlanV3` is the single persistent runtime source of truth for long-running bot NPC behavior.

Runtime ownership after PR5:

- `ObjectiveDecision` explains **why** the NPC acts.
- adapter intent remains an internal execution bridge only.
- `ActivePlanV3` owns the multi-step operation.
- `scheduled_action` is only the currently executing child runtime step.
- `action_queue` is legacy compatibility only and should stay empty for v3 bots during normal operation.

## Runtime order

Recommended tick order:

1. migrate legacy save/runtime remnants (`_v2_context`, untagged `scheduled_action`);
2. process current `scheduled_action`;
3. evaluate/continue/repair/abort/complete `active_plan_v3`;
4. only if no valid active plan exists, run NPC Brain v3 objective selection;
5. create/save a new `ActivePlanV3` and start its first step.

## Naming and public debug semantics

PR5 final naming is v3-first:

- `brain_v3_context` replaces `_v2_context`;
- decision trace event is `decision="objective_decision"`, not `new_intent`;
- public debug/export surfaces:
  - current objective,
  - objective ranking,
  - adapter intent,
  - active plan,
  - current runtime step,
  - memory used.

Legacy names may still be read for save compatibility, but new ticks must not write them.

## ActivePlan model

Required fields:

- `id`, `objective_key`, `status`, `created_turn`, `updated_turn`,
- `steps`, `current_step_index`,
- `source_refs`, `memory_refs`,
- `repair_count`, `abort_reason`.

Step lifecycle states:

- `pending`,
- `running`,
- `completed`,
- `failed`,
- `skipped`.

## Lifecycle rules

### Creation

Every actionable objective should create an `ActivePlanV3`, including one-step plans such as:

- restore water / food,
- heal self,
- wait in shelter,
- rest/sleep.

### Execution

When a step starts:

- mark step `running`,
- set `started_turn`,
- tag `scheduled_action` with:
  - `active_plan_id`,
  - `active_plan_step_index`,
  - `active_plan_objective_key`.

When a scheduled action completes:

- mark step `completed`,
- set `completed_turn`,
- advance `current_step_index`,
- start the next step or complete and clear the plan.

When runtime validation fails:

- mark the current step failed,
- request repair or abort,
- preserve objective context until repair or clean re-decision completes.

## Repair behavior

Important repair cases:

- `emission_interrupt` → insert shelter / wait substeps or equivalent safe interruption flow;
- `target_location_empty` → do not retry the same broken target forever; re-evaluate or replace target;
- `trader_unavailable` → pick alternative trader if possible, otherwise abort/re-evaluate;
- `supplies_consumed_mid_plan` → insert restore/resupply or abort/re-evaluate.
- `hunt_intel_no_progress` → do not allow `LOCATE_TARGET → ask_for_intel` to complete repeatedly without producing usable target location evidence.

## Memory and evidence integration

- `memory_refs` preserve evidence chain from `source_refs` (`memory:*`);
- repair checks should use both legacy memory and `memory_v3` where relevant;
- ActivePlan lifecycle events must bridge into `memory_v3`:
  - `active_plan_created`,
  - `active_plan_step_started`,
  - `active_plan_step_completed`,
  - `active_plan_step_failed`,
  - `active_plan_repair_requested`,
  - `active_plan_repaired`,
  - `active_plan_aborted`,
  - `active_plan_completed`.

## Validation focus

PR5 acceptance should cover:

- no new `_v2_context` writes;
- `brain_v3_context` exists after tick;
- decision trace uses `objective_decision`;
- every actionable objective creates `active_plan_v3`;
- `scheduled_action` is tagged child runtime state;
- `action_queue` stays empty for v3 bots;
- completion/abort/repair lifecycle writes trace + memory and clears state correctly;
- memory_v3 receives ActivePlan lifecycle entries;
- frontend/profile/export show v3-first fields.

> Full kill-stalker operation logic is documented separately in post-PR5 material.

## Hunt-specific ActivePlan invariant

For `LOCATE_TARGET`, `ask_for_intel` is only meaningful if it produces usable hunt evidence.

Required postcondition:

- after intel collection, target reasoning must have either:
  - `TargetBelief.last_known_location_id != null`, or
  - canonical location evidence for the target in memory (`target_intel`, `target_last_known_location`, `target_seen`).

If that postcondition is not met:

- the plan must not silently count this as successful progress forever;
- repair / re-evaluation should prevent one-step loops against the same stale source.

## Final runtime additions after PR5

- `ENGAGE_TARGET` is executed as:
  1. `start_combat`
  2. `monitor_combat`
  3. `confirm_kill`
- `monitor_combat` blocks kill confirmation while combat is still active.
- `LEAVE_ZONE` is now a first-class ActivePlan step:
  - `travel_to_location` (exit) when needed,
  - `leave_zone` terminal step (`has_left_zone = true`, runtime cleanup).
