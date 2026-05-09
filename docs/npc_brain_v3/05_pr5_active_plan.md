# PR5 — ActivePlan v3

## Role

`ActivePlan v3` is the source of truth for long-running NPC execution.

## ActivePlan model

Required fields:

- `id`, `objective_key`, `status`, `created_turn`, `updated_turn`,
- `steps`, `current_step_index`,
- `source_refs`, `memory_refs`,
- `repair_count`, `abort_reason`.

Step lifecycle states:

- `pending`, `running`, `completed`, `failed`, `skipped`.

## Monitor/control rules

Plan monitor can:

- `continue`,
- `pause`,
- `resume`,
- `repair`,
- `abort`.

## Repair examples

- trader unavailable,
- route blocked,
- target location empty,
- emission interrupt,
- supplies consumed mid-plan.

## Relation to Objective layer

- `ObjectiveDecision` starts/restarts plan context.
- ActivePlan may request re-evaluation when assumptions break.

## Memory integration

- `memory_refs` preserve evidence chain.
- `confirmed_empty` marks invalidated targets.
- stale-memory invalidation triggers repair/reselection.

## PR5 test focus

- long artifact loop,
- interruption by emission,
- sleep/recovery continuation,
- resupply then resume,
- failed-plan repair.

> Full kill-stalker operation logic is documented separately in post-PR5 document.
