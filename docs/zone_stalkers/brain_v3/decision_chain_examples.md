# Decision Chain Examples — Reference Guide

This is a reference guide for NPC Brain v3 runtime decision flows. Each example traces how an NPC moves from a starting condition through objective selection, plan execution, and completion. Use these to understand how the pipeline works in practice.

---

## 1. Poor NPC Without a Weapon

**Starting condition:** NPC has no weapon and insufficient money to buy one.

**Flow:**

1. `no_weapon + insufficient_money` → objective: `GET_MONEY_FOR_RESUPPLY`
2. `FIND_ARTIFACTS` — explore and collect artifacts
3. `SELL_ARTIFACTS` — trade at nearest trader
4. `RESUPPLY_WEAPON` — buy a weapon
5. `RESUPPLY_AMMO` — buy compatible ammo
6. Resume main strategic goal

---

## 2. Hunger and Thirst Threshold Behavior

Three zones:

- **Below soft threshold:** strategic executable objective continues uninterrupted.
- **Above soft threshold:** `RESTORE_FOOD` / `RESTORE_WATER` may be selected, depending on urgency score vs. strategic priority.
- **Critical zone:** blocking survival objective overrides all strategic/economic actions.

---

## 3. Emission Interruption

**Starting condition:** NPC is sleeping, traveling, or exploring when an emission warning fires.

**Flow:**

```
emission warning
→ interrupt current action
→ REACH_SAFE_SHELTER (emergency)
→ WAIT_IN_SHELTER until emission ends
→ resume prior plan (repair) or re-evaluate
```

The prior `ActivePlanV3` is preserved with context so repair can continue after safety.

---

## 4. Memory-Assisted Routing

When an NPC has previously visited a trader or observed a resource source, memory contributes to the decision:

- Objective `source_refs` reference the memory record.
- `brain_trace.memory_used` shows which records drove the decision.
- Route and target quality improve because the NPC avoids traversing unknown paths.

---

## 5. ActivePlan Interruption and Recovery

**Starting condition:** long artifact-hunting plan in progress when an interrupting event occurs.

**Flow:**

1. Plan started: `FIND_ARTIFACTS → travel → explore → pickup`
2. Interrupting event fires (emission warning, critical need, etc.)
3. Plan enters repair state — current step marked `failed`
4. Repair inserts safety substeps (shelter/wait) or re-evaluates
5. Plan resumes or a new plan replaces it — full objective context is preserved through `memory_refs`

---

## 6. End-to-End: get_rich Goal

**Full chain:**

```
spawn
→ FIND_ARTIFACTS / GET_MONEY_FOR_RESUPPLY
→ travel_to_location + explore_location
→ artifact pickup
→ SELL_ARTIFACTS
→ trade_sell_item
→ global_goal_completed
→ LEAVE_ZONE
→ has_left_zone = true
```

---

## 7. End-to-End: kill_stalker (Known Target)

**Starting condition:** NPC spawns with `target_last_known_location` already in memory.

**Full chain:**

```
spawn + target_last_known_location in memory
→ TRACK_TARGET
→ travel_to_location + search_target
→ target_seen
→ ENGAGE_TARGET
→ start_combat → monitor_combat
→ CONFIRM_KILL / target_death_confirmed
→ global_goal_completed
→ LEAVE_ZONE
→ has_left_zone = true
```

---

## 8. End-to-End: kill_stalker (Unknown Target)

**Starting condition:** NPC spawns without any target memory.

**Full chain:**

```
spawn (no target memory)
→ LOCATE_TARGET
→ ask_for_intel (trader/stalker provides location)
  → intel bridged to canonical target_intel in memory_v3
  → TargetBelief.last_known_location_id becomes known on next tick
→ TRACK_TARGET (not another LOCATE_TARGET loop)
→ travel_to_location + search_target
→ ENGAGE_TARGET → CONFIRM_KILL
→ LEAVE_ZONE
```

**Critical invariant:**

Once intel resolves a target location, the next tick must prefer `TRACK_TARGET`. The runtime must not fall back into `LOCATE_TARGET → ask_for_intel` every tick for the same resolved hunt.
