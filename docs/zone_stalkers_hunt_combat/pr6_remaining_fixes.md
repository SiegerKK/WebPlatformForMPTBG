# PR 6 — Remaining Fixes Before Closing: Hunt Leads and Target Search

> Branch: `copilot/implement-pr-6-new-mechanics`
> Base: `copilot/implement-pr-5-for-npc-brain-v3`
>
> Goal of PR6:
>
> ```text
> Replace primitive target search:
>   last_known_location → search_target → repeat
>
> with:
>   HuntLead
>   → possible_locations / route hypotheses
>   → confidence decay
>   → target_not_found suppression
>   → exhausted location cooldown
>   → better hunt objective selection
> ```
>
> Current status:
>
> ```text
> PR6 is a good first implementation, but it is not ready to close yet.
> ```

---

# 1. What is already implemented correctly

## 1.1. HuntLead model exists

Added:

```text
backend/app/games/zone_stalkers/decision/models/hunt_lead.py
```

The model has the expected shape:

```text
id
target_id
kind
location_id
route_from_id
route_to_id
created_turn
observed_turn
confidence
freshness
source
source_ref
source_agent_id
expires_turn
details
```

This is the correct foundation for PR6.

## 1.2. TargetBelief has PR6 fields

`TargetBelief` now includes:

```text
best_location_id
best_location_confidence
possible_locations
likely_routes
exhausted_locations
lead_count
```

and keeps backwards-compatible fields:

```text
last_known_location_id
location_confidence
```

Good.

## 1.3. build_target_belief() now aggregates leads

`target_beliefs.py` now:

```text
- reads memory_v3 records;
- canonicalizes intel_from_trader / intel_from_stalker → target_intel;
- converts relevant records into HuntLead;
- applies freshness decay;
- aggregates location hypotheses;
- builds route hypotheses from target_moved / target_route_observed;
- suppresses locations with target_not_found;
- computes exhausted_locations after repeated failed searches;
- avoids omniscient target location unless visible/co-located or debug flag is enabled.
```

This is the core of PR6 and it is mostly correct.

## 1.4. New search step kinds exist

`PlanStep` now includes:

```text
look_for_tracks
question_witnesses
```

alongside the existing:

```text
ask_for_intel
search_target
```

Good.

## 1.5. Search executors exist

`executors.py` now has:

```text
_exec_look_for_tracks
_exec_question_witnesses
_exec_search_target
```

`search_target` now writes `failed_search_count` and `cooldown_until_turn` into `target_not_found`, which is the right direction.

## 1.6. ActivePlan composer understands hunt search

`active_plan_composer.py` now composes strategic hunt plans:

```text
GATHER_INTEL / LOCATE_TARGET:
  travel_to_location
  question_witnesses

VERIFY_LEAD:
  travel_to_location
  search_target
  look_for_tracks
  question_witnesses

TRACK_TARGET:
  travel_to_location
  search_target
  look_for_tracks
```

This is a strong improvement over repeated one-step `search_target`.

## 1.7. PR6 tests were added

Added:

```text
backend/tests/decision/v3/test_hunt_leads.py
```

Current tests cover:

```text
target_seen → high confidence hypothesis
target_not_found suppresses location
repeated target_not_found exhausts location
target_moved updates best location
track target uses best non-exhausted location
no leads generates gather intel
```

Good start.

---

# 2. Main blocker A — look_for_tracks is currently omniscient

## Problem

`_exec_look_for_tracks()` currently reads:

```python
target = state["agents"][target_id]
target_loc = target["location_id"]
```

and if the target is alive and not in the current location, it writes:

```text
target_route_observed
target_moved
to_location_id = target.location_id
```

This means `look_for_tracks` can reveal the exact current target location from world state.

That is too strong for PR6.

It creates a hidden omniscience path:

```text
hunter checks tracks
→ instantly learns exact current target location
```

even if:

```text
target is far away
hunter has no fresh trail
no one saw the target
no route evidence exists
debug_omniscient_targets is false
```

## Required behavior

`look_for_tracks` should not freely read exact target location.

It should use one of these sources:

```text
1. existing memory_v3 leads;
2. current location traces;
3. recent target_moved / target_route_observed;
4. witnesses in current location;
5. debug_omniscient_targets only in debug/test mode.
```

## Suggested MVP behavior

If target is not visible/co-located:

```text
look_for_tracks should produce one of:
- target_route_observed to adjacent/likely route from prior lead;
- no_tracks_found;
- weak target_combat_noise / rumor if available;
```

It should not directly know `state["agents"][target_id]["location_id"]`.

## Better algorithm

