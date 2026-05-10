# Intermediate Optimization PR — Traffic and CPU Hotfixes After PR6

> Branch base: after closing `copilot/implement-pr-6-new-mechanics`
>
> Goal:
>
> ```text
> Fix the most obvious performance bottlenecks now.
> Do not start a large architecture rewrite.
> Do not spend this PR mainly on measurement.
> ```
>
> Focus areas:
>
> ```text
> 1. Reduce traffic caused by tick/update flow.
> 2. Reduce CPU work done every tick.
> 3. Preserve PR6 hunt-search debug features.
> ```
>
> This is an intermediate optimization PR between PR6 and the larger future optimization roadmap.

---

# 0. Current state after PR6

PR6 added important gameplay/debug features:

```text
HuntLead
TargetBelief possible_locations / likely_routes / exhausted_locations
Hunt Traces debug map
hunt_search_by_agent
location_hunt_traces
projection endpoints
basic performance metrics
```

PR6 also fixed the main correctness problems:

```text
search_target → target_found outcome
VERIFY_LEAD/TRACK_TARGET/PURSUE_TARGET stop after target_found
recently_seen → ENGAGE_TARGET
witness_source_exhausted cooldown
zero-confidence possible_locations filtering
route hints ignore exhausted destinations
```

So this intermediate PR should not touch hunt logic unless needed for performance gating.

---

# 1. Problem A — WebSocket/update traffic

## 1.1. Current WebSocket payload

`tick_match()` sends a WebSocket payload:

```python
ws_payload = {
    "type": "ticked",
    "match_id": match_id_str,
    "world_turn": result.get("world_turn"),
    "world_hour": result.get("world_hour"),
    "world_day": result.get("world_day"),
    "world_minute": result.get("world_minute"),
    "new_events": result.get("new_events", []),
}
```

This is already better than sending full state through WebSocket.

But there are two possible problems:

```text
1. new_events can become large.
2. Frontend may react to "ticked" by refetching full context/state.
```

The second problem is likely the bigger traffic issue.

---

# 2. Required change A — cap/summarize `new_events` in WebSocket tick payload

## File

```text
backend/app/core/ticker/service.py
```

## Required behavior

Do not send unlimited `new_events` through the WebSocket tick event.

Change WebSocket payload to include:

```json
{
  "type": "ticked",
  "match_id": "...",
  "world_turn": 123,
  "world_hour": 12,
  "world_day": 1,
  "world_minute": 34,
  "event_count": 17,
  "new_events_preview": [...]
}
```

Where:

```text
new_events_preview:
  max 5–10 events
  compact summaries only
```

Do not include full event payloads unless explicitly needed.

## Suggested constants

```python
WS_TICK_EVENT_PREVIEW_LIMIT = 10
```

## Suggested helper

```python
def _compact_tick_event(event: dict[str, Any]) -> dict[str, Any]:
    return {
        "event_type": event.get("event_type"),
        "payload": _compact_event_payload(event.get("payload", {})),
    }
```

`_compact_event_payload()` should keep only small fields:

```text
agent_id
location_id
world_turn
objective_key
action_kind
summary
```

Avoid sending:

```text
full memory
full brain traces
large nested payloads
debug blocks
```

## Acceptance

```text
[ ] WS tick payload size stays small even when many events are emitted.
[ ] Frontend still knows that a tick happened.
[ ] Frontend can show event count and a short preview if needed.
```

---

# 3. Required change B — frontend must not refetch full state after each tick

## Problem

If frontend receives:

```text
WebSocket: ticked
```

and then fetches the full context/state, WebSocket itself may look small, but overall traffic is still huge.

## Required behavior

For Zone Stalkers, normal refresh after tick should use projection:

```text
GET /zone-stalkers/contexts/{context_id}/projection?mode=game
```

not full context state.

Debug map should use:

```text
GET /zone-stalkers/contexts/{context_id}/projection?mode=debug-map
```

only when debug map is open.

Full state should be manual only:

```text
mode=full
```

## Frontend tasks

Search frontend tick/update flow and replace full-state refresh after `ticked` with:

```text
normal map/game UI → projection?mode=game
debug map open → projection?mode=debug-map
agent profile raw/full debug opened → existing full/debug endpoint only on demand
```

## If frontend currently has one generic context loader

Add a Zone Stalkers-specific loader:

```ts
async function loadZoneProjection(contextId: string, mode: 'game' | 'debug-map' | 'full') {
  return api.get(`/zone-stalkers/contexts/${contextId}/projection?mode=${mode}`)
}
```

Then update Zone Stalkers screens to call this instead of generic full context refresh.

## Acceptance

```text
[ ] After tick, normal UI does not fetch full state.
[ ] Network panel shows projection?mode=game for normal UI refresh.
[ ] Debug map uses projection?mode=debug-map.
[ ] Full state is only fetched by explicit debug/raw action.
```

---

# 4. Required change C — throttle frontend refresh at high tick speeds

## Problem

At x600 or fast auto-tick, frontend may process every WebSocket tick and issue too many refreshes.

## Required behavior

Add client-side throttle/debounce:

```text
normal UI refresh: max 2–4 per second
debug-map refresh: max 1–2 per second
```

Keep latest tick info in memory, but do not fetch/render on every tick if ticks arrive faster than UI can display.

## Suggested values

```ts
const GAME_PROJECTION_REFRESH_MS = 250;
const DEBUG_MAP_REFRESH_MS = 500;
```

## Acceptance

```text
[ ] x600 mode does not trigger one HTTP refresh per tick.
[ ] UI still shows recent world_turn.
[ ] Manual actions still force immediate refresh.
```

---

# 5. Problem B — CPU: hunt debug payload is built every tick

## Current issue

At the end of `tick_zone_map()`, the code builds:

```python
build_hunt_debug_payload(state=state, world_turn=state["world_turn"])
```

on every tick and stores it in:

```text
state.debug.hunt_search_by_agent
state.debug.location_hunt_traces
```

This can be expensive because it may scan agents, memories, beliefs and location traces.

This should not be done every tick by default.

---

# 6. Required change D — gate `build_hunt_debug_payload()`

## File

```text
backend/app/games/zone_stalkers/rules/tick_rules.py
```

## Required behavior

Do not build full hunt debug payload on every tick by default.

Use one of these approaches.

## Recommended approach

Build hunt debug payload only when debug map wants it.

Since tick code does not know whether the frontend debug map is open, use state flags:

```text
state["debug_hunt_traces_enabled"] = false by default
state["debug_hunt_traces_refresh_interval"] = 10 or 30 turns
```

Then in tick:

```python
if state.get("debug_hunt_traces_enabled"):
    interval = int(state.get("debug_hunt_traces_refresh_interval", 10))
    last_turn = int(state.get("_debug_hunt_traces_built_turn", -999999))
    if state["world_turn"] - last_turn >= interval:
        state.setdefault("debug", {}).update(build_hunt_debug_payload(...))
        state["_debug_hunt_traces_built_turn"] = state["world_turn"]
```

If disabled:

```text
do not build location_hunt_traces
do not scan all memory for hunt debug
```

## Manual refresh endpoint

Add endpoint:

```text
POST /zone-stalkers/debug/hunt-search/refresh/{context_id}
```

or:

```text
GET /zone-stalkers/debug/hunt-search/{context_id}
```

that:

```text
loads state
builds hunt debug payload
returns it
optionally stores it into state.debug if requested
```

This endpoint is used by debug map when needed.

## Simpler MVP

If adding endpoint is too much for this PR:

```text
keep debug_hunt_traces_enabled default true only for debug contexts,
but refresh every 10–30 turns instead of every turn.
```

However, preferred default is false for CPU safety.

## Acceptance

```text
[ ] build_hunt_debug_payload is not called every tick by default.
[ ] Debug map can still get hunt traces through endpoint or enabled debug flag.
[ ] Existing Hunt Traces UI still works when debug mode is enabled/refreshed.
```

---

# 7. Required change E — do not store heavy debug payload in normal state unless needed

## Problem

