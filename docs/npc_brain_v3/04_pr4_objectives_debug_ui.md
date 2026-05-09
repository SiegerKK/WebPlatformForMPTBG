# PR4 — Objectives, Scoring, Debug UI

## Objective layer

Objective model fields:

- `key`, `source`, `reason`, `urgency`, `expected_value`, `risk`, `time_cost`,
- `resource_cost`, `confidence`, `memory_confidence`, `goal_alignment`,
- `source_refs`, `blockers`, `metadata`.

## Objective generation

Canonical objectives include:

- `RESTORE_FOOD`, `RESTORE_WATER`, `HEAL_SELF`, `REST`,
- `RESUPPLY_*`, `GET_MONEY_FOR_RESUPPLY`,
- `FIND_ARTIFACTS`, `SELL_ARTIFACTS`,
- `REACH_SAFE_SHELTER`, `WAIT_IN_SHELTER`,
- hunt placeholders (`HUNT_TARGET` + decomposed placeholders).

## Threshold split and sources

- Soft thresholds exist for food/water/sleep.
- Objective source semantics are split as:
  - `immediate_need`,
  - `soft_need`,
  - `recovery_need`.

## Scoring and selection behavior

- Objective scoring uses urgency/value/risk/time/resource/confidence alignment terms.
- Blockers reduce or disqualify infeasible alternatives.
- Maintenance-vs-strategic anti-ping-pong protects strategic continuity.
- Feasibility gate rejects actionable objective outcomes that collapse to wait-only plans.

## Objective → Intent adapter

- Adapter carries objective semantics to execution layer.
- **Forced resupply category** is mandatory for `RESUPPLY_*`:
  - `RESUPPLY_FOOD` → only food,
  - `RESUPPLY_DRINK` → only drink/water,
  - `RESUPPLY_AMMO` → compatible ammo,
  - `RESUPPLY_MEDICINE` → medicine,
  - `RESUPPLY_WEAPON` → weapon,
  - `RESUPPLY_ARMOR` → armor.

## Objective-first memory and trace

Decision memory writes:

- `action_kind = objective_decision`,
- `objective_key`, `objective_score`, `objective_source`, `objective_reason`,
- `adapter_intent_kind`, `plan_step`.

`current_goal` is derived from selected objective trajectory.

## Debug UI and export scope

UI panels:

- NPC Brain v3,
- Objective Ranking,
- Needs & Constraints,
- Memory Used,
- Runtime Action,
- Memory Timeline,
- Memory v3 Summary,
- Raw Debug (collapsed).

Exports:

- Full debug JSON,
- Compact NPC history JSON.

Compact export must distinguish:

- `latest_event`,
- `latest_decision`,
- `current_objective`,
- `current_runtime`.

> ActivePlan lifecycle/repair is not part of PR4; it belongs to PR5.
