# Debug Profile and Map

This document describes the NPC debug UI panels, JSON export formats, the hunt trace debug map overlay, and the `zone_debug_delta` WebSocket subscription used for live debug updates.

---

## NPC Profile Panels

The NPC profile modal is opened per agent and displays multiple panels, each covering a different aspect of the NPC's current state.

### NpcBrainPanel

Shows the core decision output for the current tick:

- Current objective (`objective_key`, score, reason, urgency)
- Adapter intent kind
- Active plan summary (`objective_key`, status, current step kind, step index, steps count)
- Current runtime step (`scheduled_action` compact view)
- Memory used (records that drove this decision)
- Decision trace event (uses `decision="objective_decision"`)

### ObjectiveRankingPanel

Shows the full scored objective ranking for the last decision cycle:

- All generated objectives sorted by score
- Per-objective: key, score, urgency, blockers, source, reason

### MemoryUsedPanel

Shows the memory records that contributed to the current decision:

- Record kind, layer, location, entity references
- Retrieval score, confidence, freshness
- Source refs

For hunt objectives, this panel prioritizes:
- `target_intel`, `target_last_known_location`, `target_seen`
- `target_not_found`, `target_moved`, `target_route_observed`

### RuntimeActionPanel

Shows the currently executing `scheduled_action`:

- Type (travel, sleep, explore, wait, trade, search_target, etc.)
- Progress / turns remaining
- Target (location, trader, etc.)
- `active_plan_id`, `active_plan_step_index`, `active_plan_objective_key`

### MemoryTimeline

Shows the recent history of memory_v3 writes:

- Record kind, turn, layer, location, confidence
- Grouped by turn or event

### Needs and Constraints Panel

Shows current survival state:

- `hunger`, `thirst`, `sleepiness`, `hp`, `radiation`
- `combat_readiness` summary
- Liquidity classification for inventory items
- `ImmediateNeed` / `ItemNeed` detail

### Hunt Search Panel

Shows the current `TargetBelief` for hunt agents:

- `target_id`, `best_location_id`, `best_location_confidence`
- `possible_locations` list (location, probability, reason, freshness)
- `likely_routes`
- `exhausted_locations`
- `lead_count`, `source_refs`

---

## Full Debug JSON Export

The full debug export contains the complete NPC runtime state as structured JSON:

```json
{
  "agent_id": "...",
  "name": "...",
  "brain_v3_context": {
    "objective_decision": {...},
    "objective_ranking": [...],
    "adapter_intent": {...},
    "active_plan_v3": {...},
    "brain_trace": {...}
  },
  "memory_v3": {...},
  "memory": [...],
  "needs": {...},
  "inventory": [...],
  "equipment": {...},
  "hunt_search": {...}
}
```

Heavy sections (full `memory_v3`, full `brain_trace`, raw debug) are collapsed by default and loaded on demand when the panel is opened or the export button is pressed.

---

## NPC Story JSON Export (Compact)

The compact NPC history export produces a human-readable story format:

```json
{
  "agent_id": "...",
  "name": "...",
  "latest_event": {...},
  "latest_decision": {
    "objective_key": "TRACK_TARGET",
    "score": 0.83,
    "reason": "target_last_known_location known"
  },
  "current_objective": "TRACK_TARGET",
  "current_runtime": "travel_to_location",
  "hunt_search": {
    "target_id": "...",
    "best_location_id": "...",
    "best_location_confidence": 0.72,
    "possible_locations": [...],
    "exhausted_locations": [...],
    "lead_count": 7
  }
}
```

The compact export must distinguish between `latest_event`, `latest_decision`, `current_objective`, and `current_runtime`.

---

## Location Hunt Traces

The hunt trace overlay shows per-location hunt activity on the debug map. This is derived from the `location_hunt_trace_index` in the debug cache.

Each location entry shows:
- Hunt-relevant memory events (kind, turn, confidence, hunter/target refs)
- Positive leads (target_seen, target_intel, target_last_known_location)
- Negative leads (target_not_found, no_witnesses, witness_source_exhausted)
- Route changes (target_moved, target_route_observed)

The index is bounded (max ~100 records per location) and built on demand, not on every tick. See the optimization docs for how it is gated and served.

---

## Debug Map Overlay Modes

The debug map supports different overlay modes controlled by the frontend debug subscription:

### `debug-map-lite` projection

Initial load for debug map uses `GET /zone-stalkers/contexts/{context_id}/projection?mode=debug-map-lite`. This contains:
- Location agent lists and counts
- Static map topology
- Compact agent summaries (location, status, current objective)

Heavy hunt trace data is not included in this projection.

### hunt_search_by_agent

Per-hunter target search summary available via:
- WebSocket `zone_debug_delta` (when debug subscription is active)
- `GET /zone-stalkers/debug/hunt-search/{context_id}` (on-demand endpoint)

Shows best location, confidence, possible leads, and exhausted locations per hunting agent.

### location_hunt_traces

Per-location hunt trace data available via:
- WebSocket `zone_debug_delta` (filtered to visible/selected locations)
- `GET /zone-stalkers/debug/hunt-search/locations/{location_id}` (on-demand)

---

## zone_debug_delta

The `zone_debug_delta` WebSocket message carries debug-specific updates for clients with an active debug subscription.

### Subscribe

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

### Unsubscribe

```json
{
  "type": "unsubscribe_zone_debug",
  "match_id": "...",
  "context_id": "..."
}
```

### Message shape

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
    "hunt_search_by_agent": {...},
    "location_hunt_traces": {...},
    "agent_brain_summary": {},
    "selected_agent_profile_summary": {},
    "selected_location_summary": {}
  }
}
```

Debug deltas are bounded and filtered by subscription scope. They do not contain full memory, full brain_trace, or all locations.

---

## debug-map-lite and Scoped Endpoints

| Endpoint | Purpose |
|---|---|
| `GET /projection?mode=game` | Normal game UI initial load — compact agent/location data |
| `GET /projection?mode=debug-map-lite` | Debug map initial load — compact map + agent data |
| `GET /projection?mode=full` | Full state — manual/debug only |
| `GET /debug/state-size/{context_id}` | Heavy state size diagnostics — manual only |
| `GET /debug/hunt-search/{context_id}` | On-demand full hunt search payload |
| `GET /debug/hunt-search/locations/{location_id}` | On-demand hunt traces for one location |