If `state.debug.location_hunt_traces` is stored permanently and saved every tick, state size grows and serialization cost increases.

## Required behavior

Treat hunt debug payload as derived debug data.

Recommended:

```text
- build on demand for debug endpoint;
- or store only bounded latest summary;
- never append unbounded debug traces into state.debug.
```

Existing builder already bounds output; keep those limits.

Also consider clearing stale debug payload when disabled:

```python
if not state.get("debug_hunt_traces_enabled"):
    state.get("debug", {}).pop("location_hunt_traces", None)
    state.get("debug", {}).pop("hunt_search_by_agent", None)
```

But only do this if frontend can fetch debug on demand.

## Acceptance

```text
[ ] Normal state does not keep large location_hunt_traces by default.
[ ] Debug map can explicitly request it.
```

---

# 8. Problem C — projections still use full deepcopy

## Current issue

`project_zone_state()` currently does:

```python
projected = copy.deepcopy(state)
```

then removes heavy fields with `pop`.

This is acceptable for manual diagnostics, but if frontend starts using projections frequently, this becomes a CPU bottleneck.

## Required change F — implement explicit game projection builder

## File

```text
backend/app/games/zone_stalkers/projections.py
```

Add explicit builder for `game` / `zone-lite` projection that does not deepcopy the whole state.

Example:

```python
def _project_agent_game(agent: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": agent.get("id"),
        "name": agent.get("name"),
        "archetype": agent.get("archetype"),
        "controller": agent.get("controller"),
        "location_id": agent.get("location_id"),
        "is_alive": agent.get("is_alive"),
        "has_left_zone": agent.get("has_left_zone"),
        "hp": agent.get("hp"),
        "hunger": agent.get("hunger"),
        "thirst": agent.get("thirst"),
        "sleepiness": agent.get("sleepiness"),
        "money": agent.get("money"),
        "global_goal": agent.get("global_goal"),
        "current_goal": agent.get("current_goal"),
        "scheduled_action": _compact_scheduled_action(agent.get("scheduled_action")),
        "active_plan_summary": _compact_active_plan(agent.get("active_plan_v3")),
        "equipment_summary": _compact_equipment(agent.get("equipment")),
        "inventory_summary": _compact_inventory(agent.get("inventory")),
    }
```

For state:

```python
def _project_zone_game(state: dict[str, Any]) -> dict[str, Any]:
    return {
        "context_type": state.get("context_type"),
        "world_turn": state.get("world_turn"),
        "world_day": state.get("world_day"),
        "world_hour": state.get("world_hour"),
        "world_minute": state.get("world_minute"),
        "game_over": state.get("game_over"),
        "emission_active": state.get("emission_active"),
        "emission_scheduled_turn": state.get("emission_scheduled_turn"),
        "agents": {id: _project_agent_game(a) for id, a in state.get("agents", {}).items()},
        "traders": {id: _project_trader_game(t) for id, t in state.get("traders", {}).items()},
        "locations": _project_locations_game(state.get("locations", {})),
    }
```

Do not include:

```text
memory
memory_v3
brain_trace
full brain_v3_context
full active_plan_v3
debug
location_hunt_traces
```

## Debug-map projection

For `debug-map`, explicit builder is also preferred, but if time is limited:

```text
- implement explicit game projection first;
- keep debug-map as deepcopy+strip temporarily.
```

## Acceptance

```text
[ ] game/zone-lite projection no longer deepcopies full state.
[ ] game projection contains enough data for normal UI.
[ ] game projection excludes heavy debug/memory/trace data.
[ ] tests verify heavy fields are absent.
```

---

# 9. Required change G — keep projection endpoint but make frontend use it

Backend already has:

```text
GET /zone-stalkers/contexts/{context_id}/projection?mode=game
GET /zone-stalkers/contexts/{context_id}/projection?mode=debug-map
GET /zone-stalkers/contexts/{context_id}/projection?mode=full
```

This PR should use these endpoints in frontend.

Do not add a separate massive API unless needed.

---

# 10. CPU quick wins that are acceptable in this PR

