# Network and Debug Optimization

This document describes the implemented architecture for Zone Stalkers network traffic optimization and debug payload management. All features described here are live.

---

## Initial State Load

The frontend uses HTTP to load the initial state and WebSocket for incremental updates.

### Projection modes

```
GET /zone-stalkers/contexts/{context_id}/projection?mode={mode}
```

| Mode | Purpose |
|---|---|
| `game` | Normal game UI — compact agent/location data, no debug info |
| `debug-map-lite` | Debug map initial load — compact map topology + agent summaries |
| `debug-map` | Full debug map data |
| `full` | Complete state — manual/debug only |

The `game` and `debug-map-lite` projections use explicit builders that avoid `deepcopy` and exclude heavy sections (memory_v3, brain_trace, hunt debug) entirely.

---

## zone_delta WebSocket Protocol

After every tick, the backend sends a `zone_delta` message instead of the full state. The frontend applies it locally.

### Message shape

```json
{
  "type": "zone_delta",
  "match_id": "...",
  "context_id": "...",
  "base_revision": 123,
  "revision": 124,
  "world": {
    "world_turn": 1234,
    "world_day": 5,
    "world_hour": 12,
    "world_minute": 30,
    "emission_active": false
  },
  "changes": {
    "agents": {
      "agent_1": {
        "location_id": "loc_B",
        "hp": 85,
        "hunger": 42
      }
    },
    "locations": {
      "loc_A": {"agents": [...]},
      "loc_B": {"agents": [...]}
    },
    "traders": {}
  },
  "events": {
    "count": 3,
    "preview": [...]
  }
}
```

`events.preview` is bounded by `WS_EVENT_PREVIEW_LIMIT = 10`. If there are more events, `count` reflects the true total.

### Command flow

Commands for Zone Stalkers `zone_map` contexts also send `zone_delta` (not the generic `state_updated` message). If delta is not available, the fallback is `state_updated` with `requires_resync: true`.

---

## State Revision Tracking

All projections and `zone_delta` messages carry revision fields:

```json
{
  "state_revision": 124,
  "map_revision": 7
}
```

- `state_revision` — increments on every state change.
- `map_revision` — increments only when map topology changes.
- `base_revision` in `zone_delta` — the revision the delta is relative to.

### Resync protocol

When the frontend receives a `zone_delta` with `base_revision` that doesn't match its current revision, it triggers a full resync:

```
GET /projection?mode=game
```

The backend sets `requires_resync: true` in `state_updated` when it cannot produce a valid delta.

---

## Frontend Delta Application

The frontend applies `zone_delta` locally via `applyZoneDelta.ts`:

1. Verify `base_revision` matches local state revision.
2. Merge `changes.agents` into local agent map (patch, not replace).
3. Merge `changes.locations`.
4. Merge `changes.traders`.
5. Update world fields.
6. Append event previews.
7. Update stored revision to `revision`.

If merge fails or revision mismatches, trigger resync.

---

## Debug WebSocket Subscriptions

Clients can subscribe to debug data via WebSocket messages.

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

### zone_debug_delta message

After each tick where debug-relevant data changes, clients with active subscriptions receive:

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

Debug deltas are bounded and filtered by subscription scope. They do not include full memory, full brain_trace, or all locations.

---

## Hunt Debug Payload (On-Demand)

The hunt debug payload (`build_hunt_debug_payload`) is **not built on every tick**. It is gated by `debug_hunt_traces_enabled` and only produced on demand.

### `_debug_cache`

The state carries a `_debug_cache` structure with:

```python
_debug_cache = {
    "location_hunt_trace_index": dict[str, list],  # location_id → records
    "agent_hunt_search_cache": dict[str, dict],    # agent_id → TargetBelief summary
}
```

The index is incremental — only updated for locations touched in the current tick.

### Scoped hunt debug endpoints

| Endpoint | Purpose |
|---|---|
| `GET /zone-stalkers/debug/hunt-search/{context_id}` | Full on-demand hunt debug payload |
| `GET /zone-stalkers/debug/state-size/{context_id}` | State size diagnostics (manual, heavy) |

---

## Heavy Section Loading

The following data is never included in normal `zone_delta` or `game` projection:

```text
Full memory_v3 records
Full brain_trace events
Full active_plan_v3 steps
Hunt trace debug data
```

These are loaded on demand when the NPC profile panel is opened or a debug export is requested. The heavy NPC profile sections (memory, brain_trace, full active_plan) are fetched separately and not part of the regular tick data flow.

---

## Memory Decay

Memory decay runs on a scheduled interval (every 30–60 turns), not on every tick. This avoids repeated O(N·M) scans over all agents and memory records.

---

## Brain Trace Gating

Full brain trace output is gated by agent selection:

```json
{
  "debug_brain_trace_enabled": false,
  "debug_brain_trace_agent_ids": []
}
```

When disabled, only compact `latest_decision_summary` is kept per agent. Full trace is only stored for the selected/debug agent IDs, bounded to a maximum of ~200 events per agent.

---

## Game Projection Builder

The `game` projection mode uses an explicit builder function that constructs agent/location compact representations without calling `deepcopy` on the full state. Only the fields needed for the game UI are included.

The `zone-lite` projection follows the same pattern.
