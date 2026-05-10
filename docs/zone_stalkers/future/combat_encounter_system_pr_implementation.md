# Combat Encounter System — PR Implementation Documentation

> Target path:
>
> ```text
> docs/zone_stalkers/future/combat_encounter_system.md
> ```
>
> Purpose:
>
> ```text
> Turn the current simple combat flow into a systemic, text-driven, multi-actor combat encounter system.
> ```
>
> This document is implementation-ready PR documentation for Copilot.
>
> It must be implemented with the existing Zone Stalkers architecture in mind:
>
> ```text
> - Brain v3 objective / intent / active_plan pipeline;
> - kill_stalker goal;
> - HuntLead / TargetBelief / Hunt Traces;
> - zone_delta / zone_debug_delta WebSocket sync;
> - debug-map-lite and scoped debug endpoints;
> - CPU optimization direction:
>   DirtyRuntime, Copy-on-Write runtime, event-driven actions, lazy details.
> ```

---

# 0. Current Problem

The current combat model is too shallow:

```text
ENGAGE_TARGET
→ start_combat
→ monitor_combat
→ confirm_kill
```

The target model:

```text
ENGAGE_TARGET
→ create CombatEncounter
→ resolve round-based text combat
→ generate structured narrative events
→ feed results back into Brain v3, hunt memory, TargetBelief, debug UI
```

The combat system should feel like a small text quest, closer to the spirit of Space Rangers 2:

```text
- a situation is described;
- each actor can act;
- actions affect position, morale, wounds, cover, route clues;
- combat generates story and mechanical consequences;
- the hunt system reacts to those consequences.
```

---

# 1. Implementation Strategy

Implement this as three sequential PRs.

```text
PR 7 — Text Combat Encounter MVP
  Round-based encounter model, actions, simple NPC combat policy,
  narrative events, integration with ENGAGE_TARGET / monitor_combat / confirm_kill.

PR 8 — Retreat, Wounds, and Combat Balance
  Persistent wounds, meaningful retreat, opportunity shots,
  anti-endless-flee balancing, hunt leads from combat outcomes.

PR 9 — Advanced Hunt Tactics and Text Quest Layer
  INTERCEPT_TARGET, AMBUSH_TARGET, STAKEOUT_LOCATION,
  text-quest UI hooks, richer narrative arcs.
```

Important:

```text
PR7 must already include performance-safe storage and delta/debug architecture.
Do not implement a heavy combat system that scans all encounters and all actors every tick.
```

---

# 2. Existing System Integration

## 2.1. Brain v3

Combat must integrate with Brain v3 through:

```text
global_goal
current_goal
objective ranking
intent adapter
active_plan_v3
memory_v3
brain_trace / story export
```

Relevant current objectives:

```text
ENGAGE_TARGET
CONFIRM_KILL
TRACK_TARGET
LOCATE_TARGET
VERIFY_LEAD
HUNT_TARGET
RESTORE_HEALTH
FLEE_DANGER
```

New combat-related objectives can be added gradually:

```text
SURVIVE_COMBAT
GAIN_COVER
SUPPRESS_TARGET
RELOAD_WEAPON
HEAL_IN_COMBAT
FLEE_COMBAT
FINISH_TARGET
CAPTURE_TARGET
```

## 2.2. Hunt system

Combat outcomes must feed existing hunt/search mechanics:

```text
target_wounded
target_moved
target_route_observed
target_last_known_location
target_condition_observed
target_death_confirmed
combat_noise_heard
combat_seen_by_witness
target_fled_combat
```

These records must be usable by:

```text
TargetBelief
possible_locations
likely_routes
exhausted_locations
Hunt Traces
debug map
```

## 2.3. Optimization constraints

The feature must follow the current optimization direction:

```text
- no full-state refresh per combat round;
- no full debug payload per round;
- no per-tick scan of every completed combat;
- no large narrative log in normal zone_delta;
- combat details must be lazy/scoped;
- combat state changes must mark dirty agents/locations/state;
- combat must support Copy-on-Write runtime.
```

---

# 3. Storage Model

## 3.1. State fields

Add to Zone state:

```json
{
  "combat_encounters": {},
  "combat_encounter_index": {
    "by_location": {},
    "by_actor": {},
    "active_ids": []
  },
  "combat_runtime": {
    "next_encounter_seq": 1
  }
}
```