```python
def _exec_look_for_tracks(...):
    target_belief = build_target_belief(...)
    current_loc = agent["location_id"]

    route = best route hypothesis where route.from_location_id == current_loc
    if route:
        write target_route_observed / target_moved with route.to_location_id
        return

    if state.get("debug_omniscient_targets"):
        reveal actual target.location_id
        return

    if random_check(skill_stalker, freshness, terrain):
        choose plausible adjacent location, not guaranteed actual target location
        write target_route_observed
    else:
        write no_tracks_found
```

## Required tests

```python
def test_look_for_tracks_does_not_reveal_exact_target_location_without_lead_or_debug():
    ...
```

Expected:

```text
target exists at loc_new
hunter at loc_old
no route lead
debug_omniscient_targets = false

Executing look_for_tracks should NOT write target_moved/to_location_id=loc_new.
```

```python
def test_look_for_tracks_can_use_existing_route_hypothesis():
    ...
```

```python
def test_look_for_tracks_can_reveal_actual_location_only_with_debug_omniscience():
    ...
```

## Priority

```text
BLOCKER
```

---

# 3. Main blocker B — one target_not_found fully suppresses old location

## Problem

Current test:

```python
test_target_not_found_suppresses_old_location
```

expects:

```text
target_last_known_location loc_b
+ one target_not_found loc_b
→ best_location_id is None
```

This is too harsh for gameplay.

Design goal from PR6:

```text
1 failed search:
  confidence drops strongly

2 failed searches:
  lead becomes stale / low confidence

3 failed searches:
  location becomes exhausted / cooldown
```

One failed search should not always delete the only lead entirely.

## Required behavior

Adjust weighting so one `target_not_found` lowers confidence but does not always zero it.

Example:

```text
target_last_known_location confidence 0.85
one target_not_found confidence 0.75
→ remaining confidence around 0.20–0.35
→ hypothesis still exists but is weak/stale
```

After repeated failures:

```text
3 target_not_found
→ exhausted_locations includes loc_b
→ TRACK_TARGET should not choose loc_b during cooldown
```

## Suggested changes

Preferred: apply target_not_found as a staged confidence multiplier:

```text
1 miss: confidence *= 0.45
2 miss: confidence *= 0.20
3 miss: confidence = 0 and location exhausted
```

This is easier to reason about than raw negative score subtraction.

## Required test changes

Replace current expectation:

```python
assert belief.best_location_id is None
assert belief.location_confidence == 0.0
```

with:

```python
assert belief.best_location_id == "loc_b"
assert 0.0 < belief.location_confidence < 0.85
```

Then keep separate test:

```python
def test_repeated_target_not_found_exhausts_location():
    ...
```

Expected:

```text
after 3 misses:
  loc_b in exhausted_locations
  best_location_id is None or next-best non-exhausted location
```

## Priority

```text
BLOCKER
```

---

# 4. Main blocker C — need hard E2E regression for “no repeated empty location forever”

## Problem

PR6 has unit tests for lead aggregation, but we need an E2E regression for the actual bug pattern:

```text
TRACK_TARGET
→ search_target old location
→ target_not_found
→ repeat same old location hundreds of times
```

Current E2E `target_moved` can pass because `look_for_tracks` omnisciently discovers the target's actual location.

That does not prove the search system is correct.

## Required test

Add:

```python
def test_e2e_hunter_does_not_repeat_same_empty_location_forever_without_tracks():
    ...
```

Setup:

```text
hunter remembers target_last_known_location = loc_old
target is not at loc_old
debug_omniscient_targets = false
look_for_tracks has no valid route clue
```

Run enough ticks.

Expected:

```text
- hunter writes target_not_found for loc_old;
- after <= 3 failed searches, loc_old becomes exhausted;
- hunter does NOT keep creating TRACK_TARGET/search_target loc_old;
- hunter switches to GATHER_INTEL / LOCATE_TARGET / question_witnesses / wait-cooldown;
- count(search_target loc_old) <= small threshold, e.g. 3–5.
```

Assertions:

```python
assert count_target_not_found(hunter, "loc_old") <= 5
assert "loc_old" in latest_belief.exhausted_locations
assert not current_active_plan_targets_location(hunter, "loc_old")
assert any_objective_decision(hunter, "GATHER_INTEL") or any_objective_decision(hunter, "LOCATE_TARGET")
```

## Priority

```text
BLOCKER
```

---

# 5. High priority — source_agent_id is probably wrong in HuntLead

## Problem

In `_record_to_hunt_lead()`:

```python
source_agent_id=str(record.get("agent_id") or "") or None
```

But `record.agent_id` is usually the owner of the memory, i.e. the hunter.

For `target_intel`, the source should be:

```text
details.source_agent_id
```

for example:

```text
trader_debug_0
```

not the hunter id.

## Required fix

Use:

```python
source_agent_id = (
    details.get("source_agent_id")
    or details.get("witness_id")
    or details.get("trader_id")
    or record.get("source_agent_id")
)
```

Then fallback to `None`.

## Required test

```python
def test_hunt_lead_preserves_intel_source_agent_id():
    ...
```

Expected:

```text
target_intel details.source_agent_id = trader_1
lead.source_agent_id == trader_1
```

## Priority

```text
HIGH
```

---

# 6. High priority — question_witnesses is only an alias for ask_for_intel

## Current behavior

`_exec_question_witnesses()` simply calls:

```python
_exec_ask_for_intel(...)
```

This is okay as a temporary compatibility fallback, but PR6 explicitly wants different search actions.

## Required behavior

`question_witnesses` should ask co-located non-trader stalkers first.

Expected outputs:

```text
target_intel
target_last_known_location
target_rumor_unreliable
no_witnesses
```

Trader-specific paid intel should remain:

```text
ask_for_intel
```

## Minimal implementation

```text
question_witnesses:
  - inspect co-located stalker observations/memory if available;
  - if they saw target recently, write target_intel / target_last_known_location;
  - if no witnesses, write no_witnesses;
  - do not automatically buy trader intel.
```

## Test

```python
def test_question_witnesses_does_not_buy_trader_intel_by_default():
    ...
```

```python
def test_question_witnesses_can_write_target_intel_from_colocated_stalker_memory():
    ...
```

## Priority

```text
HIGH
```

---

# 7. High priority — debug/export is incomplete

## Problem

`frontend/src/games/zone_stalkers/ui/index.tsx` type definitions were extended with:

```text
possible_locations
likely_routes
exhausted_locations
lead_count
```

But PR6 expected debug/profile/export visibility.

No obvious changes were made to:

```text
AgentProfileModal
NpcBrainPanel
exportNpcHistory.ts
```

So the user may still not see PR6 hunt-search state in the NPC profile or compact export.

## Required frontend/debug additions

Add section:

```text
Hunt Search / Поиск цели
```

Show:

```text
target_id
best_location_id
best_location_confidence
possible_locations
likely_routes
exhausted_locations
lead_count
source_refs
```

Recommended panel:

```text
frontend/src/games/zone_stalkers/ui/agent_profile/HuntSearchPanel.tsx
```

or extend existing `NpcBrainPanel`.

## Required compact export addition

In `exportNpcHistory.ts`, add:

```json
"hunt_search": {
  "target_id": "...",
  "best_location_id": "...",
  "best_location_confidence": 0.72,
  "possible_locations": [...],
  "likely_routes": [...],
  "exhausted_locations": [...],
  "lead_count": 7
}
```

## Manual acceptance

Open NPC profile after a hunt run and verify:

```text
- possible locations are visible;
- exhausted locations are visible;
- best location confidence is visible;
- source_refs/memory evidence is visible.
```

## Priority

```text
HIGH
```

---

# 8. Medium priority — memory_used should prioritize hunt leads

## Problem

Previous logs showed `memory_used` often filled by:

```text
active_plan_created
active_plan_step_started
active_plan_step_completed
active_plan_completed
```

For hunt search, `memory_used` should prefer:

```text
target_intel
target_last_known_location
target_seen
target_not_found
target_moved
target_route_observed
```

## Required behavior

When generating hunt objectives, ensure:

```text
objective.source_refs includes target_belief.source_refs
```

especially for:

```text
GATHER_INTEL
VERIFY_LEAD
TRACK_TARGET
INTERCEPT_TARGET
ENGAGE_TARGET
CONFIRM_KILL
```

Then `ActivePlan.memory_refs` and UI can show relevant target evidence.

## Required test

```python
def test_track_target_objective_source_refs_include_best_lead_memory():
    ...
```

Expected:

```text
TRACK_TARGET source_refs contains memory:<target_intel_or_target_seen_record>
```

## Priority

```text
MEDIUM/HIGH
```

---

# 9. Medium priority — stale memory handling may be too aggressive

## Problem

`_record_to_hunt_lead()` ignores records with:

```python
status in {"archived", "stale"}
```

For hunt search, stale target memories can still be useful as weak leads.

Example:

```text
old target_seen at loc_A is stale,
but if there are no better leads,
it should still contribute weakly or lead to GATHER_INTEL around loc_A.
```

## Suggested behavior

```text
archived → ignore
stale → keep with strong freshness/confidence penalty
```

For example:

```python
if record.status == "stale":
    confidence *= 0.35
    freshness *= 0.35
```

## Required test

```python
def test_stale_target_memory_can_still_create_weak_hypothesis():
    ...
```

