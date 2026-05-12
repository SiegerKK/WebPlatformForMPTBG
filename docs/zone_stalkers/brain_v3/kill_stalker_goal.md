# Kill Stalker Goal

`global_goal = kill_stalker` is a long-running system operation, not a single action. Eliminating another NPC is built on Objective + ActivePlan + Memory: the hunter gathers intel, tracks the target, engages in combat, and confirms the kill before transitioning to `LEAVE_ZONE`.

---

## Target Selection

The target is identified by `kill_target_id` on the agent. This is set at spawn and does not change during the run. The hunter always pursues this specific NPC.

---

## Information Gathering

When the hunter has no target location, it pursues `LOCATE_TARGET`:

1. `ask_for_intel` at a trader — paid intel returns `target_intel` (canonical `target_last_known_location` with confidence ~0.70).
2. `question_witnesses` — co-located stalkers provide `target_intel` or `target_last_known_location` with confidence ~0.55.
3. Social intel is canonicalized in the memory bridge: `intel_from_trader` / `intel_from_stalker` → `target_intel`.

After intel collection, `TargetBelief.last_known_location_id` becomes non-null on the next tick. Objective generation then selects `TRACK_TARGET` rather than repeating `LOCATE_TARGET`.

---

## Target Search

With a known `best_location_id`, the hunter pursues `TRACK_TARGET` or `VERIFY_LEAD`:

- Travels to the best non-exhausted location.
- Executes `search_target`, `look_for_tracks`, `question_witnesses`.
- `target_not_found` applies staged confidence suppression (see [hunt_search_and_traces.md](./hunt_search_and_traces.md)).
- After 3 failed searches at the same location, it becomes `exhausted` and the hunter switches to a different objective or location.

---

## target_found

When `search_target` writes `target_seen`:
- `TargetBelief` flags `recently_seen = true`.
- `VERIFY_LEAD` / `TRACK_TARGET` / `PURSUE_TARGET` stop immediately.
- Objective generation selects `ENGAGE_TARGET` on the next tick.

---

## Engage Target

`ENGAGE_TARGET` is executed as a three-step `ActivePlanV3`:

1. `start_combat` — creates a combat state, stores encounter reference.
2. `monitor_combat` — advances combat each tick; blocks completion while combat is active.
3. `confirm_kill` — only runs after combat is resolved.

`monitor_combat` prevents premature kill confirmation. The step stays `running` until the encounter reaches a terminal state.

---

## Kill Confirmation

`CONFIRM_KILL` requires:
- Confirmed evidence in memory: `target_death_confirmed`.
- Target agent state: `is_alive = false`.

`kill_stalker` completion is only valid when both conditions hold.

---

## After Kill Confirmation

After goal completion, the objective flow transitions to `LEAVE_ZONE`:
1. `travel_to_location` (exit location) if needed.
2. `leave_zone` terminal step — sets `has_left_zone = true` and performs runtime cleanup.

---

## Required Target Memories

The hunt operation produces and consumes these memory kinds:

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

## Hunt Intel Loop Prevention

The hunt pipeline must not get stuck in:
```
LOCATE_TARGET → ask_for_intel → completed → LOCATE_TARGET → ask_for_intel → ...
```

Correct post-intel flow:
1. Trader/stalker provides location intel.
2. Memory bridge stores canonical `target_intel`.
3. `TargetBelief.last_known_location_id` is non-null on the next tick.
4. Objective generation selects `TRACK_TARGET`.
5. `ActivePlanV3` becomes `travel_to_location → search_target`.

If intel collection did not produce a usable location, repair/re-evaluation prevents the same no-progress step from repeating indefinitely.

---

## Current Combat and Hunt Integration

Combat itself is currently a simple flow (`start_combat → monitor_combat → confirm_kill`). Full round-based combat, wounds, ambush, intercept, and stakeout are planned features — see [`../../archive/combat_encounter_system_pr_implementation.md`](../../archive/combat_encounter_system_pr_implementation.md).
