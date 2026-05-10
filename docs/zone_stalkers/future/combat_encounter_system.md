# Combat Encounter System — Planned Features

This document covers the planned combat encounter system for Zone Stalkers. These features are not yet implemented. The current system uses a simple `start_combat → monitor_combat → confirm_kill` flow; the features below are the next iteration.

The plan is organized as three sequential PRs:

1. **PR 7** — Text Combat Encounter MVP (round-based model, narrative events)
2. **PR 8** — Retreat, Wounds, and Combat Balance (meaningful flee, persistent wounds)
3. **PR 9** — Advanced Hunt Tactics and Text Quest Layer (intercept, ambush, stakeout, narrative)

---

## PR 7 — Text Combat Encounter MVP

### Problem

Current combat is too shallow:

```text
ENGAGE_TARGET
→ start_combat
→ monitor_combat
→ confirm_kill
```

The combat itself is mostly a series of automatic shots and retreats. The target model is a group text encounter where each participant can choose an action each round, and rounds generate narrative events.

---

### CombatEncounter Model

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
    status: str   # active | resolved | aborted
    created_turn: int
    updated_turn: int
    resolution: CombatResolution | None
```

Phases: `contact`, `positioning`, `exchange`, `maneuver`, `crisis`, `resolution`

---

### CombatParticipant

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
    status: str   # active | downed | fled | dead | surrendered | unconscious
```

---

### BattlefieldState

```python
@dataclass
class BattlefieldState:
    range_band: str         # close | medium | long
    visibility: str
    noise_level: float
    cover_by_actor: dict[str, str]   # none | light | heavy
    escape_routes: tuple[str, ...]
    anomaly_danger: float
    weather: str | None
    reinforcements_possible: bool
```

---

### Combat Actions (MVP)

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

Later additions: `flank`, `throw_grenade`, `call_help`, `shout`, `surrender`, `finish_target`

---

### Round Resolution

Each combat round:

1. Build `CombatSituation` for each participant.
2. Generate available actions.
3. Choose action — player input if player-controlled; NPC combat policy if bot.
4. Resolve initiative/reactions.
5. Apply effects.
6. Write narrative events.
7. Check resolution.

Initiative:

```text
initiative = skill_combat + random + cover/position modifiers
```

---

### NPC Combat Policy

```text
backend/app/games/zone_stalkers/combat/policy.py
```

Inputs: actor hp, target hp, ammo, weapon, cover, range, morale, global_goal, current_objective, wounds, suppression, escape routes.

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
- `kill_stalker` hunter: higher KILL_TARGET weight.
- `get_rich` target: higher SURVIVE_COMBAT/FLEE weight if wounded.

---

### Narrative Generation

Each round produces text events:

```text
combat_round
combat_shot
combat_suppression
combat_take_cover
combat_flee_attempt
combat_wound
combat_resolution
```

Stored in memory:

```text
backend/app/games/zone_stalkers/combat/narrative.py
```

---

### Integration with ENGAGE_TARGET

`start_combat` creates a `CombatEncounter` stored in `state["combat_encounters"][encounter_id]`.

`monitor_combat` advances one round per tick. If `encounter.status == "active"`, step stays `running`. If resolved, step completes.

`confirm_kill` only runs after encounter resolution.

---

### Combat Resolution Outcomes

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

For `kill_stalker`:
- `target_killed` or `target_downed + finish_target` → `target_death_confirmed`.
- `target_fled` → `target_moved` / `target_route_observed` leads.
- `target_wounded` → memory record.

---

### Group Combat

The data model supports multiple participants (hunter, target, allies, guards, bystanders). Bystander reactions: `flee`, `take_cover`, `call_help`. Full group AI is not required in PR7; just make the model support it.

---

## PR 8 — Retreat, Wounds, and Combat Balance

### Problem

Current logs can produce an endless cycle:

```text
ENGAGE_TARGET → combat → target_fled → TRACK_TARGET → ENGAGE_TARGET → target_fled
```

The target escapes too easily because flee is cheap and wounds have no lasting consequences.

---

### Wound Model

```text
light_wound     → minor hp loss / morale penalty
serious_wound   → accuracy, flee, morale penalty
bleeding        → periodic hp loss, forces heal objective
limping         → travel speed penalty, flee chance penalty, stronger tracks
arm_injured     → accuracy / reload penalty
concussion      → perception / initiative penalty
downed          → cannot flee normally; can be finished/captured/rescued
```

---

### Damage Resolution

```text
damage
→ hp loss
→ possible wound
→ morale change
→ stamina change
```

Critical thresholds:

```text
hp <= 0: downed or dead depending on damage severity
hp <= 25: serious_wound likely, flee desire increases
```

---

### Retreat as Action

`flee` calculates escape chance:

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

- `successful_escape` — target leaves location, writes `target_moved` + `target_route_observed`.
- `partial_escape` — target changes position/range, leaves route clue.
- `failed_escape` — target remains in combat, morale drops, hunter may get opportunity shot.

---

### Opportunity Shot

When target tries to flee: if attacker has line of sight, ammo, and target leaves cover → attacker may get an opportunity shot.

Modifiers: attacker aimed last round, target suppressed/wounded, range, visibility, cover.

---

