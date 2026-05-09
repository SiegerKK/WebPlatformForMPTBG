# PR 9 — Advanced Hunt Tactics and Text Quest Layer

> Goal: add higher-level hunt tactics and make combat/search encounters readable as emergent text quests.

---

# 1. Problem

After PR6–PR8, the hunter should be able to track and fight better.

But the hunt can still feel direct:

```text
track
engage
track
engage
```

To make it feel like a story, NPCs need tactical alternatives:

```text
intercept
ambush
stakeout
bait
pursue
call allies
```

And the system should generate readable text quest-style summaries.

---

# 2. New hunt objectives

Add/formalize:

```text
INTERCEPT_TARGET
AMBUSH_TARGET
STAKEOUT_LOCATION
PURSUE_TARGET
BAIT_TARGET
CALL_HUNT_ALLIES
```

## INTERCEPT_TARGET

Go to where the target is likely to go.

Requires:

```text
likely_route
target_intention
route confidence
```

Plan:

```text
travel_to_intercept_location
observe_location
engage_or_update_lead
```

## AMBUSH_TARGET

Prepare position and wait.

Requires:

```text
target likely to pass location
hunter has time
hunter has weapon
cover available
```

Plan:

```text
travel_to_ambush_location
prepare_ambush
wait_for_target
start_combat_with_advantage
```

## STAKEOUT_LOCATION

Observe a location for some time.

Plan:

```text
travel_to_location
observe_location for N turns
question_witnesses if target absent
```

## PURSUE_TARGET

Immediate chase after target fled.

Plan:

```text
follow_route
look_for_tracks
engage_if_found
```

## BAIT_TARGET

Use money/items/rumor to lure target.

Plan:

```text
create_bait_event
wait/observe
engage_if_target_arrives
```

---

# 3. Tactical scoring

Tactics should be chosen based on repeated failures and target behavior.

Examples:

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

# 4. Ambush advantage

`AMBUSH_TARGET` should influence combat encounter:

```text
attacker starts with:
  better cover
  higher initiative
  aim bonus
  surprise bonus
  target morale penalty
```

Do not make ambush an instant kill.

It should modify CombatEncounter starting state.

---

# 5. Intercept mechanics

Use route hypotheses from PR6.

```text
target likely route: loc_A → loc_B → loc_C
hunter can reach loc_B faster than target
```

Then:

```text
INTERCEPT_TARGET loc_B
```

If target arrives:

```text
start_combat
```

If not:

```text
target_not_found
look_for_tracks
update route confidence
```

---

# 6. Stakeout mechanics

Stakeout is a planned wait, not idle.

```text
STAKEOUT_LOCATION:
  observe_location N turns
```

During observation:

```text
if target arrives:
  target_seen
  start_combat or ambush
if witnesses arrive:
  target_intel
if no result:
  location confidence decays
```

---

# 7. Text quest narrative layer

Add a narrative renderer:

```text
backend/app/games/zone_stalkers/narrative/hunt_narrative.py
backend/app/games/zone_stalkers/narrative/combat_narrative.py
```

Purpose:

```text
Convert structured events into readable story blocks.
```

Example:

```text
Поцик 1 трижды упустил Челика 3 у Бункера торговца.
Он решил не лезть в прямую перестрелку и занял позицию у дороги к Скоплению аномалий.
Через час Челик 3 появился на тропе, раненный и уставший.
Поцик 1 дал ему подойти ближе и открыл огонь из укрытия.
```

---

# 8. Player-interactive text quest support

Even if current actors are bots, design should allow a player participant.

For a player-controlled participant:

```text
CombatEncounter returns available actions
Frontend displays text scene + options
Player chooses action
Encounter advances
```

For bot participants:

```text
NPC policy chooses automatically
```

This allows future gameplay like Space Rangers 2 text quests.

---

# 9. Group interaction

Add simple group hooks:

```text
bystander_flees
ally_joins
guard_intervenes
trader_hides
faction_member_supports
```

Rules:

```text
same faction may help
neutral may flee
trader may call guards
loner may avoid involvement
```

Keep this simple in PR9.

---

# 10. Debug/export

Compact history should group repeated events into story arcs:

```text
Hunt arc:
  gathered intel
  checked old location
  found tracks
  intercepted target
  ambush started
  combat resolved
```

Add export section:

```json
"story_arcs": [
  {
    "kind": "hunt",
    "target_id": "...",
    "start_turn": 1000,
    "end_turn": 1300,
    "summary": "...",
    "key_events": [...]
  }
]
```

---

# 11. Tests

Add:

```text
backend/tests/decision/v3/test_advanced_hunt_tactics.py
backend/tests/narrative/test_hunt_combat_narrative.py
```

Tests:

```python
def test_repeated_target_flee_increases_intercept_score():
    ...

def test_route_hypothesis_generates_intercept_target():
    ...

def test_ambush_target_starts_combat_with_surprise_bonus():
    ...

def test_stakeout_detects_target_arrival():
    ...

def test_hunt_narrative_groups_repeated_events():
    ...

def test_player_participant_receives_available_combat_actions():
    ...
```

E2E:

```python
def test_e2e_repeated_failed_engage_switches_to_ambush_and_kills_target():
    ...

def test_e2e_intercept_target_after_route_observed():
    ...
```

---

# 12. Acceptance criteria

PR9 is complete when:

```text
[ ] Repeated direct failures can switch hunter to intercept/ambush/stakeout.
[ ] Route hypotheses can produce INTERCEPT_TARGET.
[ ] Ambush modifies combat starting state.
[ ] Stakeout can observe target arrival.
[ ] Hunt/combat narrative can summarize arcs.
[ ] Player-controlled participant can receive text quest choices.
[ ] Bot-controlled participants still act automatically.
[ ] Compact history is readable as story, not raw event spam.
```

---

# 13. Out of scope

Out of scope:

```text
full frontend player UI polish
large-scale faction wars
procedural dialogue system
complex quest authoring language
```

This PR creates the foundation.
