# Post-PR5 — Kill Stalker Goal Operation

## Principle

`kill_target_id` is not a direct action. Eliminating another NPC is a long-running system operation built on Objective + ActivePlan + Memory.

## Required mechanics

- target memory and intel gathering,
- last known location tracking and movement handling,
- equipment/combat strength observation,
- combat readiness validation,
- stalking/tracking,
- ambush/intercept,
- engagement,
- kill confirmation,
- retreat/recovery.

## Required target memories

- `target_seen`,
- `target_last_known_location`,
- `target_not_found`,
- `target_route_observed`,
- `target_equipment_seen`,
- `target_combat_strength_observed`,
- `target_death_confirmed`,
- `target_intel`.

## Hunt objective decomposition

- `LOCATE_TARGET`,
- `PREPARE_FOR_HUNT`,
- `TRACK_TARGET`,
- `INTERCEPT_TARGET`,
- `AMBUSH_TARGET`,
- `ENGAGE_TARGET`,
- `CONFIRM_KILL`,
- `RETREAT_FROM_TARGET`,
- `RECOVER_AFTER_COMBAT`.

## ActivePlan hunt staging requirements

- multi-stage execution with monitor-driven repair,
- repair when target moved,
- abort when target is too strong,
- resume after resupply/heal.

## Test matrix (post-PR5)

- target unknown → gather intel,
- target seen → track,
- target moved → repair,
- insufficient ammo → prepare/resupply,
- target killed → confirm kill,
- failed ambush → retreat/recover.

## Boundaries with earlier PR docs

- PR2 keeps only combat-readiness prerequisites.
- PR3 keeps target-memory taxonomy prerequisites.
- PR4 keeps hunt-objective placeholders.
- Full hunt operation is canonical only in this document.

## Implemented final mechanics

- `TargetBelief` is built in runtime and passed into objective generation.
- Memory bridge maps hunt events to `memory_v3` (`target_seen`, `target_not_found`, `target_moved`, `target_death_confirmed`) with entity/location indexes.
- `kill_stalker` completion requires confirmed evidence (`target_death_confirmed`) and dead target state.
- `ENGAGE_TARGET` uses combat monitor stage (`start_combat` → `monitor_combat` → `confirm_kill`) to avoid premature confirmation.
- After goal completion, objective flow transitions to `LEAVE_ZONE`.
