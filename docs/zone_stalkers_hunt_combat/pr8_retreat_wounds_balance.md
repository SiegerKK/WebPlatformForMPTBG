# PR 8 — Retreat, Wounds and Combat Balance

> Goal: fix the "forever fleeing enemy" problem by making retreat a costly combat action and making damage produce persistent consequences.

---

# 1. Problem

Current logs show:

```text
hunter engages
target gets shot
target flees
hunter confirms target is alive
hunt_failed
repeat
```

This creates an endless loop:

```text
ENGAGE_TARGET
→ combat
→ target_fled
→ TRACK_TARGET
→ ENGAGE_TARGET
→ target_fled
```

The target can survive many encounters because fleeing is too cheap and wounds are not meaningful enough.

---

# 2. Design goal

Retreat should be valid but not free.

A fleeing actor should:

```text
risk opportunity fire
lose stamina
leave tracks
possibly become wounded/bleeding
become more predictable
create target_moved / route_observed leads
```

Repeated retreats should make the target easier to catch.

---

# 3. New wound model

Add:

```text
backend/app/games/zone_stalkers/combat/wounds.py
```

Wound types:

```text
light_wound
serious_wound
bleeding
limping
arm_injured
concussion
downed
```

Effects:

```text
light_wound:
  minor hp loss / morale penalty

serious_wound:
  accuracy penalty
  flee penalty
  morale penalty

bleeding:
  periodic hp loss
  forces heal objective

limping:
  travel speed penalty
  flee chance penalty
  stronger tracks

arm_injured:
  accuracy / reload penalty

concussion:
  perception / initiative penalty

downed:
  cannot flee normally
  can be finished/captured/rescued
```

---

# 4. Damage resolution

Replace pure HP-only outcomes with:

```text
damage
→ hp loss
→ possible wound
→ morale change
→ stamina change
```

Example:

```text
shotgun hit at medium range:
  hp -18
  35% light_wound
  15% bleeding
```

Critical thresholds:

```text
hp <= 0:
  downed or dead depending on damage severity

hp <= 25:
  serious_wound likely
  flee desire increases
  combat effectiveness drops
```

---

# 5. Retreat as action

`flee` should calculate chance:

```text
escape_score =
  base_escape
  + cover_bonus
  + distance_bonus
  + route_knowledge
  + speed
  - suppression
  - limping
  - bleeding
  - close_range_penalty
  - hunter_position_bonus
```

Outcomes:

```text
successful_escape
partial_escape
failed_escape
downed_while_fleeing
```

## successful_escape

```text
target leaves current location
target_moved memory
target_route_observed memory
stamina loss
```

## partial_escape

```text
target changes position/range
may still be in encounter
leaves route clue
```

## failed_escape

```text
target remains in combat
morale drops
hunter may get opportunity shot
```

---

# 6. Opportunity shot

When a target tries to flee:

```text
if attacker has line of sight
and ammo
and target leaves cover
then attacker may get opportunity shot
```

Modifiers:

```text
attacker aimed last round
target suppressed
target wounded
range close/medium
visibility good
cover none/light
```

Memory:

```text
combat_opportunity_shot
target_wounded
target_moved if still escaped
```

---

# 7. Flee consequences as hunt leads

When target flees, always write useful leads:

```text
target_moved
target_route_observed
target_last_known_location
```

If wounded:

```text
target_wounded
```

If bleeding/limping:

```text
target_condition_observed
```

These should feed PR6 TargetBelief.

Example:

```json
{
  "action_kind": "target_moved",
  "target_id": "agent_debug_2",
  "from_location_id": "loc_debug_61",
  "to_location_id": "loc_debug_65",
  "confidence": 0.9,
  "reason": "fled_combat"
}
```

---

# 8. Repeated retreat penalty

Track per target/hunter pair:

```text
recent_retreat_count
recent_hunt_failed_count
```

Effects:

```text
1st retreat:
  normal

2nd retreat:
  stamina penalty
  stronger route lead

3rd retreat:
  hunter unlocks/interested in INTERCEPT_TARGET

4th retreat:
  direct ENGAGE_TARGET score drops
  AMBUSH_TARGET / INTERCEPT_TARGET score rises
```

This prevents blind repeated `ENGAGE_TARGET`.

---

# 9. Recovery behavior

A wounded target should change behavior:

```text
bleeding → seek medkit / trader / safe location
limping → avoid long routes
low morale → avoid combat
low ammo → resupply
```

This creates search hooks:

```text
target_wounded
→ hunter checks medics/traders/safe locations
```

---

# 10. Balance parameters

Add constants:

```python
BASE_FLEE_CHANCE = 0.45
SUPPRESSION_FLEE_PENALTY = 0.25
LIMPING_FLEE_PENALTY = 0.20
OPPORTUNITY_SHOT_BASE_CHANCE = 0.35
BLEEDING_DAMAGE_PER_TURN = 1
RETREAT_STAMINA_COST = 0.25
MAX_DIRECT_ENGAGE_RETRIES = 3
```

Expose in one config file:

```text
backend/app/games/zone_stalkers/combat/config.py
```

---

# 11. Tests

Add:

```text
backend/tests/combat/test_retreat_and_wounds.py
```

Tests:

```python
def test_flee_can_fail_when_suppressed_and_wounded():
    ...

def test_successful_flee_writes_target_moved_and_route_observed():
    ...

def test_opportunity_shot_can_trigger_on_flee():
    ...

def test_repeated_retreat_increases_intercept_objective_weight():
    ...

def test_wounded_target_seeks_recovery():
    ...

def test_bleeding_creates_urgent_heal_need():
    ...
```

E2E:

```python
def test_e2e_hunt_target_flees_then_hunter_tracks_new_location():
    ...

def test_e2e_repeated_flee_switches_to_intercept_or_ambush():
    ...
```

---

# 12. Acceptance criteria

PR 8 is complete when:

```text
[ ] Flee is resolved as a combat action with chance/cost.
[ ] Opportunity shot exists.
[ ] Flee writes target_moved / target_route_observed.
[ ] Wounds persist after combat.
[ ] Wounds affect accuracy, flee, travel or needs.
[ ] Repeated flee changes hunter strategy.
[ ] Target cannot escape forever without consequences.
[ ] E2E proves hunter makes progress after target flees.
```

---

# 13. Out of scope

Out of scope:

```text
full ambush system
advanced faction reinforcement
player-facing combat UI
large-scale battle simulation
```
