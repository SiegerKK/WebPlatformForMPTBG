# NPC Brain v3 — Hunt-related mechanics that should be implemented before PR 5

> Назначение: отдельный список частей, которые лучше заложить раньше PR 5, чтобы post-PR5 `kill_stalker` operation не потребовала переделки уже реализованных PR 2–PR 5.
>
> Основной post-PR5 документ: `npc_brain_v3_post_pr5_kill_stalker_operation.md`

---

# 1. Короткий вывод

Большую часть сложной охоты лучше делать после PR 5.

Но несколько вещей стоит заложить раньше:

```text
PR 2:
  - ammo/weapon/medicine ItemNeed must be reliable;
  - no suicidal hunt if no weapon/ammo;
  - heal_self must use affordability/liquidity;
  - combat readiness inputs must be available.

PR 3:
  - memory kinds for target_seen / target_not_found / target_equipment_seen;
  - memory indexes must support entity_id = target_id;
  - memory_used.used_for should include locate_target / prepare_for_hunt.

PR 4:
  - objective framework should reserve hunt objective keys;
  - scoring must support blocking readiness conditions;
  - continue-vs-switch should handle hunt interruption.

PR 5:
  - ActivePlan must support pause/resume/repair generally;
  - no hunt-specific hack should be needed.
```

---

# 2. What to add in PR 2

PR 2 is about needs, item gaps and liquidity. Hunt needs depend on that.

## 2.1. Ammo need must be correct

Ensure:

```text
if weapon exists:
  ammo ItemNeed uses compatible ammo type;
  ammo urgency increases when ammo count is low;
  zero ammo creates strong ItemNeed.
```

This is required because `kill_stalker` must not blindly attack with no ammo.

## 2.2. Weapon need must be explicit

If:

```text
global_goal = kill_stalker
weapon = null
```

then:

```text
ItemNeed.weapon should be active
```

Later PR 4 can transform this into:

```text
PREPARE_FOR_HUNT
```

But PR 2 must provide the input.

## 2.3. Medicine stock should be available as ItemNeed

Hunting should consider:

```text
medkit/bandage availability
```

So PR 2 should keep medicine stock as explicit `ItemNeed.medicine`.

## 2.4. Heal self should be PR2-compatible

`heal_self` should use:

```text
ImmediateNeed.heal_now
inventory heal item
affordability
liquidity
```

not old `money == 0` logic.

This is important because a wounded hunter should:

```text
heal before hunt
```

or:

```text
retreat/heal during hunt
```

## 2.5. Avoid selling hunt-critical equipment

Liquidity policy must never sell:

```text
equipped weapon
equipped armor
compatible ammo below reserve
last medkit when hp low
```

For future hunt logic, this is essential.

## 2.6. Optional PR 2 trace fields

Add to brain_trace item needs:

```text
weapon missing
ammo missing
medicine missing
```

No need for `CombatReadiness` yet.

---

# 3. What to add in PR 3

PR 3 is the most important precondition for systemic hunting.

## 3.1. Add memory kinds

The PR 3 memory contract should reserve these kinds:

```text
target_seen
target_last_known_location
target_not_found
target_route_observed
target_equipment_seen
target_combat_strength_observed
target_death_confirmed
target_intel
```

Do not implement full hunt logic yet.

Just allow MemoryStore to store/retrieve them.

## 3.2. Entity index must support target lookup

Memory indexes must allow:

```python
retrieve_memory(
    entity_ids=(target_id,),
    tags=("target",)
)
```

This is required for future `TargetBelief`.

## 3.3. `used_for` values

PR 3 should allow these `memory_used.used_for` values:

```text
locate_target
track_target
prepare_for_hunt
engage_target
confirm_kill
```

Even if not used yet.

## 3.4. Preserve target observation merge semantics

If target is repeatedly seen in same location:

```text
target_seen:
  first_seen_turn
  last_seen_turn
  times_seen
```

If target not found:

```text
target_not_found should not overwrite target_seen;
it should coexist and be used later to lower confidence.
```

## 3.5. Memory retention

Target-related memories should have medium/high retention:

```text
target_seen:
  medium/high

target_equipment_seen:
  high

target_death_confirmed:
  high

target_not_found:
  medium
```

