# Zone Stalkers — Full Debug Optimization Completion PR

> Target branch base:
>
> ```text
> copilot/optimize-network-traffic-cpu-load
> ```
>
> Goal:
>
> ```text
> Fully finish traffic + CPU optimization for Zone Stalkers with debug mode as a first-class workflow.
> ```
>
> Debug is the main working mode, so it must be optimized, not treated as a heavy fallback.

---

# 0. Current status before this PR

The current optimization branch already added important groundwork:

```text
backend/app/games/zone_stalkers/delta.py
frontend/src/games/zone_stalkers/state/applyZoneDelta.ts
frontend/src/games/zone_stalkers/state/types.ts
projection endpoint
state_revision
zone_delta WebSocket payload
compact tick fallback
explicit game/zone-lite projection builder
basic tests for delta/projection/ws compactness
```

This is good, but it is not yet a fully optimized debug workflow.

Known remaining gaps:

```text
1. Command flow still sends generic state_updated, not full zone_delta.
2. Debug map still likely depends on debug-map projection refresh.
3. Debug hunt traces are derived/heavy and should not be rebuilt/transmitted blindly.
4. Frontend needs explicit debug subscription state.
5. NPC profile still needs lazy detail loading for heavy sections.
6. Normal zone_delta should stay small, but debug_delta should exist for selected debug scope.
7. tick_zone_map still deepcopies state.
8. Debug trace/memory growth needs stricter bounds.
```

---

# 1. Desired final architecture

## 1.1. Normal game UI

```text
Initial load:
  GET /zone-stalkers/contexts/{context_id}/projection?mode=game

Runtime:
  WebSocket zone_delta

No per-tick HTTP refresh.
```

## 1.2. Debug map UI

```text
Initial load:
  GET /zone-stalkers/contexts/{context_id}/projection?mode=debug-map-lite
  or:
  GET /zone-stalkers/contexts/{context_id}/map-static
  GET /zone-stalkers/contexts/{context_id}/map-dynamic

Runtime:
  WebSocket zone_delta
  WebSocket zone_debug_delta for selected debug scope

Heavy details:
  GET debug detail endpoints on demand
```

## 1.3. NPC profile

```text
Profile header / current decision:
  comes from current projected state + zone_delta/debug_delta

Heavy sections:
  memory
  memory_v3
  brain_trace
  raw debug
  full active_plan
  full hunt_search
  story export

loaded only when panel opened or export button pressed.
```

## 1.4. Location profile

```text
Basic location state:
  from zone_delta / debug-map-lite

Hunt Traces:
  from zone_debug_delta for visible/selected locations
  or from GET /debug/hunt-search/locations/{location_id}
```

---

# 2. Backend — complete WebSocket revision model

## 2.1. State revisions

Ensure every Zone Stalkers state mutation increments:

```text
state_revision
```

Required mutation paths:

```text
- tick_zone_map through ruleset.tick
- player commands through CommandPipeline
- debug commands
- set_auto_tick only if it changes zone state
- any direct debug endpoint that mutates state
```

Every projection response must include:

```json
{
  "state_revision": 123,
  "map_revision": 1
}
```

## 2.2. Command flow must stop sending blind `state_updated`

Current issue:

```text
player command
→ backend mutates state_revision
→ sends generic state_updated
→ frontend must resync
```

This causes extra HTTP traffic.

### Required fix

For Zone Stalkers `zone_map` commands, build and send a `zone_delta` exactly like tick flow.

In `backend/app/core/commands/pipeline.py`:

```python
old_state = state
new_state, new_events = ruleset.resolve_command(...)
new_state["state_revision"] = int(old_state.get("state_revision", 0)) + 1

if match.game_id == "zone_stalkers" and new_state.get("context_type") == "zone_map":
    from app.games.zone_stalkers.delta import build_zone_delta
    zone_delta = build_zone_delta(
        old_state=old_state,
        new_state=new_state,
        events=serializable_events,
        mode="game",
    )
    ws_manager.notify(str(envelope.match_id), {
        "type": "zone_delta",
        "match_id": str(envelope.match_id),
        "context_id": str(envelope.context_id),
        **zone_delta,
    })
else:
    ws_manager.notify(... existing fallback ...)
```

If delta building fails:

```json
{
  "type": "state_updated",
  "match_id": "...",
  "context_id": "...",
  "state_revision": 124,
  "requires_resync": true
}
```