## 3.2. Why indexes are required

Do not find active encounters by scanning all encounters every tick.

Required indexes:

```text
combat_encounter_index.active_ids
combat_encounter_index.by_actor[agent_id]
combat_encounter_index.by_location[location_id]
```

This enables:

```text
- ticking active encounters only;
- finding encounter for selected NPC/profile;
- showing combat markers on debug map;
- updating location panel;
- resolving player actions.
```

## 3.3. Encounter ID

Use stable IDs:

```python
encounter_id = f"combat_{world_turn}_{state['combat_runtime']['next_encounter_seq']}"
state["combat_runtime"]["next_encounter_seq"] += 1
```

---

# 4. Core Data Model

Use JSON-compatible dictionaries.

## 4.1. CombatEncounter

```python
CombatEncounter = {
    "id": str,
    "location_id": str,
    "status": "active" | "resolved" | "aborted",
    "phase": "contact" | "positioning" | "exchange" | "maneuver" | "crisis" | "resolution",
    "round_index": int,
    "created_turn": int,
    "updated_turn": int,

    "participants": {
        "<agent_id>": CombatParticipant
    },

    "sides": {
        "hunter": {"actor_ids": [...]},
        "target": {"actor_ids": [...]},
        "neutral": {"actor_ids": [...]}
    },

    "battlefield": CombatBattlefield,

    "round_state": {
        "pending_player_actor_ids": [],
        "submitted_actions": {},
        "last_resolved_turn": int | None
    },

    "narrative_log": [],

    "resolution": None | CombatResolution,

    "source": {
        "objective_key": "ENGAGE_TARGET",
        "active_plan_id": str | None,
        "hunter_id": str | None,
        "target_id": str | None,
        "hunt_target_id": str | None,
        "started_from": "engage_target" | "ambush" | "intercept" | "random_encounter" | "debug"
    }
}
```

## 4.2. CombatParticipant

```python
CombatParticipant = {
    "actor_id": str,
    "side_id": str,
    "role": "attacker" | "target" | "ally" | "bystander" | "guard",

    "status": "active" | "downed" | "dead" | "fled" | "surrendered" | "unconscious",

    "hp_snapshot": int,
    "morale": float,
    "stamina": float,
    "suppression": float,

    "cover": "none" | "light" | "heavy",
    "position": "exposed" | "covered" | "flanking" | "retreating" | "downed",
    "range_to_primary_enemy": "close" | "medium" | "long",

    "weapon_type": str | None,
    "ammo_count": int,
    "ammo_type": str | None,

    "wounds": [],

    "last_action": None | CombatAction,
    "last_action_turn": int | None,

    "combat_memory": {
        "flee_attempts": int,
        "shots_fired": int,
        "hits_landed": int,
        "times_wounded": int
    }
}
```

## 4.3. CombatBattlefield

```python
CombatBattlefield = {
    "range_band": "close" | "medium" | "long",
    "visibility": "clear" | "low" | "smoke" | "night",
    "noise_level": float,
    "anomaly_danger": float,
    "escape_routes": [],
    "cover_by_actor": {},
    "reinforcements_possible": bool,
    "terrain_type": str | None
}
```

## 4.4. CombatNarrativeEvent

```python
CombatNarrativeEvent = {
    "id": str,
    "turn": int,
    "round_index": int,
    "kind": str,
    "actor_id": str | None,
    "target_id": str | None,
    "summary": str,
    "effects": {},
    "importance": "low" | "normal" | "high" | "critical"
}
```

Keep `narrative_log` bounded:

```text
MAX_COMBAT_NARRATIVE_EVENTS = 100 per encounter
```

---

# 5. Backend Files to Add

```text
backend/app/games/zone_stalkers/combat_encounter/
  __init__.py
  model.py
  config.py
  create.py
  actions.py
  policy.py
  resolve.py
  narrative.py
  memory_hooks.py
  debug_projection.py
  delta.py
```

Responsibilities:

```text
model.py
  constants / shape constructors / typed helpers

config.py
  combat balance constants

create.py
  create_combat_encounter(), add/remove indexes

actions.py
  available actions and validation

policy.py
  bot combat policy

resolve.py
  round resolution and outcome application

narrative.py
  structured event → text summary

memory_hooks.py
  combat result → memory_v3 / hunt leads

debug_projection.py
  compact debug views

delta.py
  combat delta / debug delta snippets
```

---

# 6. PR 7 — Text Combat Encounter MVP

## 6.1. Goal

Implement the first playable systemic combat loop:

```text
ENGAGE_TARGET
→ start CombatEncounter
→ one combat round per tick or per player action
→ produce combat narrative
→ resolve killed/fled/draw
→ active_plan continues to CONFIRM_KILL or TRACK_TARGET
```

## 6.2. MVP combat actions

Implement:

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

For PR7, these must have full effects:

```text
shoot
take_cover
flee
reload
observe
use_medkit
```

Other actions can be simple modifiers.

## 6.3. Combat action shape

```python
CombatAction = {
    "actor_id": str,
    "kind": "shoot" | "take_cover" | "flee" | "reload" | "observe" | "use_medkit",
    "target_id": str | None,
    "declared_turn": int,
    "params": {}
}
```

## 6.4. Available actions

```python
def get_available_combat_actions(
    *,
    state: dict,
    encounter: dict,
    actor_id: str,
    world_turn: int,
) -> list[dict]:
    ...
```

Rules:

```text
dead/downed/fled actors have no normal actions;
no ammo → no shoot/suppress;
no medkit → no use_medkit;
no escape route → flee has low chance or is unavailable;
already in heavy cover → take_cover less useful but still possible.
```

## 6.5. NPC combat policy

```python
def choose_npc_combat_action(
    *,
    state: dict,
    encounter: dict,
    actor_id: str,
    world_turn: int,
) -> dict:
    ...
```

Basic policy:

```text
if hp critical and medkit available:
  use_medkit

if hp low or morale low:
  flee or take_cover

if no ammo:
  reload

if exposed:
  take_cover

if hunter with kill_stalker and target visible:
  shoot / aim / suppress

if target of kill_stalker:
  survive first, flee if wounded/outgunned

if bystander:
  flee or take_cover
```

## 6.6. Round resolution

```python
def resolve_combat_round(
    *,
    state: dict,
    encounter: dict,
    submitted_actions: dict[str, dict],
    world_turn: int,
    rng: random.Random,
) -> tuple[dict, list[dict]]:
    ...
```

Resolution steps:

```text
1. Validate actions.
2. Fill missing bot actions through policy.
3. Determine initiative order.
4. Apply actions.
5. Write narrative events.
6. Apply hp/ammo/morale/suppression/stamina changes.
7. Check resolution.
8. Update encounter phase/status.
9. Return changed encounter + events.
```

Initiative MVP:

```text
initiative =
  skill_combat
  + random(0, 10)
  + cover_bonus
  + aim_bonus
  - suppression_penalty
  - wound_penalty
```

Hit chance MVP:

```text
hit_chance =
  base_weapon_accuracy
  + skill_combat * 0.03
  + aim_bonus
  + range_modifier
  + target_exposed_bonus
  - target_cover_penalty
  - suppression_penalty
  - wound_penalty
```

Clamp:

```text
min 0.05
max 0.90
```

Damage MVP:

```text
damage =
  weapon_base_damage
  + random variance
  - cover_reduction
```

For PR7, wounds can be simple:

```text
hp <= 0 → dead/downed depending on config
hp <= 25 → serious condition marker, no persistent wound mechanics yet
```

## 6.7. Integration with active_plan

Update active plan executor:

```text
ENGAGE_TARGET step:
  if no active encounter:
    create_combat_encounter
    step status = running

  if encounter active:
    monitor one round / wait for player actions
    step status = running

  if encounter resolved target killed/downed:
    step status = completed
    next CONFIRM_KILL

  if target fled:
    step status = failed or completed_with_lead
    write target_moved / target_route_observed
    next TRACK_TARGET / LOCATE_TARGET
```

Do not leave hunter stuck in ENGAGE_TARGET after target fled.

## 6.8. Existing combat_interactions compatibility

If current code has `state["combat_interactions"]`, do not abruptly delete it.

Migration strategy:

```text
- New encounters use state["combat_encounters"].
- Old combat_interactions can be migrated lazily or left as legacy fallback.
- Add compatibility wrapper:
  start_combat_interaction(...) → create_combat_encounter(...)
```

## 6.9. Memory / hunt hooks

When combat starts:

```text
combat_initiated
target_seen
target_last_known_location
```

When shot fired:

```text
combat_noise_heard for bystanders/location
```

When target wounded:

```text
target_wounded
target_condition_observed
```

When target fled:

```text
target_moved
target_route_observed
target_last_known_location
target_fled_combat
```

When target killed:

```text
target_death_confirmed
combat_resolved
```

## 6.10. Debug / frontend MVP

Add compact fields:

```text
active_combat_encounter_id
combat_status
combat_phase
combat_round
combat_participants_summary
last_combat_narrative_events
```

Normal `zone_delta` should include compact combat changes only:

```json
{
  "changes": {
    "combat_encounters": {
      "combat_...": {
        "status": "active",
        "phase": "exchange",
        "round_index": 3,
        "participants_summary": {}
      }
    },
    "agents": {}
  }
}
```

Full narrative log is lazy-loaded:

```text
GET /zone-stalkers/contexts/{context_id}/combat-encounters/{encounter_id}
```

## 6.11. PR7 performance requirements

```text
[ ] Do not scan all combat_encounters every tick.
[ ] Tick only combat_encounter_index.active_ids.
[ ] Do not send full narrative_log in normal zone_delta.
[ ] Do not store unbounded narrative_log.
[ ] Mark dirty agents/locations/state when combat changes.
[ ] Use COW helpers where tick mutates state.
[ ] Support debug lazy loading for full encounter details.
```

---

# 7. PR 8 — Retreat, Wounds, and Combat Balance

## 7.1. Goal

Fix endless flee loops and make combat outcomes meaningful.

Problem:

```text
ENGAGE_TARGET
→ combat
→ target_fled
→ TRACK_TARGET
→ ENGAGE_TARGET
→ target_fled
→ repeat forever
```

## 7.2. Persistent wound model

```python
CombatWound = {
    "id": str,
    "kind": "light_wound" | "serious_wound" | "bleeding" | "limping" | "arm_injured" | "concussion" | "downed",
    "created_turn": int,
    "severity": float,
    "expires_turn": int | None,
    "source_encounter_id": str,
    "effects": {}
}
```

Effects:

```text
light_wound:
  minor morale penalty

serious_wound:
  accuracy penalty, flee penalty, morale penalty

bleeding:
  periodic hp loss, creates HEAL_SELF / SEEK_MEDKIT pressure

limping:
  travel speed penalty, flee chance penalty, stronger tracks

arm_injured:
  accuracy/reload penalty

concussion:
  perception/initiative penalty

downed:
  cannot flee normally; can be finished/captured/rescued
```

Store on agent:

```json
agent["wounds_v1"] = []
```

Bound:

```text
MAX_ACTIVE_WOUNDS_PER_AGENT = 10
```

## 7.3. Flee mechanics

Replace cheap flee with score-based escape:

```text
escape_score =
  BASE_FLEE_CHANCE
  + cover_bonus
  + distance_bonus
  + route_knowledge
  + stamina_bonus
  - suppression_penalty
  - limping_penalty
  - bleeding_penalty
  - close_range_penalty
  - attacker_position_bonus
  - repeated_retreat_penalty
```

Outcomes:

```text
successful_escape
partial_escape
failed_escape
opportunity_shot
```

## 7.4. Opportunity shot

When a participant flees while observed:

```text
if attacker has line of sight and ammo:
  attacker may get opportunity shot
```

Modifiers:

```text
aimed last round
target suppressed
target wounded
range
visibility
cover
attacker weapon skill
```

## 7.5. Repeated retreat memory

Track compact per hunter-target pair:

```json
{
  "target_fled_count": 3,
  "last_fled_turn": 1234,
  "last_fled_location_id": "loc_A"
}
```

Effects:

```text
1st retreat:
  normal

2nd retreat:
  route lead confidence higher

3rd retreat:
  direct ENGAGE_TARGET score penalty
  INTERCEPT_TARGET candidate unlocked

4th retreat:
  AMBUSH_TARGET / STAKEOUT_LOCATION score rises
```