Expected:

```text
stale target_last_known_location still appears in possible_locations
with low confidence.
```

## Priority

```text
MEDIUM
```

---

# 10. Medium priority — no explicit target_location_exhausted event

## Current behavior

`target_not_found` includes:

```text
failed_search_count
cooldown_until_turn
```

This is workable, but debug/story would be clearer with an explicit event after threshold:

```text
target_location_exhausted
```

## Suggested behavior

When `failed_search_count >= 3`:

```text
write target_location_exhausted
```

Details:

```json
{
  "action_kind": "target_location_exhausted",
  "target_id": "...",
  "location_id": "...",
  "failed_search_count": 3,
  "cooldown_until_turn": 12345
}
```

Memory bridge mapping:

```text
target_location_exhausted → spatial/goal layer
tags: target, tracking, exhausted
```

## Priority

```text
MEDIUM
```

---

# 11. Medium priority — search action outcomes should be less deterministic

## Problem

For PR6 MVP, many search actions are deterministic.

Examples:

```text
target exists at current location → search_target always finds target
target exists elsewhere → look_for_tracks reveals actual location
```

`search_target` being deterministic in the same location is fine.

But `look_for_tracks` should depend on:

```text
skill_stalker
freshness
terrain
weather / anomaly danger
time since target passed
target wounded/bleeding/limping
```

## Suggested minimal scoring

```python
track_success_chance =
    0.25
    + skill_stalker * 0.05
    + freshness * 0.25
    + wounded_bonus
    - terrain_penalty
```

If failed:

```text
no_tracks_found
```

If partial:

```text
target_route_observed with low confidence
```

If successful:

```text
target_moved / target_route_observed with higher confidence
```

## Priority

```text
MEDIUM
```

---

# 12. Tests to add or update before closing PR6

## Blocker tests

```python
def test_look_for_tracks_does_not_reveal_exact_target_location_without_lead_or_debug():
    ...

def test_look_for_tracks_can_use_existing_route_hypothesis():
    ...

def test_single_target_not_found_lowers_but_does_not_delete_location():
    ...

def test_repeated_target_not_found_exhausts_location_after_threshold():
    ...

def test_e2e_hunter_does_not_repeat_same_empty_location_forever_without_tracks():
    ...
```

## High-priority tests

```python
def test_hunt_lead_preserves_intel_source_agent_id():
    ...

def test_question_witnesses_can_write_target_intel_from_colocated_stalker_memory():
    ...

def test_track_target_objective_source_refs_include_best_lead_memory():
    ...
```

## Optional medium tests

```python
def test_stale_target_memory_can_still_create_weak_hypothesis():
    ...

def test_target_location_exhausted_memory_written_after_three_failed_searches():
    ...

def test_hunt_search_export_contains_possible_locations_and_exhausted_locations():
    ...
```

---

# 13. Current readiness estimate

```text
PR6 architecture/model layer: 70–80%
TargetBelief lead aggregation: 70%
Search executors: 45–55%
Objective/planner integration: 65–75%
Debug/export visibility: 20–30%
E2E anti-loop coverage: 40–50%
```

---

# 14. Minimal blocker list before closing PR6

PR6 can be closed when these are fixed:

```text
[ ] look_for_tracks no longer reveals exact target location without lead/debug.
[ ] one target_not_found lowers confidence but does not always delete the lead.
[ ] repeated target_not_found exhausts/cooldowns the location.
[ ] E2E proves hunter does not search same empty location forever.
[ ] Hunt search state is visible in debug/export.
[ ] source_agent_id for HuntLead uses details.source_agent_id, not memory owner.
```

Recommended but non-blocking:

```text
[ ] question_witnesses becomes distinct from ask_for_intel.
[ ] stale hunt memory can still contribute weakly.
[ ] explicit target_location_exhausted memory event.
[ ] search outcomes become skill/freshness based rather than fully deterministic.
```

---

# 15. Expected fixed behavior after PR6

Before PR6/current issue:

```text
target_last_known_location = loc_old
hunter goes to loc_old
target_not_found
hunter repeatedly searches loc_old
```

After PR6 final:

```text
target_last_known_location = loc_old, confidence 0.85

1st failed search:
  loc_old confidence drops to ~0.3
  hunter may look for tracks / ask witnesses

2nd failed search:
  loc_old becomes weak/stale

3rd failed search:
  loc_old exhausted until cooldown
  hunter chooses GATHER_INTEL / VERIFY_LEAD elsewhere / question witnesses

If tracks found:
  target_route_observed loc_old → loc_new
  possible_locations includes loc_new
  TRACK_TARGET loc_new
```

This is the expected closing state for PR6.