Acceptance:

```text
[ ] Player command no longer forces full-state refresh by default.
[ ] Commands emit zone_delta when possible.
[ ] Fallback state_updated includes context_id/state_revision/requires_resync.
```

---

# 3. Backend — debug WebSocket subscription model

Debug mode needs more data than normal game mode, but not all of it all the time.

## 3.1. Add debug subscription state

In WebSocket manager or per-match connection metadata, track:

```ts
type ZoneDebugSubscription = {
  context_id: string;
  mode: "debug-map" | "agent-profile" | "location-profile";
  selected_agent_id?: string | null;
  selected_location_id?: string | null;
  hunter_id?: string | null;
  target_id?: string | null;
  visible_location_ids?: string[];
  min_confidence?: number;
  freshness_window?: number;
};
```

Client messages:

```json
{
  "type": "subscribe_zone_debug",
  "match_id": "...",
  "context_id": "...",
  "mode": "debug-map",
  "hunter_id": "agent_debug_1",
  "target_id": "agent_debug_0",
  "visible_location_ids": ["loc_A", "loc_B"],
  "freshness_window": 1000,
  "min_confidence": 0.2
}
```

```json
{
  "type": "unsubscribe_zone_debug",
  "match_id": "...",
  "context_id": "..."
}
```

If current WebSocket client message handling is not implemented, add it now.

Fallback if client-message support is too expensive:

```text
Use HTTP endpoints with throttle for debug detail,
but still do not send full debug-map projection every tick.
```

Preferred: implement subscriptions.

## 3.2. Debug delta message

Add WebSocket message:

```json
{
  "type": "zone_debug_delta",
  "match_id": "...",
  "context_id": "...",
  "base_revision": 123,
  "revision": 124,
  "debug_revision": 55,
  "scope": {
    "mode": "debug-map",
    "hunter_id": "agent_debug_1",
    "target_id": "agent_debug_0"
  },
  "changes": {
    "hunt_search_by_agent": {},
    "location_hunt_traces": {},
    "agent_brain_summary": {},
    "selected_agent_profile_summary": {},
    "selected_location_summary": {}
  }
}
```

Debug delta must be:

```text
- bounded;
- filtered by subscription;
- not full state;
- not full memory;
- not full brain_trace;
- not all locations unless explicitly requested.
```

---

# 4. Backend — debug delta builder

Add file:

```text
backend/app/games/zone_stalkers/debug_delta.py
```

API:

```python
def build_zone_debug_delta(
    *,
    old_state: dict[str, Any],
    new_state: dict[str, Any],
    subscription: dict[str, Any],
    world_turn: int,
) -> dict[str, Any]:
    ...
```

## 4.1. Debug delta must include only relevant scope

### For `mode = debug-map`

Include:

```text
- selected hunter hunt_search summary;
- selected target summary;
- visible/selected location hunt traces summary;
- route changes relevant to hunter/target;
- exhausted location changes relevant to hunter/target;
- event counters/preview.
```

Do not include:

```text
- all agents' memory;
- all location_hunt_traces;
- full brain traces;
- raw objective rankings for all agents.
```

### For `mode = agent-profile`

Include only selected agent:

```text
- current brain summary;
- active_plan summary;
- current objective/intent;
- compact hunt_search;
- latest decision summary;
- needs/equipment/inventory summaries.
```

Do not include full memory unless requested.

### For `mode = location-profile`

Include only selected location:

```text
- location agents/items/artifacts counts;
- hunt traces for selected location;
- relevant routes in/out;
- active events there.
```

---

# 5. Backend — derived debug cache/index

Hunt debug is derived from memory and belief records. Rebuilding it by scanning all records every tick is too expensive.

## 5.1. Add debug cache fields

In state:

```json
{
  "_debug_cache": {
    "hunt_trace_index_revision": 0,
    "hunt_search_revision": 0,
    "location_hunt_trace_index": {},
    "agent_hunt_search_cache": {}
  }
}
```

This is runtime/derived data. Keep it bounded.

## 5.2. Incremental hunt trace index

Instead of scanning all agents' memory_v3 records every tick:

```text
When _add_memory writes a hunt-relevant record,
update a lightweight index.
```

Hunt-relevant kinds:

```text
target_seen
target_last_known_location
target_intel
intel_from_stalker
intel_from_trader
target_not_found
target_location_exhausted
witness_source_exhausted
no_tracks_found
no_witnesses
target_moved
target_route_observed
target_wounded
target_combat_noise
target_death_confirmed
hunt_failed
combat_initiated
combat_resolved
```

Index shape:

```json
{
  "location_hunt_trace_index": {
    "loc_A": {
      "records": [
        {
          "id": "memory:...",
          "kind": "target_seen",
          "hunter_id": "agent_debug_1",
          "target_id": "agent_debug_0",
          "turn": 123,
          "confidence": 0.8,
          "source_ref": "memory:..."
        }
      ],
      "revision": 42
    }
  }
}
```

Keep bounds:

```text
max records per location in index: 100
max total indexed records: configurable, e.g. 5000
drop oldest beyond limit
```

## 5.3. Agent hunt search cache

Cache `TargetBelief`/hunt search summary per hunter/target.

Cache key:

```text
agent_id
target_id
memory_v3_revision
agent_location_id
world_turn_bucket
debug_omniscient_targets
```

Invalidate when:

```text
agent moved
target seen
new hunt-relevant memory added
combat event involving target
world_turn bucket changed
target died
```

Suggested world turn bucket:

```text
5 or 10 turns
```

This avoids rebuilding full belief every single tick when nothing relevant changed.

---

# 6. Backend — make `build_hunt_debug_payload` on-demand

Current PR started gating with `debug_hunt_traces_enabled`. Complete it.

## 6.1. Normal tick path

`tick_zone_map()` must not do this by default:

```python
build_hunt_debug_payload(...)
```

Required:

```python
if state.get("debug_hunt_traces_enabled"):
    maybe_refresh_hunt_debug_payload(...)
```

Where `maybe_refresh` obeys:

```text
debug_hunt_traces_refresh_interval
last_built_turn
subscription state if available
```

Default:

```json
{
  "debug_hunt_traces_enabled": false,
  "debug_hunt_traces_refresh_interval": 10
}
```

## 6.2. Manual/on-demand endpoints

Add endpoints:

```text
GET /zone-stalkers/contexts/{context_id}/debug/hunt-search
GET /zone-stalkers/contexts/{context_id}/debug/hunt-search/agents/{agent_id}
GET /zone-stalkers/contexts/{context_id}/debug/hunt-search/locations/{location_id}
GET /zone-stalkers/contexts/{context_id}/debug/hunt-search/targets/{target_id}
POST /zone-stalkers/contexts/{context_id}/debug/hunt-search/refresh
```

Query filters:

```text
hunter_id
target_id
location_id
since_turn
freshness_window
min_confidence
limit
include_raw=false
```

Return bounded payload only.

Acceptance:

```text
[ ] Normal ticks do not rebuild all hunt traces.
[ ] Debug UI can still request exact hunt traces.
[ ] Debug map can refresh only visible/selected scope.
```

---

# 7. Backend — projection modes for debug

Current modes:

```text
game
zone-lite
debug-map
full
```

Add/clarify:

```text
debug-map-lite
agent-profile-lite
location-profile-lite
```

## 7.1. `debug-map-lite`

Initial debug map load should include:

```text
world time
state_revision
map_revision
locations static-ish data needed to draw map
location dynamic summaries
agents lite
selected/global debug controls state
NO full memory
NO full brain_trace
NO full location_hunt_traces
NO full hunt_search_by_agent for all agents
```

Debug details come via:

```text
zone_debug_delta
or debug endpoints
```

## 7.2. `agent-profile-lite`

For selected agent profile:

```text
current stats
current objective/intent
active_plan summary
hunt_search summary top N
needs/equipment/inventory summaries
latest 20 compact story events
```

Heavy tabs lazy load.

## 7.3. `location-profile-lite`

For selected location:

```text
location static info
agents currently there
item/artifact counts
active event count
hunt trace summary counts
```

Full hunt traces lazy load.

---

# 8. Backend — full state should be manual-only

Keep `mode=full`, but require one of:

```text
- explicit debug/raw button;
- admin/debug flag;
- manual endpoint.
```

Do not call full projection automatically from normal UI or debug map.

Frontend should show warning:

```text
Full state export can be large.
```

---

# 9. Frontend — WebSocket-first debug UI

## 9.1. Normal UI flow

```text
On mount:
  getZoneProjection(contextId, "game")
  store state_revision

On zone_delta:
  if base_revision matches:
    applyZoneDelta
  else:
    getZoneProjection(contextId, "game")
```

