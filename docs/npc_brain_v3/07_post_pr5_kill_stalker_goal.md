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
- Social hunt intel is canonicalized into `target_intel`:
  - `intel_from_trader` → `target_intel`
  - `intel_from_stalker` → `target_intel`
- `TargetBelief` must reconstruct `last_known_location_id` from canonical hunt intel so bought/received intel becomes actionable on the next tick.
- `kill_stalker` completion requires confirmed evidence (`target_death_confirmed`) and dead target state.
- `ENGAGE_TARGET` uses combat monitor stage (`start_combat` → `monitor_combat` → `confirm_kill`) to avoid premature confirmation.
- After goal completion, objective flow transitions to `LEAVE_ZONE`.

## Hunt intel loop regression rule

The hunt pipeline must not get stuck in:

- `LOCATE_TARGET`
- `ask_for_intel`
- ActivePlan completed
- `LOCATE_TARGET`
- `ask_for_intel`
- ...

Correct flow after useful intel:

1. trader/stalker provides target location intel;
2. memory bridge stores canonical `target_intel`;
3. `TargetBelief.last_known_location_id` becomes known on the next tick;
4. objective generation selects `TRACK_TARGET`;
5. ActivePlan becomes `travel_to_location` → `search_target`.

If useful intel was not produced, runtime/repair logic should avoid repeating the same no-progress intel step forever.
