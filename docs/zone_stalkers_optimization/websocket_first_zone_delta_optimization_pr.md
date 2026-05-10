# WebSocket-First Optimization PR — Zone Stalkers Delta Sync

> Goal:
>
> ```text
> Minimize network traffic by moving Zone Stalkers runtime updates from repeated HTTP full/projection refreshes to WebSocket deltas.
> ```
>
> This PR should start **after PR6 is closed**.
>
> PR6 introduced:
>
> ```text
> projection endpoint
> game/debug-map/full projection modes
> lightweight tick metrics
> Hunt Traces debug map
> ```
>
> This PR uses that groundwork and changes the data flow:
>
> ```text
> initial snapshot over HTTP
> → incremental updates over WebSocket
> → occasional resync only when revisions mismatch
> ```

---

# 1. Why this PR is needed

The most expensive traffic pattern is:

```text
tick happens
→ WebSocket says "ticked"
→ frontend fetches full state or large projection
→ repeat every tick
```

Even if WebSocket payload is small, traffic is still high if the frontend reacts by repeatedly fetching state.

Target architecture:

```text
1. Frontend loads initial snapshot:
   GET /zone-stalkers/contexts/{context_id}/projection?mode=game

2. Backend sends WebSocket delta after every tick:
   changed agents
   changed locations
   world time
   compact events

3. Frontend applies delta locally.

4. If frontend detects revision mismatch:
   fetch fresh snapshot.
```

This makes normal ticks cheap.

---

# 2. Scope of this PR

## In scope

```text
- Add state revision/version to Zone Stalkers state.
- Add WebSocket delta payload for tick updates.
- Send compact deltas instead of relying on HTTP refresh after every tick.
- Frontend local state applies deltas.
- Frontend no longer fetches full/projection state after every tick.
- Add resync logic when revision mismatch occurs.
- Keep HTTP projection endpoints for initial load/manual resync.
- Keep debug-map projection for debug map open/resync.
```

## Out of scope

Do not implement in this PR:

```text
- static/dynamic map split;
- full copy-on-write backend state;
- pathfinding cache;
- advanced CPU decision scheduler;
- database persistence redesign;
- full event sourcing;
- multiplayer conflict resolution;
- complete frontend store rewrite unless necessary.
```

This PR is specifically about traffic reduction through WebSocket deltas.

---

# 3. Current assumptions

Backend currently has:

```text
GET /zone-stalkers/contexts/{context_id}/projection?mode=game
GET /zone-stalkers/contexts/{context_id}/projection?mode=debug-map
GET /zone-stalkers/contexts/{context_id}/projection?mode=full
```

Current WebSocket tick payload is conceptually:

```json
{
  "type": "ticked",
  "match_id": "...",
  "world_turn": 123,
  "world_hour": 12,
  "world_day": 1,
  "world_minute": 34,
  "new_events": [...]
}
```

This PR should evolve it into:

```json
{
  "type": "zone_delta",
  "match_id": "...",
  "context_id": "...",
  "base_revision": 41,
  "revision": 42,
  "world": {
    "world_turn": 123,
    "world_day": 1,
    "world_hour": 12,
    "world_minute": 34
  },
  "changes": {
    "agents": { "...": { ...patch... } },
    "locations": { "...": { ...patch... } },
    "traders": { "...": { ...patch... } },
    "debug": { ...optional... }
  },
  "events": {
    "count": 8,
    "preview": [...]
  }
}
```

---

# 4. State revision model

## 4.1. Add revisions to state

Add to Zone Stalkers state:

```json
{
  "state_revision": 1,
  "map_revision": 1
}
```

Meaning:

```text
state_revision:
  increments every tick or state mutation that changes dynamic state.

map_revision:
  increments only when static map topology/layout/location definitions change.
```

For this PR, `state_revision` is required. `map_revision` can be added now but used later.

## 4.2. Increment state_revision

In `tick_zone_map()` or immediately after successful tick state update:

```python
state["state_revision"] = int(state.get("state_revision", 0)) + 1
```

Also increment after user commands that mutate state.

If command pipeline already has generic revision logic, use it. Otherwise add Zone-specific revision.

## 4.3. Include revision in projections