No full refresh after tick.

## 9.2. Debug map flow

```text
On open debug map:
  getZoneProjection(contextId, "debug-map-lite")
  send subscribe_zone_debug(mode="debug-map", filters...)

On zone_delta:
  apply normal world/entity changes

On zone_debug_delta:
  apply debug patches to debug store

On close debug map:
  send unsubscribe_zone_debug
```

Fallback if WS debug subscription is not ready:

```text
debug details refresh via HTTP every 1000–2000 ms max,
only while debug map is open,
only for visible/selected filters.
```

## 9.3. Frontend state split

Recommended:

```ts
zoneStateCore:
  world
  agents
  locations
  traders

debugState:
  huntSearchByAgent
  locationHuntTraces
  selectedAgentProfile
  selectedLocationProfile

uiState:
  selected ids
  filters
  overlay mode
```

At minimum, keep debugState separate from core zone state so normal deltas do not rerender all debug panels.

## 9.4. Throttle rendering

Suggested:

```ts
NORMAL_RENDER_THROTTLE_MS = 100-250
DEBUG_RENDER_THROTTLE_MS = 250-500
DEBUG_HTTP_REFRESH_MS = 1000-2000
```

Important:

```text
Do not issue HTTP request per WebSocket tick.
```

---

# 10. Frontend — apply debug delta

Add:

```text
frontend/src/games/zone_stalkers/state/applyZoneDebugDelta.ts
```

Pseudo:

```ts
export function applyZoneDebugDelta(
  debugState: ZoneDebugState,
  delta: ZoneDebugDelta,
): ZoneDebugState {
  return {
    ...debugState,
    huntSearchByAgent: {
      ...debugState.huntSearchByAgent,
      ...delta.changes.hunt_search_by_agent,
    },
    locationHuntTraces: {
      ...debugState.locationHuntTraces,
      ...delta.changes.location_hunt_traces,
    },
    selectedAgentProfile: delta.changes.selected_agent_profile_summary
      ? { ...debugState.selectedAgentProfile, ...delta.changes.selected_agent_profile_summary }
      : debugState.selectedAgentProfile,
  };
}
```

---

# 11. Frontend — lazy profile details

NPC profile should not receive full details by default.

## 11.1. Add endpoints/client methods

```ts
contextsApi.getZoneAgentProfile(contextId, agentId, mode = "lite")
contextsApi.getZoneAgentMemory(contextId, agentId, { limit, beforeTurn })
contextsApi.getZoneAgentBrainTrace(contextId, agentId, { limit })
contextsApi.getZoneAgentActivePlan(contextId, agentId)
contextsApi.getZoneAgentStoryExport(contextId, agentId)
```

## 11.2. UI behavior

```text
Opening NPC modal:
  load lite profile only.

Opening Memory tab:
  load memory page.

Opening Brain Trace tab:
  load brain trace page.

Clicking Full debug JSON:
  request full agent debug/export.
```

Acceptance:

```text
[ ] Opening NPC profile is fast.
[ ] Memory tab lazy-loads.
[ ] Brain trace tab lazy-loads.
[ ] Full debug export still works, but manual only.
```

---

# 12. Frontend — lazy location Hunt Traces

Location profile should show quick summary first:

```text
positive leads count
negative leads count
routes count
exhausted count
latest turn
```

Only load full traces when:

```text
- location selected;
- Hunt Traces panel expanded;
- filter changed;
- manual refresh clicked.
```

Endpoint:

```text
GET /zone-stalkers/contexts/{context_id}/debug/hunt-search/locations/{location_id}?hunter_id=&target_id=&limit=50
```

---

# 13. CPU — reduce tick work

## 13.1. Keep current CPU quick wins

Already added:

```text
MEMORY_DECAY_INTERVAL_TURNS = 30
LOCATION_OBSERVATION_INTERVAL_TURNS = 10
```

Keep and test.

## 13.2. Gate brain_trace

Current risk:

```text
ensure_brain_trace_for_tick() may touch every v3 monitored bot every tick.
```

Add config:

```json
{
  "debug_brain_trace_enabled": false,
  "debug_brain_trace_agent_ids": []
}
```

Default:

```text
false for all agents
```

When disabled:

```text
write only compact latest_decision_summary,
not full trace events.
```

When enabled:

```text
trace selected agents only
or keep last N.
```

Acceptance:

```text
[ ] Brain trace still works for selected debug NPC.
[ ] Normal/debug-map mode does not grow trace for every NPC forever.
```

## 13.3. Cap memory and trace structures harder

Current legacy memory cap is large:

```text
MAX_AGENT_MEMORY = 2000
```

For performance, use tiered storage:

```text
memory_recent_compact: last 200-300 entries for UI
memory_v3.records: bounded by TTL/importance
archive/export: manual only later
```

Minimal safe PR:

```text
- keep MAX_AGENT_MEMORY if changing it risks logic;
- ensure projections never send full memory;
- add memory detail endpoint with limit/pagination.
```

## 13.4. Remove `copy.deepcopy(state)` from tick later, but not mandatory here

The largest CPU issue left is:

```python
state = copy.deepcopy(state)
```

in `tick_zone_map`.

This is a deeper change. It can be done in this PR only if safe.

Preferred near-term safe approach:

```text
Leave deepcopy for correctness.
Remove other waste first:
  - no full projections every tick;
  - no full hunt debug every tick;
  - no full memory/trace in payload;
  - no all-agent brain traces.
```

If doing it now:

```text
Make a dedicated test pass for mutation isolation.
```

---

# 14. Backend — static/dynamic map split for debug map

Since debug map is the main mode, static location layout should not be resent often.

Add endpoints:

```text
GET /zone-stalkers/contexts/{context_id}/map-static
GET /zone-stalkers/contexts/{context_id}/map-dynamic
```

or add to projection:

```text
projection?mode=debug-map-lite&include_static=false
```

Static:

```text
location id/name/terrain/connections/debug_layout/image_url/region
map_revision
```

Dynamic:

```text
world time
state_revision
location agents/item_count/artifact_count/anomaly_activity
agent lite positions
```

Frontend:

```text
load static once per map_revision
apply zone_delta for dynamic updates
```

Acceptance:

```text
[ ] Debug map static layout loads once.
[ ] Subsequent ticks do not resend connections/debug_layout/image_url for every location.
[ ] map_revision mismatch reloads static map.
```

---

# 15. Backend — event history optimization

Do not send full event history repeatedly.

WebSocket:

```text
events.count
events.preview max 10
```

HTTP event history:

```text
GET /matches/{match_id}/events?after_sequence=&limit=50
```

Frontend should not refetch all events on every tick.

Acceptance:

```text
[ ] Event panel fetches incremental/paginated events.
[ ] Tick WS preview remains bounded.
```

---

# 16. Test plan

## 16.1. Backend tests

Add/update:

```text
backend/tests/test_zone_stalkers_delta.py
backend/tests/test_zone_stalkers_debug_delta.py
backend/tests/test_zone_stalkers_debug_endpoints.py
backend/tests/test_zone_stalkers_projections.py
backend/tests/test_ticker_ws_compact.py
```

Required tests:

```python
def test_command_flow_emits_zone_delta_for_zone_map():
    ...

def test_state_updated_fallback_includes_revision_and_resync():
    ...

def test_debug_delta_filters_by_selected_hunter_target():
    ...

def test_debug_delta_does_not_include_full_memory_or_brain_trace():
    ...

def test_hunt_debug_payload_not_built_when_disabled():
    ...

def test_hunt_search_location_endpoint_returns_bounded_traces():
    ...

def test_debug_map_lite_projection_excludes_full_location_hunt_traces():
    ...

def test_agent_profile_lite_excludes_full_memory_and_trace():
    ...

def test_map_static_contains_layout_but_not_dynamic_debug():
    ...

def test_map_dynamic_contains_counts_and_positions_only():
    ...
```

## 16.2. Frontend tests

Add if testing infra exists:

```text
applyZoneDelta.test.ts
applyZoneDebugDelta.test.ts
debug subscription lifecycle test
profile lazy loading test
```

Minimum:

```text
npm run build
```

Manual testing is mandatory.

---

# 17. Manual acceptance checklist

## 17.1. Network normal mode

```text
[ ] Initial load uses projection?mode=game or map-static + map-dynamic.
[ ] Auto tick sends zone_delta over WebSocket.
[ ] No HTTP request happens per tick.
[ ] zone_delta size is small and proportional to changed entities.
[ ] state_revision increases correctly.
[ ] Revision mismatch triggers one projection resync.
```

## 17.2. Network debug map

