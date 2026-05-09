# PR 6 — Hunt Leads and Target Search System

> Goal: replace primitive `last_known_location → search_target` behavior with a proper target-search system based on leads, confidence, stale information and route hypotheses.
>
> This PR starts after NPC Brain v3 / PR5 is closed.
>
> This is not a Brain v3 runtime rewrite. It is a gameplay-mechanics layer consumed by Brain v3.

---

# 1. Problem

Current hunt search is too simple:

```text
TargetBelief.last_known_location_id
→ TRACK_TARGET
→ travel_to_location
→ search_target
```

If the target is not there, the NPC can repeatedly do:

```text
TRACK_TARGET
→ search_target
→ target_not_found
→ TRACK_TARGET
→ search_target
```

This creates a new loop after the old `LOCATE_TARGET → ask_for_intel` bug was fixed.

The root issue:

```text
The game has no real search/investigation mechanic.
```

The hunter only has "last known location", not a set of competing leads.

---

# 2. Target design

Search should be based on **leads**.

A lead is a piece of information about where the target may be, where it moved, what it did, or what it is likely to do next.

Examples:

```text
target_seen
target_last_known_location
target_intel
target_not_found
target_moved
target_route_observed
target_wounded
target_resupplied
target_healed
target_resting
target_combat_noise
target_trader_rumor
target_campfire_rumor
```

The hunter should build a probabilistic belief:

```text
possible location A — confidence 0.62, source: target_seen
possible location B — confidence 0.38, source: target_moved
possible route A→B — confidence 0.44, source: retreat_observed
```

Then objectives should be generated from this belief:

```text
VERIFY_LEAD
TRACK_TARGET
INTERCEPT_TARGET
STAKEOUT_LOCATION
GATHER_INTEL
```

---

# 3. New model: HuntLead

Add:

```text
backend/app/games/zone_stalkers/decision/models/hunt_lead.py
```

Suggested model:

```python
@dataclass(frozen=True)
class HuntLead:
    id: str
    target_id: str
    kind: str
    location_id: str | None
    route_from_id: str | None
    route_to_id: str | None
    created_turn: int
    observed_turn: int | None
    confidence: float
    freshness: float
    source: str
    source_ref: str | None
    source_agent_id: str | None
    expires_turn: int | None
    details: Mapping[str, Any]
```

Lead kinds:

```text
target_seen
target_last_known_location
target_intel
target_not_found
target_moved
target_route_observed
target_wounded
target_healed
target_resupplied
target_resting
target_combat_noise
target_trader_rumor
```

---

# 4. New model: LocationHypothesis

Add:

```text
backend/app/games/zone_stalkers/decision/models/target_belief.py
```

Extend existing `TargetBelief`:

```python
@dataclass(frozen=True)
class LocationHypothesis:
    location_id: str
    probability: float
    confidence: float
    freshness: float
    reason: str
    source_refs: tuple[str, ...]
```

TargetBelief should include:

```python
possible_locations: tuple[LocationHypothesis, ...]
likely_routes: tuple[RouteHypothesis, ...]
best_location_id: str | None
best_location_confidence: float
```

Keep backwards-compatible fields:

```python
last_known_location_id
location_confidence
```

but compute them from the best hypothesis.

---

# 5. Build leads from memory_v3

Update:

```text
backend/app/games/zone_stalkers/decision/target_beliefs.py
```

`build_target_belief()` should:

```text
1. read memory_v3 records for target_id;
2. convert relevant records into HuntLead;
3. decay old leads;
4. suppress invalidated leads;
5. produce possible_locations;
6. choose best_location_id.
```

## Confidence rules

Suggested starting values:

```text
target_seen                   0.95
target_last_known_location     0.85
target_intel from trader       0.70
target_intel from stalker      0.55
target_moved                   0.80
target_route_observed          0.65
target_not_found              -0.75 for that location
combat_noise                   0.35
```

Freshness decay:

```text
freshness = max(0.0, 1.0 - age_turns / decay_window)
```

Different lead kinds can have different decay windows:

```text
target_seen              600 turns
trader intel             900 turns
route observed           300 turns
combat noise             120 turns
target_not_found         1200 turns as negative evidence
```

---

# 6. target_not_found must invalidate locations

When search_target fails:

```text
target_not_found
```

should reduce confidence of that location.

Rules:

```text
1 failed search:
  lower confidence strongly

2 failed searches at same location:
  mark lead stale

3 failed searches at same location:
  cooldown location for this target
```

Add memory:

```text
target_location_exhausted
```

or add details to `target_not_found`:

```json
{
  "target_id": "...",
  "location_id": "...",
  "failed_search_count": 3,
  "cooldown_until_turn": 12345
}
```

---

# 7. New search actions

Add or formalize PlanStep kinds:

```text
STEP_VERIFY_LEAD
STEP_LOOK_FOR_TRACKS
STEP_QUESTION_WITNESSES
STEP_OBSERVE_LOCATION
STEP_CHECK_TRADER_LOGS
```

MVP can start with:

```text
search_target
look_for_tracks
question_witnesses
```

## search_target

Searches current location for target.

Outcomes:

```text
target_seen
target_not_found
```

## look_for_tracks

Searches for movement clues.

Outcomes:

```text
target_route_observed
target_moved
no_tracks_found
```

## question_witnesses

Asks NPCs in current location.

Outcomes:

```text
target_intel
target_last_known_location
target_rumor_unreliable
no_witnesses
```

---

# 8. Objective generation changes

Update hunt objective generation.

## If no useful leads

Generate:

```text
GATHER_INTEL
LOCATE_TARGET
```

## If one or more possible_locations exist

Generate:

```text
VERIFY_LEAD
TRACK_TARGET
```

for the best location.

## If a route is likely

Generate:

```text
INTERCEPT_TARGET
```

but only as placeholder unless PR9 tactics are implemented.

## If current best location is exhausted

Do not choose it again until cooldown expires.

Generate:

```text
GATHER_INTEL
VERIFY_LEAD next best location
LOOK_FOR_TRACKS
```

---

# 9. ActivePlan composition

Examples:

## VERIFY_LEAD

```text
travel_to_location
search_target
look_for_tracks
question_witnesses
```

## TRACK_TARGET

```text
travel_to_location
search_target
```

If already at the location:

```text
search_target
look_for_tracks
```

## GATHER_INTEL

```text
travel_to_intel_source
ask_for_intel
```

or:

```text
question_witnesses
```

---

# 10. Debug/export additions

Add to NPC profile / compact export:

```json
"hunt_search": {
  "target_id": "...",
  "best_location_id": "...",
  "best_location_confidence": 0.74,
  "possible_locations": [
    {
      "location_id": "loc_A",
      "probability": 0.62,
      "reason": "target_seen",
      "freshness": 0.85
    }
  ],
  "exhausted_locations": ["loc_B"],
  "lead_count": 8
}
```

The debug UI should show:

```text
Target belief:
  best lead
  confidence
  stale leads
  exhausted locations
  why this location was chosen
```

---

# 11. Required tests

Add:

```text
backend/tests/decision/v3/test_hunt_leads.py
```

Tests:

```python
def test_target_seen_creates_high_confidence_location_hypothesis():
    ...

def test_trader_intel_creates_medium_confidence_location_hypothesis():
    ...

def test_target_not_found_suppresses_old_location():
    ...

def test_repeated_target_not_found_exhausts_location():
    ...

def test_target_moved_updates_best_location():
    ...

def test_track_target_uses_best_non_exhausted_location():
    ...

def test_no_leads_generates_gather_intel():
    ...
```

E2E regression:

```python
def test_hunter_does_not_repeat_search_target_same_empty_location_forever():
    ...
```

---

# 12. Acceptance criteria

PR 6 is complete when:

```text
[ ] TargetBelief has possible_locations, not just one last_known_location_id.
[ ] target_not_found lowers confidence for that location.
[ ] repeated target_not_found applies cooldown/exhaustion.
[ ] target_moved updates the best hypothesis.
[ ] TRACK_TARGET chooses the best non-exhausted location.
[ ] Search actions produce useful hunt leads.
[ ] Debug/export shows hunt_search / possible leads.
[ ] E2E proves hunter does not repeat the same empty location forever.
```

---

# 13. Out of scope

Do not implement full combat encounter here.

Out of scope:

```text
round-based combat
ambush
intercept
opportunity shots
wound system
group combat
```

Those belong to PR7–PR9.
