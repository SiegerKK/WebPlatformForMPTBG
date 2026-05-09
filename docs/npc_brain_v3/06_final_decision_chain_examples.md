# Final Decision Chain — Examples

This document is example-focused (runtime flows), not contract-focused.

## 1) Poor NPC without weapon

Flow:

1. `no_weapon + insufficient_money`
2. `GET_MONEY_FOR_RESUPPLY`
3. `FIND_ARTIFACTS`
4. `SELL_ARTIFACTS`
5. `RESUPPLY_WEAPON`
6. `RESUPPLY_AMMO`
7. resume main strategic goal

## 2) Hunger / thirst threshold behavior

- Below soft threshold: strategic executable objective continues.
- Above soft threshold: `RESTORE_FOOD` / `RESTORE_WATER` may be selected.
- Critical zone: blocking survival objective overrides strategic flow.

## 3) Emission interruption

`warning during sleep/travel/explore` → `emergency shelter` → `wait in shelter` → resume or repair prior plan.

## 4) Memory-assisted routing

Remembered trader/resource source contributes to:

- objective `source_refs`,
- trace `memory_used`,
- improved route/target quality.

## 5) ActivePlan interruption/recovery

Long artifact plan:

- started,
- interrupted by environment/context,
- resumed or repaired with updated assumptions,
- continues without losing full decision context.

## 6) Full E2E chain — get_rich

`spawn` → `FIND_ARTIFACTS`/`GET_MONEY_FOR_RESUPPLY` → `travel_to_location` + `explore_location` → artifact pickup → `SELL_ARTIFACTS` → `trade_sell_item` → `global_goal_completed` → `LEAVE_ZONE` → `has_left_zone=true`.

## 7) Full E2E chain — kill_stalker (known target)

`spawn` + `target_last_known_location` → `TRACK_TARGET` → `search_target`/`target_seen` → `ENGAGE_TARGET` (`start_combat` → `monitor_combat`) → `CONFIRM_KILL`/`target_death_confirmed` → `global_goal_completed` → `LEAVE_ZONE` → `has_left_zone=true`.

## 8) Full E2E chain — kill_stalker (unknown target)

`spawn` without target memory → `LOCATE_TARGET` → `ask_for_intel` (`intel_from_trader`/target location evidence) → `TRACK_TARGET` → `ENGAGE_TARGET` → `CONFIRM_KILL` → `LEAVE_ZONE`.
