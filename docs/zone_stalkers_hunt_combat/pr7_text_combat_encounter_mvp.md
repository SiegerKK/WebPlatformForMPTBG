# PR 7 — Text Combat Encounter MVP

> Goal: replace primitive one-shot combat flow with a round-based text encounter system.
>
> This PR creates the foundation for combat as a multi-interactive text quest.

---

# 1. Problem

Current combat is too shallow:

```text
ENGAGE_TARGET
→ start_combat
→ monitor_combat
→ confirm_kill
```

The combat itself is mostly a series of automatic shots and retreats.

The desired design:

```text
Combat is a group text encounter.
Each participant can choose an action each round.
Actions change the battlefield state.
The encounter generates narrative events.
```

---

# 2. New model: CombatEncounter

Add:

```text
backend/app/games/zone_stalkers/combat/encounter.py
```

Suggested structure:

```python
@dataclass
class CombatEncounter:
    id: str
    location_id: str
    participants: dict[str, CombatParticipant]
    phase: str
    round_index: int
    battlefield: BattlefieldState
    narrative_log: list[CombatNarrativeEvent]
    status: str
    created_turn: int
    updated_turn: int
    resolution: CombatResolution | None
```

Statuses:

```text
active
resolved
aborted
```

Phases:

```text
contact
positioning
exchange
maneuver
crisis
resolution
```

---

# 3. New model: CombatParticipant

```python
@dataclass
class CombatParticipant:
    actor_id: str
    side_id: str
    role: str
    hp: int
    morale: float
    stamina: float
    suppression: float
    cover: str
    position: str
    weapon_type: str | None
    ammo_type: str | None
    ammo_count: int
    wounds: list[CombatWound]
    status: str
```

Statuses:

```text
active
downed
fled
dead
surrendered
unconscious
```

---

# 4. New model: BattlefieldState

```python
@dataclass
class BattlefieldState:
    range_band: str
    visibility: str
    noise_level: float
    cover_by_actor: dict[str, str]
    escape_routes: tuple[str, ...]
    anomaly_danger: float
    weather: str | None
    reinforcements_possible: bool
```

Range bands:

```text
close
medium
long
```

Cover:

```text
none
light
heavy
```

---

# 5. Combat actions

MVP actions:

```text
observe
take_cover
aim
shoot
suppress
reload
advance
fall_back
flee
use_medkit
```

Add later:

```text
flank
throw_grenade
call_help
shout
surrender
finish_target
```

Each action should have:

```python
CombatAction {
  actor_id
  kind
  target_id
  payload
}
```

---

# 6. Round resolution

Each combat round:

```text
1. Build CombatSituation for each participant.
2. Generate available actions.
3. Choose action:
   - player input if player-controlled;
   - NPC combat policy / Brain v3 if bot.
4. Resolve initiative/reactions.
5. Apply effects.
6. Write narrative events.
7. Check resolution.
```

MVP can resolve actors sequentially by initiative:

```text
initiative = skill_combat + random + cover/position modifiers
```

---

# 7. NPC combat decision

For MVP, create a combat policy:

```text
backend/app/games/zone_stalkers/combat/policy.py
```

Inputs:

```text
actor hp
target hp
ammo
weapon
cover
range
morale
global goal
current objective
wounds
suppression
escape routes
```

Combat objectives:

```text
KILL_TARGET
SURVIVE_COMBAT
GAIN_COVER
SUPPRESS_TARGET
SHOOT_TARGET
RELOAD
FLEE_COMBAT
HEAL_IN_COMBAT
```

Examples:

```text
kill_stalker hunter:
  higher KILL_TARGET weight

get_rich target:
  higher SURVIVE_COMBAT/FLEE weight if wounded
```

---

# 8. Narrative generation

Each round should produce text events.

Examples:

```text
Поцик 1 прижал Челика 3 огнём из АК-74.
Челик 3 нырнул за бетонную плиту.
Дробь ударила по корпусу старого автобуса.
Челик 3 попытался отступить к дороге.
```

Add:

```text
backend/app/games/zone_stalkers/combat/narrative.py
```

Narrative events should be saved into memory:

```text
combat_round
combat_shot
combat_suppression
combat_take_cover
combat_flee_attempt
combat_wound
combat_resolution
```

---

# 9. Integration with ActivePlan

`ENGAGE_TARGET` should create:

```text
ActivePlan ENGAGE_TARGET:
  1. start_combat
  2. monitor_combat
  3. confirm_kill
```

Changes:

## start_combat

Creates CombatEncounter:

```text
state["combat_encounters"][encounter_id]
```

Stores encounter id in scheduled_action / active plan payload.

## monitor_combat

Each tick/round:

```text
advance encounter by one round
if encounter active:
  keep step running
if resolved:
  complete monitor step
```

## confirm_kill

Only runs after encounter resolution.

---

# 10. Combat resolution

Possible outcomes:

```text
target_killed
target_downed
target_fled
hunter_fled
both_disengaged
target_surrendered
hunter_out_of_ammo
third_party_intervened
```

For kill_stalker:

```text
target_killed or target_downed + finish_target
→ target_death_confirmed
```

Other outcomes should create leads:

```text
target_fled → target_moved / target_route_observed
target_wounded → target_wounded
hunter_wounded → recover objective
```

---

# 11. Group combat

MVP should support multiple participants structurally, even if most tests use 1v1.

Possible participants:

```text
hunter
target
allies
guards
bystanders
```

Bystander reactions can be simple:

```text
flee
take_cover
call_help
```

Do not overbuild group AI in PR7; just make the data model support it.

---

# 12. Tests

Add:

```text
backend/tests/combat/test_combat_encounter_mvp.py
```

Tests:

```python
def test_start_combat_creates_encounter():
    ...

def test_monitor_combat_advances_rounds():
    ...

def test_combat_action_shoot_can_damage_target():
    ...

def test_take_cover_reduces_hit_chance_or_damage():
    ...

def test_flee_is_an_action_not_free_exit():
    ...

def test_confirm_kill_cannot_run_before_encounter_resolved():
    ...

def test_resolved_target_dead_writes_target_death_confirmed():
    ...
```

E2E:

```python
def test_e2e_hunt_uses_combat_encounter_to_kill_target():
    ...
```

---

# 13. Acceptance criteria

PR 7 is complete when:

```text
[ ] CombatEncounter exists.
[ ] monitor_combat advances a round-based encounter.
[ ] Each participant has available combat actions.
[ ] NPCs choose combat actions from situation.
[ ] Combat generates narrative memory events.
[ ] Flee is an action with chance/cost, not a free automatic escape.
[ ] confirm_kill only happens after encounter resolution.
[ ] 1v1 kill_stalker E2E passes through CombatEncounter.
```

---

# 14. Out of scope

Out of scope for PR7:

```text
advanced ambush/intercept tactics
deep wound system
full faction intervention
complex text UI for player choices
large group battle balancing
```

Those belong to later PRs.
