# PR 6 Addendum — Debug Map Support for Hunt Traces and Target Search

> Branch context: `copilot/implement-pr-6-new-mechanics`
>
> Goal:
>
> ```text
> Add debug-map visibility for the new PR6 hunt/search mechanics:
>
> - traces/leads in location profile;
> - map overlay modes for target search;
> - route/lead visualization;
> - filters for hunter/target/freshness/confidence;
> - compact export support;
> - tests/manual checks.
> ```
>
> This addendum belongs to PR6 because PR6 introduces:
>
> ```text
> HuntLead
> TargetBelief.possible_locations
> TargetBelief.likely_routes
> TargetBelief.exhausted_locations
> target_not_found suppression
> target_moved / target_route_observed
> look_for_tracks / question_witnesses
> ```
>
> Without map/debug support, it is very hard to evaluate whether the trace mechanic actually works.

---

# 1. Problem

PR6 adds target search as a lead-based system, but the debug map still mostly shows locations, agents and profile data.

For hunt/search debugging, we need to answer visually:

```text
Where does the hunter think the target may be?
Why does he think that?
Which locations are weak/strong leads?
Which locations are exhausted?
Where did the target move?
Which route is inferred?
Why did the NPC choose TRACK_TARGET / VERIFY_LEAD / GATHER_INTEL?
```

Currently this is hard to see from the map.

The user has to open raw debug JSON or inspect memory records manually.

---

# 2. Desired UX

## 2.1. Location profile: new "Следы / Hunt Traces" section

When a location is selected on the debug map, the right-side location profile should include a section:

```text
Следы / Hunt Traces
```

This section should show all hunt/search evidence related to the selected location.

Example:

```text
Следы / Hunt Traces

Target: Челик 3
Best lead: yes
Confidence: 72%
Freshness: 81%
Reason: target_moved
Source: Поцик 1 memory / combat retreat

Positive leads:
  • target_seen — 95% — 12 turns ago — Поцик 1 saw target here
  • target_intel — 65% — 80 turns ago — trader reported target here

Negative leads:
  • target_not_found — 75% — 4 turns ago — checked and did not find target
  • target_location_exhausted — cooldown until turn 5230

Routes:
  • loc_G5 → loc_debug_61 — 80% — target_moved
  • loc_debug_61 → loc_debug_65 — 65% — target_route_observed

Hunters interested:
  • Поцик 1: TRACK_TARGET, confidence 72%
```

---

## 2.2. Map overlay modes

Add a debug map mode selector.

Recommended modes:

```text
Default
Agents
Hunt Leads
Target Search
Exhausted Locations
Target Routes
Combat/Hunt Events
```

### Mode: Hunt Leads

Shows location confidence for selected hunter/target pair.

Visual meaning:

```text
high confidence positive lead → stronger highlight
weak lead → faint highlight
negative/exhausted location → red/grey marker
```

### Mode: Target Search

Shows what the selected hunter currently believes.

Highlights:

```text
best_location_id
possible_locations
likely_routes
exhausted_locations
current ActivePlan destination
```

### Mode: Exhausted Locations

Shows locations that are temporarily bad/stale for the target search.

Useful for debugging:

```text
Why did the NPC stop searching this location?
Did target_not_found create cooldown?
Is cooldown still active?
```

### Mode: Target Routes

Shows inferred target movement.

Route arrows should be drawn for:

```text
target_moved
target_route_observed
retreat_observed converted to target_moved
```

Route edge tooltip:

```text
from_location_id
to_location_id
confidence
freshness
source kind
turn
target id
hunter id
```

### Mode: Combat/Hunt Events

Shows events:

```text
target_seen
target_not_found
target_moved
target_route_observed
target_death_confirmed
hunt_failed
combat_initiated
combat_resolved
```

This is useful for replay-like debugging.

---

# 3. Filters

Add filters to the debug map panel.

Minimum filters:

```text
selected hunter
selected target
lead kind
min confidence
freshness window
show exhausted
show routes
show source refs
```

Recommended controls:

```text
Hunter: [Поцик 1]
Target: [Челик 3]
Mode: [Target Search]
Min confidence: 0.20
Freshness: [last 500 turns]
Kinds:
  [x] target_seen
  [x] target_intel
  [x] target_moved
  [x] target_route_observed
  [x] target_not_found
  [x] exhausted
```

Default behavior:

```text
If selected NPC has kill_target_id:
  preselect hunter = selected NPC
  preselect target = selected NPC.kill_target_id
  mode = Target Search
```

---

# 4. Backend/debug data contract

The frontend should not reconstruct all hunt-search visualization from raw memory records every time.

Add a derived debug block to the agent debug payload or world debug payload.

Recommended location:

```text
agent.brain_v3_context.hunt_search_debug
```

or:

```text
agent.debug.hunt_search
```

If the current debug payload already includes `brain_v3_context.hunt_target_belief`, extend it.

## 4.1. Agent-level hunt search debug

```json
{
  "hunt_search": {
    "hunter_id": "agent_debug_1",
    "target_id": "agent_debug_2",
    "target_name": "Челик 3",

    "best_location_id": "loc_debug_61",
    "best_location_confidence": 0.72,

    "possible_locations": [
      {
        "location_id": "loc_debug_61",
        "location_name": "Бункер торговца",
        "probability": 0.54,
        "confidence": 0.72,
        "freshness": 0.81,
        "reason": "target_moved",
        "source_refs": ["memory:abc"]
      }
    ],

    "likely_routes": [
      {
        "from_location_id": "loc_G5",
        "to_location_id": "loc_debug_61",
        "confidence": 0.8,
        "freshness": 0.91,
        "reason": "target_moved",
        "source_refs": ["memory:def"]
      }
    ],

    "exhausted_locations": [
      {
        "location_id": "loc_G5",
        "cooldown_until_turn": 5200,
        "failed_search_count": 3,
        "source_refs": ["memory:ghi"]
      }
    ],

    "lead_count": 14,
    "current_objective": "TRACK_TARGET",
    "current_plan_target_location_id": "loc_debug_61"
  }
}
```

## 4.2. Location-level hunt trace summary

For each location, generate:

```json
{
  "location_hunt_traces": {
    "loc_debug_61": {
      "location_id": "loc_debug_61",
      "positive_leads": [
        {
          "hunter_id": "agent_debug_1",
          "target_id": "agent_debug_2",
          "kind": "target_seen",
          "confidence": 0.95,
          "freshness": 0.88,
          "turn": 3120,
          "summary": "Поцик 1 видел Челика 3 здесь",
          "source_ref": "memory:abc"
        }
      ],
      "negative_leads": [
        {
          "hunter_id": "agent_debug_1",
          "target_id": "agent_debug_2",
          "kind": "target_not_found",
          "confidence": 0.75,
          "freshness": 0.97,
          "turn": 3130,
          "summary": "Цель не найдена",
          "failed_search_count": 1,
          "cooldown_until_turn": null,
          "source_ref": "memory:def"
        }
      ],
      "routes_in": [
        {
          "target_id": "agent_debug_2",
          "from_location_id": "loc_G5",
          "confidence": 0.8,
          "kind": "target_moved"
        }
      ],
      "routes_out": [
        {
          "target_id": "agent_debug_2",
          "to_location_id": "loc_debug_65",
          "confidence": 0.65,
          "kind": "target_route_observed"
        }
      ],
      "is_exhausted_for": [
        {
          "hunter_id": "agent_debug_1",
          "target_id": "agent_debug_2",
          "cooldown_until_turn": 5200
        }
      ]
    }
  }
}
```

This can be built from:

```text
all agents' memory_v3 records
TargetBelief.possible_locations
TargetBelief.likely_routes
TargetBelief.exhausted_locations
```

---

# 5. Backend implementation tasks

## 5.1. Add debug aggregation helper

Suggested file:

```text
backend/app/games/zone_stalkers/debug/hunt_search_debug.py
```

API:

```python
def build_hunt_search_debug_for_agent(
    *,
    agent_id: str,
    agent: dict[str, Any],
    state: dict[str, Any],
    world_turn: int,
) -> dict[str, Any]:
    ...
```

and:

```python
def build_location_hunt_traces(
    *,
    state: dict[str, Any],
    world_turn: int,
) -> dict[str, Any]:
    ...
```

## 5.2. Use existing TargetBelief

`build_hunt_search_debug_for_agent()` should not duplicate the belief algorithm.

It should use:

```text
build_agent_context
build_belief_state
build_target_belief
```

Then serialize:

```text
possible_locations
likely_routes
exhausted_locations
lead_count
source_refs
```

## 5.3. Extract raw hunt memory records

`build_location_hunt_traces()` should scan all agents:

```text
for each agent.memory_v3.records
  if record.kind in hunt_trace_kinds
    group by location_id
```

Hunt trace kinds:

```text
target_seen
target_last_known_location
target_intel
target_not_found
target_location_exhausted
target_moved
target_route_observed
target_wounded
target_combat_noise
target_death_confirmed
hunt_failed
combat_initiated
combat_resolved
```

## 5.4. Route extraction

For route records:

```text
from_location_id
to_location_id
location_id
```

Rules:

```text
if kind == target_moved:
  from = details.from_location_id
  to = details.to_location_id or record.location_id

if kind == target_route_observed:
  from = details.from_location_id
  to = details.to_location_id or record.location_id
```

## 5.5. Add to router/game-state payload

Wherever the frontend receives Zone Stalkers state, include:

```json
"debug": {
  "hunt_search_by_agent": {
    "agent_debug_1": { ... }
  },
  "location_hunt_traces": {
    "loc_G5": { ... }
  }
}
```

Alternative if there is no global `debug` field:

```json
"hunt_search_debug": {
  "by_agent": { ... },
  "by_location": { ... }
}
```

Keep it debug-only if payload size becomes a concern.

## 5.6. Payload size guard

Do not send unlimited history.

Limits:

```text
max records per location: 20
max routes per location: 20
max possible_locations per agent: 10
max source_refs per item: 5
freshness cutoff default: last 2000 turns
```

---

# 6. Frontend implementation tasks

## 6.1. Extend frontend types

File likely:

```text
frontend/src/games/zone_stalkers/ui/index.tsx
```

Add types:

```ts
type HuntSearchDebug = {
  hunter_id: string;
  target_id?: string;
  target_name?: string;
  best_location_id?: string | null;
  best_location_confidence?: number;
  possible_locations?: HuntLocationHypothesis[];
  likely_routes?: HuntRouteHypothesis[];
  exhausted_locations?: HuntExhaustedLocation[];
  lead_count?: number;
  current_objective?: string;
  current_plan_target_location_id?: string | null;
};

type LocationHuntTraceSummary = {
  location_id: string;
  positive_leads: HuntTraceRecord[];
  negative_leads: HuntTraceRecord[];
  routes_in: HuntRouteTrace[];
  routes_out: HuntRouteTrace[];
  is_exhausted_for: HuntExhaustedLocation[];
};
```

## 6.2. Add map overlay state

Add UI state:

```ts
type MapDebugOverlayMode =
  | 'default'
  | 'agents'
  | 'hunt_leads'
  | 'target_search'
  | 'exhausted_locations'
  | 'target_routes'
  | 'combat_hunt_events';
```

State:

```ts
const [mapOverlayMode, setMapOverlayMode] = useState<MapDebugOverlayMode>('default');
const [huntDebugHunterId, setHuntDebugHunterId] = useState<string | null>(null);
const [huntDebugTargetId, setHuntDebugTargetId] = useState<string | null>(null);
const [huntMinConfidence, setHuntMinConfidence] = useState(0.2);
const [huntFreshnessWindow, setHuntFreshnessWindow] = useState(2000);
const [showExhaustedLocations, setShowExhaustedLocations] = useState(true);
const [showHuntRoutes, setShowHuntRoutes] = useState(true);
```

## 6.3. Add map controls

Add a small panel near existing debug map controls:

```text
Overlay:
  Default / Hunt Leads / Target Search / Exhausted / Routes / Events

Hunter:
  [select NPC]

Target:
  [select target]

Min confidence:
  slider 0–1

Freshness:
  last 100 / 500 / 2000 / all turns

Checkboxes:
  show exhausted
  show routes
  show labels
```

## 6.4. Render location overlays

For each location node:

### Hunt Leads mode

Use:

```text
possible_locations confidence
```

Visual:

```text
confidence >= 0.75: strong highlight
0.4–0.75: medium highlight
0.15–0.4: weak highlight
```

Suggested rendering:

```text
border thickness = confidence
opacity = freshness
badge = reason / confidence
```

### Exhausted Locations mode

Render exhausted locations as:

```text
red/grey marker
badge: "exhausted"
tooltip: cooldown_until_turn, failed_search_count
```

### Target Search mode

Render:

```text
best_location_id: special marker
possible_locations: confidence markers
active plan target: pulsing outline
hunter current location
target current location only if debug_omniscient_targets or visible in profile/debug
```

Important:

```text
Do not reveal actual target location in normal target-search mode unless it is part of debug state intentionally.
```

This is a debug map, but still distinguish:

```text
known to NPC
actual state
```

Suggested toggles:

```text
[x] Show NPC belief
[ ] Show actual target location
```

Actual target location should be an explicit debug toggle.

## 6.5. Render route arrows

If the current map renderer can draw SVG/Canvas lines between locations, add arrows for:

```text
likely_routes
routes_in
routes_out
```

If drawing arrows is hard, MVP:

```text
show route badges in both connected location cards/tooltips
```

But preferred:

```text
arrow from_location_id → to_location_id
opacity = confidence
style:
  target_moved: solid
  target_route_observed: dashed
  retreat_observed-derived: dashed red/orange
```

## 6.6. Location profile section

Wherever selected location details are shown on the right side, add:

```tsx
<LocationHuntTracesPanel
  location={selectedLocation}
  traces={debug.location_hunt_traces?.[selectedLocation.id]}
  selectedHunterId={huntDebugHunterId}
  selectedTargetId={huntDebugTargetId}
/>
```

Panel structure:

```text
Следы / Hunt Traces

Summary:
  positive leads count
  negative leads count
  routes in/out count
  exhausted for N hunters

Positive leads:
  kind
  target
  hunter/source
  confidence
  freshness
  turn
  summary

Negative leads:
  target_not_found
  failed_search_count
  cooldown

Routes:
  from → to
  confidence
  reason

Raw source refs:
  memory:...
```

## 6.7. Agent profile integration

In NPC profile / Brain panel, show:

```text
Hunt Search
  best_location_id
  confidence
  possible_locations
  likely_routes
  exhausted_locations
  lead_count
```

If this is implemented separately from map work, reuse the same component:

```text
HuntSearchPanel
```

---

# 7. Visual semantics

Suggested map colors/styles:

```text
positive lead:
  blue/cyan highlight

best lead:
  bright cyan + badge "best"

target_seen:
  strong outline

target_intel:
  dotted outline

target_moved / route:
  arrow

target_not_found:
  red cross / red ring

exhausted:
  grey/red hatch or "EXH" badge

active plan destination:
  pulsing yellow outline

actual target location debug:
  purple marker, only if toggle enabled
```

Do not rely on color only.

Add text badges:

```text
seen
intel
moved
not found
exhausted
best
route
```

---

# 8. Distinguish belief vs actual world state

This is critical.

The debug map should allow two separate layers:

```text
NPC belief layer:
  what the hunter knows/thinks

Actual world layer:
  where the target really is
```

By default for hunt debugging:

```text
show NPC belief
hide actual target location
```

Add explicit toggle:

```text
Show actual target location
```

When enabled:

```text
draw actual target marker with different style
```

This prevents confusing two questions:

```text
Did NPC reason correctly from his knowledge?
Did NPC know hidden world state?
```

---

# 9. Compact export additions

Update:

```text
frontend/src/games/zone_stalkers/ui/agent_profile/exportNpcHistory.ts
```