## 7.6. Wounded target behavior

Brain v3 effects:

```text
bleeding:
  high HEAL_SELF / SEEK_MEDKIT

limping:
  avoid long routes
  leaves stronger tracks

low morale:
  avoid direct combat

low ammo:
  resupply objective
```

Hunt effects:

```text
hunter can search likely medics/traders/shelters
target_wounded increases confidence of recent leads
target_condition_observed influences route prediction
```

## 7.7. PR8 performance requirements

```text
[ ] Wound effects are computed from active wounds only.
[ ] Do not scan all memories for target_fled_count each tick.
[ ] Cache/maintain flee stats as compact per target/hunter summary.
[ ] Bleeding damage should use scheduled task or interval.
[ ] Wound list is bounded.
```

---

# 8. PR 9 — Advanced Hunt Tactics and Text Quest Layer

## 8.1. New objectives

```text
INTERCEPT_TARGET
AMBUSH_TARGET
STAKEOUT_LOCATION
PURSUE_TARGET
BAIT_TARGET
RECOVER_AFTER_COMBAT
```

## 8.2. INTERCEPT_TARGET

Uses `TargetBelief.likely_routes`.

Condition:

```text
target likely route exists
hunter can reach intercept location before target
target confidence high enough
```

Plan:

```text
travel_to_intercept_location
observe_location
if target arrives → start combat with positioning advantage
if absent → look_for_tracks / target_not_found
```

Performance:

```text
Do not recompute all route intercepts every tick.
Recompute when:
  target_moved
  target_route_observed
  target_seen
  hunter arrived
  map_revision changed
```

## 8.3. AMBUSH_TARGET

Condition:

```text
target likely to pass through location
hunter has weapon/ammo/time
cover available
target is dangerous or repeatedly escaped
```

Plan:

```text
travel_to_ambush_location
prepare_ambush
wait_for_target
start_combat_with_advantage
```

Combat advantage:

```text
attacker cover bonus
initiative bonus
aim bonus
surprise bonus
target morale penalty
```

## 8.4. STAKEOUT_LOCATION

Condition:

```text
target often visits location
recent traces but no direct sighting
hunter has enough supplies
```

Plan:

```text
travel_to_location
observe_location for N turns
question_witnesses if target absent
update belief
```

Stakeout is not idle. It should create structured observation records.

## 8.5. PURSUE_TARGET

Used immediately after target flees:

```text
follow_route
look_for_tracks
if contact → combat
if no contact → update target_not_found / route confidence
```

## 8.6. BAIT_TARGET

Optional late PR9 feature:

```text
create_bait_event
wait/observe
engage_if_target_arrives
```

Bait examples:

```text
fake trader rumor
valuable artifact rumor
money/debt lure
faction message
```

## 8.7. Text quest renderer

Add:

```text
backend/app/games/zone_stalkers/narrative/combat_narrative.py
backend/app/games/zone_stalkers/narrative/hunt_narrative.py
```

Structured events become grouped story blocks.

Example:

```text
Поцик 1 уже дважды упускал Челика 3 у Бункера торговца.
Вместо третьей прямой атаки он занял позицию у дороги к Скоплению аномалий.
Через час Челик 3 появился на тропе, раненый и уставший.
Поцик 1 дождался, пока цель выйдет из укрытия, и открыл вогонь.
```

## 8.8. Player interaction hooks

Frontend should be able to show:

```text
text scene
current participants
available player actions
round log
result
```

Endpoints:

```text
GET /zone-stalkers/contexts/{context_id}/combat-encounters/{encounter_id}
POST /zone-stalkers/contexts/{context_id}/combat-encounters/{encounter_id}/actions
```

For bot-only combat, the system advances automatically.

For player participant:

```text
encounter waits for player action or uses timeout/default defensive action.
```

## 8.9. PR9 performance requirements

```text
[ ] No full narrative regeneration every tick.
[ ] Narrative arcs are built on demand or incrementally.
[ ] Stakeout uses scheduled observation turns if CPU PR3 is available.
[ ] Intercept scoring uses cached TargetBelief / route candidates.
[ ] Debug map receives compact tactic summaries only.
```

---

# 9. Backend API

## 9.1. Encounter detail