---

# 4. What to add in PR 4

PR 4 introduces objective scoring. It should reserve the shape of hunt objectives.

## 4.1. Reserve objective keys

Add these objective keys to PR 4 contracts/enums:

```text
LOCATE_TARGET
PREPARE_FOR_HUNT
TRACK_TARGET
INTERCEPT_TARGET
AMBUSH_TARGET
ENGAGE_TARGET
CONFIRM_KILL
RETREAT_FROM_TARGET
RECOVER_AFTER_COMBAT
```

Implementation can be minimal, but names should not be invented later ad-hoc.

## 4.2. HUNT_TARGET should not mean "attack now"

Important semantic rule:

```text
HUNT_TARGET / kill_stalker global goal does not automatically mean ENGAGE_TARGET.
```

Instead:

```text
global_goal kill_stalker
→ generate hunt-related objective candidates
```

Possible selected objective:

```text
PREPARE_FOR_HUNT
LOCATE_TARGET
TRACK_TARGET
ENGAGE_TARGET
```

## 4.3. Objective scoring must support blockers

PR 4 should support objective blockers like:

```text
no_weapon
low_ammo
hp_low
target_location_unknown
target_too_strong
emission_threat
critical_survival_need
```

Even if `CombatReadiness` is implemented later, the scoring shape should allow blockers.

## 4.4. Rejected alternatives in brain_trace

PR 4 trace should be able to say:

```text
HUNT_TARGET rejected:
  not enough ammo

PREPARE_FOR_HUNT selected:
  buying ammo reduces risk
```

This is essential for debugging hunt behavior later.

---

# 5. What to add in PR 5

PR 5 should be generic, not hunt-specific, but it must support the lifecycle hunt will need.

## 5.1. Generic pause/resume

Required for:

```text
pause hunt to drink
pause hunt for emission
pause hunt to heal
```

## 5.2. Generic repair

Required for:

```text
target not found
route changed
new target location memory appears
combat readiness changed
```

## 5.3. ActivePlan metadata

ActivePlan should allow:

```text
objective_key
target_id
target_location_id
source_memory_refs
repair_count
paused_parent_plan_id
```

This is not only for hunt, but hunt heavily depends on it.

## 5.4. PlanStep kinds to reserve

Reserve or support:

```text
observe_location_for_target
initiate_combat
confirm_target_dead
ask_for_intel
wait_for_target
```

They do not all need full implementation in PR 5, but the plan system should not make them hard to add.

---

# 6. What should NOT be done before PR 5

Do not implement full hunt operation before PR 5.

Avoid:

```text
- big custom hunt state machine in tick_rules.py;
- special-case if chains for kill_stalker;
- direct attack logic bypassing Objective/ActivePlan;
- target tracking outside MemoryStore;
- target repair outside ActivePlan.
```

Reason:

```text
Without ActivePlan as source of truth, complex hunt logic will become another parallel behavior system.
```

---

# 7. Minimal pre-PR5 acceptance checks

Before starting post-PR5 hunt operation, make sure these are true:

```text
PR 2:
  [ ] weapon/ammo/medicine ItemNeed works.
  [ ] heal_self uses ImmediateNeed/liquidity.
  [ ] liquidity does not sell equipped weapon/armor or last critical ammo/meds.

PR 3:
  [ ] memory_v3 can store/retrieve by entity_id.
  [ ] target_* memory kinds are allowed.
  [ ] memory_used.used_for supports target-related purposes.

PR 4:
  [ ] objective scoring supports blockers and rejected reasons.
  [ ] hunt-related objective keys are reserved.

PR 5:
  [ ] ActivePlan supports pause/resume/repair.
  [ ] runtime bridge supports immediate and long PlanSteps.
  [ ] plan metadata can carry target_id and source_memory_refs.
```

---

# 8. Final recommendation

Do now:

```text
PR 2: make equipment/ammo/medicine needs solid.
PR 3: make target-related memory possible.
PR 4: reserve hunt objective keys and blocker semantics.
PR 5: make ActivePlan lifecycle generic enough.
```

Do after PR 5:

```text
TargetBelief
CombatReadiness
HuntObjective decomposition
Target tracking
Ambush/intercept
Social consequences
```