### Flee Consequences as Hunt Leads

When target flees, always write:

```text
target_moved
target_route_observed
target_last_known_location
```

If wounded: `target_wounded`. If bleeding/limping: `target_condition_observed`.

These feed directly into `TargetBelief` and the hunt search pipeline.

---

### Repeated Retreat Penalty

```text
1st retreat: normal
2nd retreat: stamina penalty + stronger route lead
3rd retreat: hunter unlocks/interested in INTERCEPT_TARGET
4th retreat: direct ENGAGE_TARGET score drops, AMBUSH_TARGET / INTERCEPT_TARGET score rises
```

This prevents the hunter from blindly repeating `ENGAGE_TARGET`.

---

### Recovery Behavior

A wounded target changes behavior:

```text
bleeding    → seek medkit / trader / safe location
limping     → avoid long routes
low morale  → avoid combat
low ammo    → resupply
```

Creates search hooks (hunter checks medics/traders/safe locations for wounded target).

---

### Balance Constants

```python
BASE_FLEE_CHANCE = 0.45
SUPPRESSION_FLEE_PENALTY = 0.25
LIMPING_FLEE_PENALTY = 0.20
OPPORTUNITY_SHOT_BASE_CHANCE = 0.35
BLEEDING_DAMAGE_PER_TURN = 1
RETREAT_STAMINA_COST = 0.25
MAX_DIRECT_ENGAGE_RETRIES = 3
```

Defined in `backend/app/games/zone_stalkers/combat/config.py`.

---

## PR 9 — Advanced Hunt Tactics and Text Quest Layer

### New Hunt Objectives

**INTERCEPT_TARGET** — Go to where the target is likely to go.

```text
Requires: likely_route, target_intention, route confidence
Plan: travel_to_intercept_location → observe_location → engage_or_update_lead
```

**AMBUSH_TARGET** — Prepare position and wait.

```text
Requires: target likely to pass, hunter has time and weapon, cover available
Plan: travel_to_ambush_location → prepare_ambush → wait_for_target → start_combat_with_advantage
```

**STAKEOUT_LOCATION** — Observe a location for N turns.

```text
Plan: travel_to_location → observe_location for N turns → question_witnesses if target absent
```

**PURSUE_TARGET** — Immediate chase after target fled.

```text
Plan: follow_route → look_for_tracks → engage_if_found
```

**BAIT_TARGET** — Use money/items/rumor to lure target.

```text
Plan: create_bait_event → wait/observe → engage_if_target_arrives
```

---

### Tactical Scoring

```text
if target_fled_count >= 2:
  increase INTERCEPT_TARGET

if target_route_observed confidence high:
  increase INTERCEPT_TARGET

if target regularly visits trader/safe location:
  increase AMBUSH_TARGET / STAKEOUT_LOCATION

if hunter low hp:
  decrease direct ENGAGE_TARGET
  increase AMBUSH_TARGET / RECOVER_AFTER_COMBAT

if target wounded:
  increase intercept at medic/trader/safe location
```

---

### Ambush Advantage

`AMBUSH_TARGET` modifies the `CombatEncounter` starting state:

```text
attacker starts with:
  better cover
  higher initiative
  aim bonus
  surprise bonus
  target morale penalty
```

---

### Intercept Mechanics

Uses route hypotheses from the hunt search system:

```text
target likely route: loc_A → loc_B → loc_C
hunter can reach loc_B faster than target
→ INTERCEPT_TARGET loc_B
```

If target arrives: `start_combat`. If not: `target_not_found` → `look_for_tracks` → update route confidence.

---

### Stakeout Mechanics

Stakeout is planned observation, not idle:

```text
STAKEOUT_LOCATION: observe_location N turns
During observation:
  if target arrives → target_seen → start_combat or ambush
  if witnesses arrive → target_intel
  if no result → location confidence decays
```

---

### Text Quest Narrative Layer

A narrative renderer converts structured events into readable story blocks:

```text
backend/app/games/zone_stalkers/narrative/hunt_narrative.py
backend/app/games/zone_stalkers/narrative/combat_narrative.py
```

Example output:

```text
Поцик 1 трижды упустив Челика 3 у Бункера торговца, вирішив не лізти в пряму перестрілку
і зайняв позицію біля дороги до Скупчення аномалій. Через годину Челик 3 з'явився на
стежці, поранений та втомлений. Поцик 1 дав йому підійти ближче і відкрив вогонь з укриття.
```

---

### Player-Interactive Support

The design allows a player-controlled participant:

```text
CombatEncounter returns available actions
Frontend displays text scene + options
Player chooses action
Encounter advances
```

For bot participants, NPC policy chooses automatically. This enables future gameplay similar to Space Rangers 2 text quests.

---

### Group Interaction Hooks

```text
bystander_flees
ally_joins
guard_intervenes
trader_hides
faction_member_supports
```

Rules: same faction may help, neutral may flee, trader may call guards, loner avoids involvement.

---

### Story Arc Export

Compact history groups repeated events into story arcs:

```json
{
  "story_arcs": [
    {
      "kind": "hunt",
      "target_id": "...",
      "start_turn": 1000,
      "end_turn": 1300,
      "summary": "...",
      "key_events": []
    }
  ]
}
```