```text
GET /zone-stalkers/contexts/{context_id}/combat-encounters/{encounter_id}
```

Returns:

```json
{
  "encounter": {},
  "available_actions": [],
  "narrative_log": []
}
```

Only used when panel is open.

## 9.2. Submit player action

```text
POST /zone-stalkers/contexts/{context_id}/combat-encounters/{encounter_id}/actions
```

Body:

```json
{
  "actor_id": "agent_1",
  "kind": "take_cover",
  "target_id": null,
  "params": {}
}
```

## 9.3. Combat debug endpoint

```text
GET /zone-stalkers/contexts/{context_id}/debug/combat-encounters
```

Filters:

```text
location_id
actor_id
status=active/resolved
limit
```

---

# 10. WebSocket / Delta Integration

## 10.1. Normal zone_delta

Include compact changes:

```json
{
  "changes": {
    "combat_encounters": {
      "combat_123": {
        "status": "active",
        "phase": "exchange",
        "round_index": 2,
        "location_id": "loc_A",
        "participants_summary": {
          "agent_1": {"status": "active", "hp": 80},
          "agent_2": {"status": "fled", "hp": 40}
        },
        "last_event_summary": "Поцик 1 выстрелил по Челику 3, но тот ушёл за бетонную плиту."
      }
    }
  }
}
```

Do not include full narrative log.

## 10.2. Debug delta

For debug subscribers, include:

```text
active combat marker per location
selected encounter summary
last N narrative events
participant statuses
hunt lead emissions from combat
```

Do not send all resolved encounters.

## 10.3. Revision

Combat changes must increment:

```text
state_revision
_debug_revision if debug delta changes
```

---

# 11. Frontend Requirements

## 11.1. Debug map

Show combat markers:

```text
active encounter at location
participants count
phase
danger/noise marker
```

Location profile should show:

```text
Active Combat Encounter
participants
phase
round
last narrative events
button: open full combat details
```

## 11.2. NPC profile

Add panel:

```text
Combat State
  current encounter
  side/role
  hp/morale/suppression/stamina
  cover/position
  wounds
  last combat action
```

## 11.3. Full encounter modal

For selected encounter:

```text
round log
participants
available actions if player actor
resolution
hunt lead outputs
```

Do not load full encounter details unless modal/panel is open.

---

# 12. Optimization and Performance Checklist

This feature must follow these rules from PR7:

```text
[ ] Active encounter IDs are indexed.
[ ] Tick only combat_encounter_index.active_ids.
[ ] No per-tick scan of all resolved encounters.
[ ] No full narrative log in zone_delta.
[ ] Full encounter detail is lazy endpoint only.
[ ] Narrative log is bounded.
[ ] Combat memory writes are compact.
[ ] DirtyRuntime marks changed agents/locations/state.
[ ] Copy-on-Write helpers are used for tick mutations.
[ ] Debug delta is scoped to selected/visible debug state.
[ ] Wound/retreat stats are bounded and indexed.
[ ] Repeated flee stats are compact, not derived by scanning all memory every tick.
[ ] Route/intercept calculations use TargetBelief caches and map_revision-aware path cache.
```

---

# 13. Tests

## 13.1. PR7 tests

Add:

```text
backend/tests/combat_encounter/test_combat_encounter_mvp.py
backend/tests/combat_encounter/test_combat_encounter_active_plan_integration.py
backend/tests/combat_encounter/test_combat_encounter_delta.py
backend/tests/combat_encounter/test_combat_encounter_debug.py
```

Required:

```python
def test_engage_target_creates_combat_encounter():
    ...

def test_combat_round_shoot_can_damage_target():
    ...

def test_combat_round_generates_narrative_event():
    ...

def test_combat_resolution_target_killed_writes_target_death_confirmed():
    ...

def test_target_fled_writes_target_moved_and_route_observed():
    ...

def test_monitor_combat_step_remains_running_while_encounter_active():
    ...

def test_confirm_kill_runs_only_after_resolved_encounter():
    ...

def test_zone_delta_contains_compact_combat_summary_not_full_log():
    ...

def test_combat_encounter_index_tracks_active_ids_by_actor_and_location():
    ...

def test_combat_tick_does_not_scan_resolved_encounters():
    ...
```

## 13.2. PR8 tests