```text
[ ] Opening debug map does not fetch full state.
[ ] Debug map loads debug-map-lite/static map.
[ ] Debug data updates via zone_debug_delta or throttled scoped endpoint.
[ ] Full location_hunt_traces for all locations is not fetched every tick.
[ ] Selecting a location loads only that location's Hunt Traces.
[ ] Selecting hunter/target filters debug data.
```

## 17.3. NPC profile

```text
[ ] Opening NPC profile is fast.
[ ] Current decision/active plan summary visible immediately.
[ ] Memory tab lazy-loads.
[ ] Brain trace tab lazy-loads.
[ ] Full debug export is manual only.
```

## 17.4. CPU

```text
[ ] build_hunt_debug_payload is not called every tick when debug disabled.
[ ] Debug enabled refreshes hunt debug at interval or selected scope only.
[ ] Memory decay is interval-based.
[ ] Location observations are interval/location-change based.
[ ] Brain trace is disabled by default or selected-agent only.
```

---

# 18. Performance targets

These are practical targets, not strict hard requirements.

## Normal mode

```text
Per tick traffic:
  < 5-20 KB in typical small/medium scenario

No per-tick HTTP request.

Full state:
  manual/debug only.
```

## Debug map mode

```text
Per tick traffic:
  < 20-100 KB depending on selected filters

No full state per tick.

Hunt traces:
  scoped by selected/visible locations and hunter/target.
```

## CPU

```text
No full state-size report per tick.
No full projection per tick.
No full hunt debug scan per tick by default.
No all-agent brain_trace growth by default.
```

---

# 19. Implementation order

Do this in order.

## Step 1 — finish command delta

```text
CommandPipeline:
  old_state/new_state
  build zone_delta for zone_map commands
  fallback state_updated with revision/resync
```

## Step 2 — verify frontend tick delta flow

```text
initial projection
zone_delta apply
revision mismatch resync
no HTTP refresh per tick
```

## Step 3 — add debug-map-lite projection

```text
remove full hunt traces from initial debug map load
keep only summary/counts
```

## Step 4 — add scoped debug endpoints

```text
hunt-search agent
hunt-search location
hunt-search target
agent profile lite/detail
```

## Step 5 — add debug subscription / zone_debug_delta

```text
subscribe_zone_debug
unsubscribe_zone_debug
build scoped debug_delta
send only while debug map/profile open
```

If this is too large, do scoped HTTP endpoints + throttle first, then WebSocket debug delta immediately after. But the target for this completion PR is full debug optimization.

## Step 6 — frontend lazy debug/profile loading

```text
NPC profile lazy tabs
location Hunt Traces lazy panel
debug map visible filters
```

## Step 7 — CPU gates

```text
hunt debug on-demand/interval
brain_trace selected only
memory/observations throttles verified
```

## Step 8 — static/dynamic debug map split

```text
map-static
map-dynamic
map_revision reload
```

---

# 20. What not to do

Avoid these mistakes:

```text
1. Do not send full debug state through zone_delta.
2. Do not rebuild all location_hunt_traces every tick.
3. Do not fetch debug-map projection every tick.
4. Do not fetch full context after every zone_delta.
5. Do not put full memory/brain_trace into agent patches.
6. Do not let NPC profile load everything immediately.
7. Do not make debug mode fast by deleting useful debug data; make it lazy/scoped.
```

---

# 21. Final definition of done

This optimization topic can be considered “closed for now” when:

```text
[ ] Normal gameplay is WebSocket-first.
[ ] Debug map is also WebSocket/scoped-detail-first.
[ ] No full state refresh per tick.
[ ] No full debug projection refresh per tick.
[ ] Commands emit delta or explicit resync revision.
[ ] Hunt traces are scoped/lazy/cached.
[ ] NPC profile heavy sections are lazy.
[ ] Brain trace is selected-agent/on-demand.
[ ] Static map data is cached by map_revision.
[ ] Backend tests cover delta/debug endpoints.
[ ] Frontend build passes.
[ ] Manual Network tab confirms traffic reduction.
```

Expected final flow:

```text
normal:
  initial game snapshot
  → zone_delta only

debug map:
  initial debug-map-lite/static snapshot
  → zone_delta for world movement
  → zone_debug_delta or scoped debug endpoints for selected debug data

profile:
  lite profile
  → lazy memory/trace/export only when opened
```

This completes the traffic and CPU optimization pass while preserving the full PR6 debug experience.
