# Hunt Search and Traces

This document describes how NPC hunters search for targets using probabilistic leads, how failed searches suppress stale locations, and how hunt activity is exposed in the debug map.

---

## HuntLead Model

`HuntLead` is the fundamental unit of target knowledge. Each lead represents one piece of information about where a target may be, where it moved, what it did, or what it is likely to do next.

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
    source_agent_id: str | None   # source witness/trader, not the hunter
    expires_turn: int | None
    details: Mapping[str, Any]
```

### Lead kinds

```
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

### Confidence values

| Lead kind | Starting confidence |
|---|---|
| `target_seen` | 0.95 |
| `target_last_known_location` | 0.85 |
| `target_intel` from trader | 0.70 |
| `target_intel` from stalker | 0.55 |
| `target_moved` | 0.80 |
| `target_route_observed` | 0.65 |
| `target_not_found` | −0.75 for that location |
| `combat_noise` | 0.35 |

### Freshness decay

```
freshness = max(0.0, 1.0 - age_turns / decay_window)
```

Decay windows by kind:

| Kind | Window (turns) |
|---|---|
| `target_seen` | 600 |
| `target_intel` from trader | 900 |
| `target_route_observed` | 300 |
| `combat_noise` | 120 |
| `target_not_found` (negative evidence) | 1200 |

### source_agent_id

For `target_intel` leads, `source_agent_id` is the witness (trader/stalker who provided the intel), not the hunter. It is resolved from `details.source_agent_id` / `details.witness_id` / `details.trader_id`.

---

## TargetBelief

`TargetBelief` is built by `build_target_belief()` and passed into objective generation. It aggregates all available leads into a probabilistic view of where the target is.

### Fields

```python
possible_locations: tuple[LocationHypothesis, ...]
likely_routes: tuple[RouteHypothesis, ...]
best_location_id: str | None
best_location_confidence: float
exhausted_locations: tuple[str, ...]
lead_count: int

# Backwards-compatible fields (derived from best hypothesis)
last_known_location_id: str | None
location_confidence: float
```

### LocationHypothesis

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

### Build process

`build_target_belief()` does the following:

1. Reads memory_v3 records for `target_id`.
2. Canonicalizes `intel_from_trader` / `intel_from_stalker` → `target_intel`.
3. Converts relevant records into `HuntLead`.
4. Applies freshness decay.
5. Aggregates location hypotheses.
6. Builds route hypotheses from `target_moved` / `target_route_observed`.
7. Suppresses locations with `target_not_found` using staged confidence multipliers.
8. Computes `exhausted_locations` after repeated failed searches.
9. Avoids omniscient target location unless target is visible/co-located or `debug_omniscient_targets` is enabled.

---

## target_not_found Staged Suppression

When `search_target` fails, `target_not_found` is written with `failed_search_count` and `cooldown_until_turn` details.

Staged confidence multipliers applied per location:

| Failure count | Effect |
|---|---|
| 1 miss | `confidence *= 0.45` — hypothesis weakened but survives |
| 2 misses | `confidence *= 0.20` — hypothesis stale/low |
| 3 misses | `confidence = 0`, location added to `exhausted_locations` |

A single `target_not_found` does not delete the only lead. It takes repeated failures before a location is fully exhausted.

### Stale memory

Records with status `stale` still contribute as weak leads:
```
stale record: confidence *= 0.35, freshness *= 0.35
archived record: ignored
```

---

## witness_source_exhausted

When `question_witnesses` finds no witnesses, `witness_source_exhausted` is written with a cooldown. This prevents the hunter from immediately retrying the same no-witness location.

---

## Search Actions

### search_target

Searches the current location for the target.

Outcomes:
- `target_seen` — target found, high confidence hypothesis promoted.
- `target_not_found` — staged suppression applied to this location.

### look_for_tracks

Searches for movement clues in the current location.

Sources consulted (in order):
1. Existing memory_v3 route leads from the current location.
2. Current location traces.
3. Recent `target_moved` / `target_route_observed` records.
4. `debug_omniscient_targets` flag (test/debug mode only).

Outcomes:
- `target_route_observed` / `target_moved` — adds route hypothesis.
- `no_tracks_found` — no useful leads found.

`look_for_tracks` does not directly read `state["agents"][target_id]["location_id"]` unless `debug_omniscient_targets` is enabled.

### question_witnesses

Asks co-located non-trader stalkers for intel about the target.

Outcomes:
- `target_intel` — witness provided location intel.
- `target_last_known_location` — witness saw the target recently.
- `target_rumor_unreliable` — vague/low-confidence report.
- `no_witnesses` — no co-located agents with useful knowledge.

`question_witnesses` is distinct from `ask_for_intel` (which is trader-specific paid intel).

---

## Objective Generation from Leads

| Belief state | Generated objectives |
|---|---|
| No useful leads | `GATHER_INTEL`, `LOCATE_TARGET` |
| One or more `possible_locations` | `VERIFY_LEAD`, `TRACK_TARGET` for best location |
| Route hypothesis exists | `INTERCEPT_TARGET` (placeholder unless full tactics implemented) |
| Best location is exhausted | `GATHER_INTEL`, `VERIFY_LEAD` next-best, `LOOK_FOR_TRACKS` |

`TRACK_TARGET` always chooses the best non-exhausted location.

### ActivePlan composition

**VERIFY_LEAD:**
```
travel_to_location → search_target → look_for_tracks → question_witnesses
```

**TRACK_TARGET:**
```
travel_to_location → search_target → look_for_tracks
```

**GATHER_INTEL:**
```
travel_to_intel_source → ask_for_intel (trader)
or: question_witnesses (co-located stalkers)
```

---

## target_found → ENGAGE_TARGET

When `search_target` writes `target_seen`, `VERIFY_LEAD` / `TRACK_TARGET` / `PURSUE_TARGET` stop immediately. The next tick selects `ENGAGE_TARGET` using the `recently_seen` flag on `TargetBelief`.

---

## Hunt Traces Debug Map

### hunt_search_by_agent

Per-hunter summary of current target search state:

```json
"hunt_search_by_agent": {
  "agent_debug_1": {
    "target_id": "agent_debug_0",
    "best_location_id": "loc_S4",
    "best_location_confidence": 0.72,
    "possible_locations": [
      {
        "location_id": "loc_S4",
        "probability": 0.62,
        "reason": "target_seen",
        "freshness": 0.85
      }
    ],
    "exhausted_locations": ["loc_B"],
    "lead_count": 8
  }
}
```

### location_hunt_traces

Per-location index of hunt-relevant memory events:

```json
"location_hunt_traces": {
  "loc_S4": {
    "records": [
      {
        "id": "memory:...",
        "kind": "target_seen",
        "hunter_id": "agent_debug_1",
        "target_id": "agent_debug_0",
        "turn": 1230,
        "confidence": 0.95,
        "source_ref": "memory:..."
      }
    ],
    "revision": 42
  }
}
```

Hunt-relevant record kinds indexed:
```
target_seen, target_last_known_location, target_intel,
intel_from_stalker, intel_from_trader,
target_not_found, target_location_exhausted,
witness_source_exhausted, no_tracks_found, no_witnesses,
target_moved, target_route_observed, target_wounded,
target_combat_noise, target_death_confirmed,
hunt_failed, combat_initiated, combat_resolved
```

The hunt trace index is built on demand (not on every tick by default). See [`../optimization/network_and_debug_optimization.md`](../optimization/network_and_debug_optimization.md) for how debug data is requested and served.