```python
def test_wound_persists_after_combat():
    ...

def test_limping_reduces_escape_chance():
    ...

def test_bleeding_creates_heal_pressure():
    ...

def test_flee_attempt_can_trigger_opportunity_shot():
    ...

def test_repeated_flee_penalizes_direct_engage():
    ...

def test_flee_writes_hunt_leads():
    ...
```

## 13.3. PR9 tests

```python
def test_intercept_target_selected_when_route_confident():
    ...

def test_ambush_starts_combat_with_advantage():
    ...

def test_stakeout_observes_location_and_updates_belief():
    ...

def test_pursue_target_after_flee_uses_route_observed():
    ...

def test_text_quest_renderer_groups_combat_events():
    ...
```

## 13.4. E2E tests

```text
kill_stalker direct combat
kill_stalker target flees then tracked
kill_stalker target wounded then found at safe/trader location
hunter switches from direct engage to intercept after repeated retreats
player participant chooses combat action
```

---

# 14. Acceptance Criteria

## PR7 done when

```text
[ ] ENGAGE_TARGET creates CombatEncounter.
[ ] Combat round resolves actions and writes narrative.
[ ] Bot policy chooses reasonable actions.
[ ] Combat can resolve target killed / target fled / disengaged.
[ ] Active plan continues correctly after combat result.
[ ] Hunt leads are written from combat result.
[ ] zone_delta contains compact combat summary.
[ ] Full encounter details are lazy-loaded.
[ ] Debug map/profile can show active combat.
[ ] No unbounded logs or per-tick full encounter scans.
```

## PR8 done when

```text
[ ] Wounds persist after combat.
[ ] Retreat is probabilistic and consequential.
[ ] Opportunity shots exist.
[ ] Repeated flee changes hunter strategy pressure.
[ ] Wounded target behavior changes.
[ ] Hunt search uses wound/flee leads.
[ ] Balance constants live in config.py.
```

## PR9 done when

```text
[ ] INTERCEPT_TARGET works.
[ ] AMBUSH_TARGET works.
[ ] STAKEOUT_LOCATION works.
[ ] PURSUE_TARGET works.
[ ] Combat can be rendered as text quest.
[ ] Player action hooks exist.
[ ] Debug UI shows tactic reasoning.
```

---

# 15. Do Not Do

```text
1. Do not send full combat logs every tick.
2. Do not scan all historical encounters every tick.
3. Do not make flee always successful.
4. Do not make combat instantly kill every target.
5. Do not bypass Brain v3 / ActivePlan.
6. Do not write hunt results only to narrative; they must become structured memory/hunt leads.
7. Do not make player interaction required for bot-only combat.
8. Do not make optimization an afterthought; indexes and bounded logs are required from PR7.
```

---

# 16. Suggested Implementation Order for PR7

```text
1. Add combat_encounter package and config.
2. Add encounter model helpers and indexes.
3. Add create_combat_encounter().
4. Add available actions and bot policy.
5. Add resolve_combat_round().
6. Add narrative event generation.
7. Add memory/hunt hooks.
8. Integrate ENGAGE_TARGET / monitor_combat / confirm_kill.
9. Add zone_delta compact combat summary.
10. Add debug/profile lazy endpoints.
11. Add tests.
12. Add frontend minimal panels/markers.
```

---

# 17. Final Expected Gameplay

Example:

```text
Поцик 1 наконец выходит на Челика 3 у Старого КПП.
Вместо мгновенного бинарного результата создаётся CombatEncounter.

Раунд 1:
  Поцик 1 занимает укрытие и целится.
  Челик 3 замечает опасность и пытается уйти за бетонный блок.

Раунд 2:
  Поцик 1 стреляет.
  Челик 3 получает лёгкое ранение, подавлен и решает отступать.

Раунд 3:
  Челик 3 пытается сбежать.
  Поцик 1 делает opportunity shot.
  Челик 3 уходит, но оставляет сильный route lead и wound lead.

После боя:
  Brain v3 не повторяет тупо ENGAGE_TARGET.
  TargetBelief обновляется.
  Hunter получает варианты:
    PURSUE_TARGET
    INTERCEPT_TARGET
    AMBUSH_TARGET
    STAKEOUT_LOCATION
```

This is the desired systemic result.