Add:

```json
"hunt_search": {
  "target_id": "...",
  "target_name": "...",
  "best_location_id": "...",
  "best_location_confidence": 0.72,
  "possible_locations": [...],
  "likely_routes": [...],
  "exhausted_locations": [...],
  "lead_count": 12
}
```

Add to story timeline grouping:

```text
target_not_found x3 at loc_G5
target_moved loc_G5 → loc_debug_61
target_route_observed loc_debug_61 → loc_debug_65
```

Do not dump all raw memory records into compact export.

Limit to:

```text
top 5 possible locations
top 5 routes
all exhausted locations relevant to current target
```

---

# 10. Backend tests

Add:

```text
backend/tests/debug/test_hunt_search_debug.py
```

Tests:

```python
def test_hunt_search_debug_contains_possible_locations():
    ...

def test_location_hunt_traces_groups_positive_and_negative_leads():
    ...

def test_location_hunt_traces_extracts_routes_from_target_moved():
    ...

def test_exhausted_location_appears_in_debug_payload():
    ...

def test_hunt_search_debug_does_not_include_unbounded_memory_records():
    ...
```

Expected examples:

```text
target_seen loc_A → positive_leads loc_A
target_not_found loc_A → negative_leads loc_A
target_moved loc_A→loc_B → routes_out loc_A and routes_in loc_B
repeated target_not_found → exhausted marker
```

---

# 11. Frontend checks

If frontend tests exist, add tests for:

```text
LocationHuntTracesPanel renders:
  positive leads
  negative leads
  exhausted locations
  routes

Map overlay mode controls:
  can switch to Hunt Leads
  can filter by hunter/target
```

If frontend test infra is not present, add manual acceptance checklist to the PR description.

---

# 12. Manual acceptance checklist

PR is acceptable when the developer can run a hunt simulation and visually verify:

```text
[ ] Select a hunter with kill_stalker goal.
[ ] Map switches to Target Search mode.
[ ] Best believed target location is highlighted.
[ ] Other possible locations are visible with confidence.
[ ] Exhausted locations are visibly marked.
[ ] target_moved / target_route_observed routes are visible.
[ ] Selecting a location shows Hunt Traces section on the right.
[ ] Hunt Traces shows positive and negative evidence.
[ ] Hunt Traces shows failed_search_count and cooldown for target_not_found.
[ ] The UI distinguishes NPC belief from actual target location.
[ ] Compact NPC story export includes hunt_search.
```

---

# 13. Acceptance criteria

This addendum is complete when:

```text
[ ] Backend exposes hunt_search_debug by agent.
[ ] Backend exposes location_hunt_traces by location.
[ ] Location profile shows Hunt Traces section.
[ ] Map has Hunt Leads / Target Search / Exhausted / Routes modes.
[ ] Map can filter by hunter and target.
[ ] Route arrows or equivalent route visualization exists.
[ ] Exhausted locations are visible.
[ ] Best location confidence is visible.
[ ] Actual target location is hidden by default and only shown with explicit debug toggle.
[ ] Compact export contains hunt_search summary.
[ ] Tests or manual checklist cover the feature.
```

---

# 14. Recommended implementation order

1. Backend debug aggregation:
   ```text
   build_hunt_search_debug_for_agent
   build_location_hunt_traces
   ```

2. Add debug payload to game state / router response.

3. Extend frontend types.

4. Add LocationHuntTracesPanel.

5. Add map overlay mode controls.

6. Render location confidence badges.

7. Render exhausted locations.

8. Render route arrows or route badges.

9. Add compact export `hunt_search`.

10. Add backend tests and manual checklist.

---

# 15. Relation to PR6 closing blockers

This debug-map work should be done together with the remaining PR6 gameplay fixes:

```text
- remove omniscient look_for_tracks;
- make target_not_found staged, not instant deletion;
- add E2E for false lead without omniscient tracks;
- expose hunt search state in debug/export.
```

The map/debug feature is not just visual polish.

It is required to evaluate whether the PR6 trace system is actually working.