All projection responses should include:

```json
{
  "state_revision": 42,
  "map_revision": 1
}
```

At minimum, ensure these fields remain in projected state.

---

# 5. Delta generation strategy

## 5.1. Minimal practical approach

Do not implement generic JSON Patch for the whole state in the first version.

Use domain-specific compact delta:

```text
world time changes
agent patches
location patches
trader patches
event preview
debug patch only if debug mode/subscription enabled
```

This is simpler and safer.

## 5.2. Compare previous and next state

In `tick_match()` / ruleset tick flow, after tick we need both:

```text
old_state
new_state
```

If existing ruleset only returns `new_state`, add delta calculation in Zone Stalkers ruleset where both are available.

Suggested location:

```text
backend/app/games/zone_stalkers/rules/tick_rules.py
backend/app/games/zone_stalkers/delta.py
```

Add file:

```text
backend/app/games/zone_stalkers/delta.py
```

API:

```python
def build_zone_delta(
    *,
    old_state: dict[str, Any],
    new_state: dict[str, Any],
    mode: Literal["game", "debug-map"] = "game",
) -> dict[str, Any]:
    ...
```

Return:

```python
{
    "base_revision": old_state.get("state_revision"),
    "revision": new_state.get("state_revision"),
    "world": {...},
    "changes": {...},
    "events": {...},
}
```

---

# 6. Agent delta contract

## 6.1. Hot fields to include

For each changed agent:

```json
{
  "location_id": "loc_A",
  "is_alive": true,
  "has_left_zone": false,
  "hp": 91,
  "hunger": 20,
  "thirst": 40,
  "sleepiness": 18,
  "money": 1000,
  "current_goal": "kill_stalker",
  "global_goal": "kill_stalker",
  "scheduled_action": {
    "type": "travel",
    "turns_remaining": 3,
    "target_id": "loc_B"
  },
  "active_plan_summary": {
    "objective_key": "VERIFY_LEAD",
    "status": "running",
    "current_step_kind": "travel_to_location",
    "current_step_index": 0,
    "steps_count": 4
  }
}
```

Only include fields that changed.

## 6.2. Do not include heavy fields

Never include in normal game delta:

```text
memory
memory_v3
brain_trace
active_plan_v3 full
brain_v3_context full
debug
location_hunt_traces
inventory full unless explicitly changed and needed
```

## 6.3. Inventory/equipment policy

For the first delta PR:

```text
- include inventory_summary if inventory changed;
- include equipment_summary if equipment changed;
- do not include full inventory by default.
```

Full inventory remains agent profile detail data.

---

# 7. Location delta contract

For each changed location:

```json
{
  "agents": ["agent_1", "agent_2"],
  "items_count": 3,
  "artifacts_count": 1,
  "known_event_marker": "optional"
}
```

Avoid sending huge location objects.

Recommended hot location fields:

```text
agents
artifacts count / compact list if small
items count / compact list if small
active event ids
danger marker / emission marker
```

For this PR, if location data is too complex, include only:

```text
location_id
agent_ids
artifact_count
item_count
```

---

# 8. Event delta contract

Do not send unlimited `new_events`.

Use:

```json
"events": {
  "count": 17,
  "preview": [
    {
      "event_type": "travel_arrived",
      "agent_id": "agent_debug_1",
      "location_id": "loc_A",
      "summary": "Убийца 1 прибыл в Южный хутор"
    }
  ]
}
```

Limit:

```text
WS_EVENT_PREVIEW_LIMIT = 10
```

If the frontend needs full event history, add a separate endpoint later.

---

# 9. Debug-map delta contract

Debug map has extra needs, but it must not receive full debug every tick.

For this PR:

```text
normal subscription:
  zone_delta mode=game

debug map open:
  client sends subscribe_debug_map
  server sends zone_delta mode=debug-map at throttled rate
```

## 9.1. Simple debug subscription model

Client sends:

```json
{
  "type": "subscribe_zone_debug",
  "match_id": "...",
  "context_id": "...",
  "mode": "debug-map",
  "hunter_id": "optional",
  "target_id": "optional"
}
```

Client sends unsubscribe when leaving debug map:

```json
{
  "type": "unsubscribe_zone_debug",
  "match_id": "...",
  "context_id": "..."
}
```

If current WebSocket manager does not support client messages yet, use simpler frontend behavior:

```text
- normal game uses WS delta;
- debug map refreshes projection?mode=debug-map on throttle.
```

But the long-term target is debug subscription.

## 9.2. Debug delta fields

Only include bounded updates:

```json
"debug": {
  "hunt_search_by_agent": {
    "agent_debug_1": {
      "target_id": "agent_debug_0",
      "best_location_id": "loc_S4",
      "best_location_confidence": 0.72,
      "lead_count": 15
    }
  },
  "location_hunt_traces_delta": {
    "loc_S4": {
      "positive_leads_added": [...],
      "negative_leads_added": [...]
    }
  }
}
```

Do not send full `location_hunt_traces` every tick.

For MVP, debug map may still use HTTP `projection?mode=debug-map` on throttle, while normal UI uses WebSocket delta.

---

# 10. Backend implementation tasks

## 10.1. Add delta builder

File:

```text
backend/app/games/zone_stalkers/delta.py
```

Functions:

```python
def compact_agent_for_delta(agent: dict[str, Any]) -> dict[str, Any]:
    ...

def compact_location_for_delta(location: dict[str, Any]) -> dict[str, Any]:
    ...

def diff_dict(old: dict[str, Any], new: dict[str, Any], keys: Iterable[str]) -> dict[str, Any]:
    ...

def build_zone_delta(
    *,
    old_state: dict[str, Any],
    new_state: dict[str, Any],
    events: list[dict[str, Any]],
    mode: Literal["game", "debug-map"] = "game",
) -> dict[str, Any]:
    ...
```

## 10.2. Store old state for delta

The ruleset tick flow needs old and new state.

Possible implementation:

```python
old_state = load_context_state(...)
new_state, events = tick_zone_map(old_state)
new_state["state_revision"] = old_state.get("state_revision", 0) + 1
delta = build_zone_delta(old_state=old_state, new_state=new_state, events=events)
save_context_state(...)
return {
    ...,
    "zone_delta": delta,
}
```

If existing ruleset is not easy to modify, attach delta to tick result at the Zone Stalkers ruleset level.

## 10.3. Send `zone_delta` through WebSocket

In `tick_match()`:

```python
zone_delta = result.get("zone_delta")
if zone_delta:
    ws_payload = {
        "type": "zone_delta",
        "match_id": match_id_str,
        **zone_delta,
    }
else:
    fallback to compact "ticked"
```

Do not include full `new_events`.

## 10.4. Keep fallback

If delta generation fails:

```text
send compact "ticked" message with requires_resync=true
```

Example:

```json
{
  "type": "ticked",
  "match_id": "...",
  "world_turn": 123,
  "requires_resync": true
}
```

Frontend then fetches `projection?mode=game`.

---

# 11. Frontend implementation tasks

## 11.1. Initial load

On Zone Stalkers screen load:

```text
GET /zone-stalkers/contexts/{context_id}/projection?mode=game
```

Store:

```text
zoneState
stateRevision
```

## 11.2. Apply WebSocket delta

When message type is `zone_delta`:

```ts
if (delta.base_revision !== currentState.state_revision) {
  fetch projection?mode=game
  return
}

applyZoneDelta(currentState, delta)
currentState.state_revision = delta.revision
```

## 11.3. Delta reducer

Add:

```text
frontend/src/games/zone_stalkers/state/applyZoneDelta.ts
```

Pseudo:

```ts
export function applyZoneDelta(state: ZoneState, delta: ZoneDelta): ZoneState {
  return {
    ...state,
    ...delta.world,
    state_revision: delta.revision,
    agents: applyEntityPatches(state.agents, delta.changes.agents),
    locations: applyEntityPatches(state.locations, delta.changes.locations),
    traders: applyEntityPatches(state.traders, delta.changes.traders),
  };
}
```

Support deletion if needed:

```json
{ "_deleted": true }
```

Probably not needed in MVP except departed/dead agents can be represented by fields.

## 11.4. No full refetch after every tick

Remove behavior:

```text
on ticked/zone_delta → reload full context
```

Replace with:

```text
on zone_delta → apply locally
on requires_resync/mismatch → fetch projection
```

## 11.5. Debug map behavior

MVP:

```text
debug map initially loads projection?mode=debug-map
on zone_delta:
  apply game-level delta
debug-specific Hunt Traces:
  refresh projection?mode=debug-map on throttle
```

Throttle:

```ts
DEBUG_MAP_RESYNC_MS = 1000
```

Later PR can add true debug deltas.

---

# 12. Resync protocol

## 12.1. Revision mismatch

If frontend has:

```text
current_revision = 42
```

and receives:

```text
base_revision = 43
```

then it missed a delta.

Frontend must:

```text
fetch projection?mode=game
replace local state
```

## 12.2. Manual resync

Add a manual button or debug command:

```text
Resync state
```

Useful during development.

## 12.3. Backend fallback

If backend cannot build delta:

```json
{
  "type": "zone_delta_unavailable",
  "match_id": "...",
  "context_id": "...",
  "revision": 44,
  "requires_resync": true
}
```

---

# 13. CPU considerations

## 13.1. Avoid full projection per tick

Do not build `projection?mode=game` on backend every tick to send over WebSocket.

Delta should be built from compact changed entities.

## 13.2. Avoid full deepcopy in delta builder

Do not call:

```python
project_zone_state(state, mode="game")
```

inside every tick delta builder.

Instead compare compact fields per entity.

## 13.3. Keep hunt debug out of normal delta

Do not call `build_hunt_debug_payload()` only to build normal `zone_delta`.

Normal delta should not need hunt traces.

---

# 14. Tests

## 14.1. Backend delta tests

Add:

```text
backend/tests/test_zone_stalkers_delta.py
```

Tests:

```python
def test_zone_delta_includes_changed_agent_location():
    ...

def test_zone_delta_does_not_include_memory_or_brain_trace():
    ...

def test_zone_delta_includes_world_time():
    ...

def test_zone_delta_limits_event_preview():
    ...

def test_zone_delta_revision_fields():
    ...
```

## 14.2. Frontend reducer tests

If frontend tests exist:

```text
frontend/src/games/zone_stalkers/state/applyZoneDelta.test.ts
```

Tests:

```text
applies agent patch
applies location patch
updates world time
detects revision mismatch
does not erase unchanged entities
```

If no frontend tests exist, add a lightweight pure TS test or manual checklist.

---

# 15. Manual acceptance checklist

Run auto tick in Zone Stalkers.

Verify in browser Network tab:

```text
[ ] Initial load uses projection?mode=game.
[ ] On tick, no full context/state request is made.
[ ] WebSocket receives zone_delta messages.
[ ] zone_delta messages are small.
[ ] UI updates agent locations/needs/world time from deltas.
[ ] If page is left idle for many ticks, resync still works.
[ ] Debug map can still load Hunt Traces with projection?mode=debug-map.
[ ] Opening NPC profile can still fetch/receive detailed data as needed.
```

---

# 16. Acceptance criteria

This PR is complete when:

```text
[ ] Zone Stalkers state has state_revision.
[ ] Initial frontend load uses game projection.
[ ] Backend builds domain-specific zone_delta after tick.
[ ] WebSocket sends zone_delta instead of ticked+large events.
[ ] Frontend applies zone_delta locally.
[ ] Frontend does not refetch full state after every tick.
[ ] Revision mismatch triggers projection resync.
[ ] Normal zone_delta excludes memory/memory_v3/brain_trace/debug/hunt traces.
[ ] Event preview is bounded.
[ ] Debug map remains functional via debug-map projection/resync.
[ ] Existing PR6 hunt/search tests pass.
[ ] Frontend build passes.
```

---

# 17. Expected result

Before:

```text
tick
→ WS "ticked"
→ frontend fetches large state/projection
→ repeat
```

After:

```text
initial projection snapshot
→ WS zone_delta
→ frontend applies local patch
→ no HTTP request per tick
```

Expected network reduction:

```text
normal tick traffic becomes proportional to changed entities,
not proportional to whole world state.
```

This is the main traffic optimization milestone.