Do these only if they are straightforward and safe.

## 10.1. Memory decay interval

Current tick may run memory decay for every agent every tick.

Add interval:

```text
MEMORY_DECAY_INTERVAL_TURNS = 30 or 60
```

Run:

```python
if world_turn % MEMORY_DECAY_INTERVAL_TURNS == 0:
    decay_memory(agent, world_turn)
```

But still allow immediate memory import/migration if memory_v3 is empty.

Acceptance:

```text
[ ] Decisions still work.
[ ] Memory freshness in TargetBelief still uses created_turn/world_turn dynamically.
```

## 10.2. Location observation interval

If location observations are written/applied for every alive stalker every tick, throttle them.

Rules:

```text
write observation when location changed;
or every LOCATION_OBSERVATION_INTERVAL_TURNS;
or when visible content changed.
```

Suggested:

```text
LOCATION_OBSERVATION_INTERVAL_TURNS = 10
```

Acceptance:

```text
[ ] NPC still sees important location changes.
[ ] Memory spam decreases.
```

## 10.3. Brain trace gating

If brain_trace is written for every v3 NPC every tick, gate it:

```text
trace all only if debug_brain_trace_all = true
otherwise trace selected/debug agents only
or keep last N compact events
```

Do not remove trace functionality; just prevent unbounded always-on growth.

---

# 11. Do NOT implement in this intermediate PR

Avoid scope creep.

Do not implement:

```text
full WebSocket delta protocol
state revision conflict recovery
static/dynamic map split
large frontend state-store rewrite
copy-on-write tick state
pathfinding cache
decision invalidation scheduler
memory compaction/migration
database persistence redesign
```

Those are later optimization PRs.

---

# 12. Tests to add/update

## 12.1. WebSocket payload compactness

Add backend test for helper if easy:

```python
def test_compact_tick_payload_limits_new_events():
    ...
```

Expected:

```text
event_count == original event count
len(new_events_preview) <= WS_TICK_EVENT_PREVIEW_LIMIT
large nested payload fields are removed
```

## 12.2. Game projection explicit builder

Update:

```text
backend/tests/test_zone_stalkers_projections.py
```

Add/keep assertions:

```text
game projection has agents/traders/world time
game projection has active_plan_summary
game projection has inventory/equipment summaries
game projection has no memory/memory_v3/brain_trace/brain_v3_context/debug
```

## 12.3. Hunt debug gated

Add:

```python
def test_tick_does_not_build_hunt_debug_payload_when_disabled():
    ...
```

or a lower-level helper test if full tick test is difficult.

## 12.4. Frontend smoke

If frontend tests are not convenient, at least ensure:

```text
npm run build
```

passes after switching fetches to projection endpoint.

---

# 13. Acceptance criteria

This intermediate optimization PR is done when:

```text
[ ] WebSocket tick payload is compact:
    event_count + bounded preview, not unlimited new_events.

[ ] Frontend normal tick refresh uses:
    /zone-stalkers/contexts/{context_id}/projection?mode=game

[ ] Frontend debug map uses:
    /zone-stalkers/contexts/{context_id}/projection?mode=debug-map

[ ] Frontend refresh after tick is throttled.

[ ] build_hunt_debug_payload is not called every tick by default.

[ ] Hunt debug payload can still be requested/refreshed for debug map.

[ ] game/zone-lite projection avoids full deepcopy.

[ ] Normal game projection excludes:
    memory, memory_v3, brain_trace, full active_plan_v3,
    full brain_v3_context, debug, location_hunt_traces.

[ ] Existing PR6 hunt/search tests still pass.

[ ] Frontend build passes.
```

---

# 14. Expected result

After this PR:

```text
Network:
  normal UI no longer pulls full state every tick;
  WS tick event stays small;
  debug map gets bounded debug data only when needed.

CPU:
  no full hunt debug scan every tick by default;
  normal projection avoids full deepcopy;
  optional throttling reduces memory/observation churn.

PR6 gameplay:
  unchanged.
```

This should address the most visible performance pain without turning this into a large architecture rewrite.
