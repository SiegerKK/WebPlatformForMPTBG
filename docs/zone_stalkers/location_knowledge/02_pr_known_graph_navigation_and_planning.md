# PR 2 — Known-Graph Navigation and Planner Integration

## Dependency

Requires PR 1: `knowledge_v1.known_locations` with visited locations, known neighbor edges and revision tracking.

## Goal

NPC planning should stop treating the world map as fully known.

When an NPC plans travel, shelter search, trader search, artifact search, hunting, or exit-zone movement, it should use the NPC's **known location graph** unless the task is explicitly exploration.

---

# Core rule

The engine can know the true world graph.

NPC decision-making must use:

```python
agent["knowledge_v1"]["known_locations"]
```

for:
- known reachable locations;
- known neighbor edges;
- known shelters;
- known traders;
- known exits;
- known anomaly/artifact locations.

---

# Known graph API

Create module:

```text
backend/app/games/zone_stalkers/knowledge/known_graph.py
```

Required functions:

```python
def build_known_graph_view(agent: dict[str, Any]) -> KnownGraphView:
    ...

def is_location_known(agent: dict[str, Any], location_id: str) -> bool:
    ...

def is_location_visited(agent: dict[str, Any], location_id: str) -> bool:
    ...

def find_known_path(
    agent: dict[str, Any],
    *,
    start_location_id: str,
    target_location_id: str,
    max_nodes: int | None = None,
) -> list[str] | None:
    ...

def find_frontier_locations(
    agent: dict[str, Any],
    *,
    from_location_id: str,
    limit: int = 10,
) -> list[dict[str, Any]]:
    ...

def known_locations_with_feature(
    agent: dict[str, Any],
    feature: str,
    *,
    min_confidence: float = 0.4,
    include_stale: bool = False,
) -> list[dict[str, Any]]:
    ...
```

`KnownGraphView` should be compact and cacheable.

---

# Path cache

Expected scale:
- 300–600 known locations per NPC;
- 40 NPCs;
- many repeated plans.

Add per-agent path cache:

```python
agent["known_graph_path_cache"] = {
    "revision": known_locations_revision,
    "location_id": current_location_id,
    "paths": {
        "loc_A->loc_B": ["loc_A", "loc_X", "loc_B"]
    },
    "created_turn": world_turn,
    "hits": 0,
    "misses": 0,
}
```

Rules:
- invalidate on `known_locations_revision` change;
- invalidate on major graph update;
- do not cache full all-pairs paths;
- cache only recent start/target pairs;
- max 64–128 path entries per agent.

---

# Travel planning changes

When planner needs to travel to `target_location_id`:

1. If target known and reachable in known graph:
   ```text
   travel_to_location(target)
   ```

2. If target known but not reachable:
   ```text
   EXPLORE_ROUTE_TO_LOCATION or GATHER_LOCATION_INTEL
   ```

3. If target unknown but objective has a rumor/intel name:
   ```text
   GATHER_LOCATION_INTEL
   ```

4. If no target:
   ```text
   EXPLORE_FRONTIER
   ```

Do not silently route through unknown world edges.

---

# Frontier exploration

Add objective/intent:

```text
EXPLORE_FRONTIER
```

Purpose:
- visit a known neighbor whose contents are unknown;
- expand known graph;
- find traders/shelters/anomalies.

Candidate locations:
```text
known_exists but not visited
known edge from visited location
confidence >= threshold
not on cooldown
not too dangerous for current survival state
```

Scoring factors:
- distance in known graph;
- expected value;
- danger;
- need for shelter/trader/food;
- current global goal;
- stale/unknown count.

---

# Trader search

Current trader selection should use known traders only.

If no known trader:
```text
GATHER_LOCATION_INTEL or EXPLORE_FRONTIER
```

If trader location known but route unknown:
```text
EXPLORE_ROUTE_TO_LOCATION
```

If trader known and route known:
```text
travel_to_trader
```

---

# Shelter/emission

Emission logic must use known shelters first.

If known reachable shelter exists:
```text
REACH_SAFE_SHELTER
```

If shelter exists but route unknown:
```text
try nearest known frontier toward shelter rumor if time allows
```

If no known shelter:
```text
panic/explore nearest known indoor/frontier or stay if current location shelter
```

Always log:
```text
known_shelter_count
reachable_known_shelter_count
unknown_shelter_rumors
chosen_shelter_id
travel_ticks
turns_until_emission
```

---

# Artifact/get-rich

Artifact search should use:
- visited anomaly locations;
- known_snapshot anomaly locations;
- shared/trader-intel anomaly rumors;
- frontier exploration if no known candidates.

Avoid omniscient world anomaly list.

Add cooldown integration:
```python
known_locations[loc_id]["snapshot"]["search_exhausted_until"]
```

---

# Hunting and target leads

If target belief points to `loc_X`:

```text
if loc_X known and reachable:
    VERIFY_LEAD / TRACK_TARGET
elif loc_X known_exists but route unknown:
    EXPLORE_ROUTE_TO_LOCATION
elif loc_X unknown but learned by name/id from witness:
    GATHER_LOCATION_INTEL
else:
    GATHER_INTEL
```

Do not route to target location through unknown graph unless this objective explicitly means exploration.

---

# Exit-zone

If exits are not globally known:
- NPC must know at least one exit location or route.
- If global goal completed and no known exit:
  ```text
  GATHER_LOCATION_INTEL(exit) or ASK_TRADER_FOR_EXIT_ROUTE
  ```
- If known exit reachable:
  ```text
  LEAVE_ZONE
  ```

For early version, it is acceptable to seed each NPC with one known exit or spawn-route exit to prevent impossible games.

---

# Tests

Create:

```text
backend/tests/knowledge/test_known_graph_navigation.py
backend/tests/decision/v3/test_location_knowledge_planner.py
```

Required tests:

```python
def test_known_path_uses_only_known_edges(): ...
def test_unknown_world_shortcut_not_used_by_npc_planner(): ...
def test_known_exists_neighbor_can_be_selected_as_explore_frontier(): ...
def test_trader_search_uses_known_trader_not_global_trader_list(): ...
def test_no_known_trader_generates_gather_location_intel_or_frontier(): ...
def test_emission_uses_known_reachable_shelter(): ...
def test_hunt_target_known_location_unreachable_generates_route_exploration(): ...
def test_artifact_search_uses_known_anomaly_locations_only(): ...
def test_exit_zone_requires_known_exit_or_exit_intel(): ...
```

---

# Performance requirements

For 600 known locations per NPC:
- BFS/A* over known graph must not run more than needed.
- Cache path results by `(revision, start, target)`.
- Do not score all 600 locations every tick for every objective.
- Use precomputed indexes from PR 4 eventually; in this PR keep simple but bounded.

Initial budgets:
```text
max frontier candidates scored per decision = 20
max known trader candidates scored = 10
max known shelter candidates scored = 10 unless emission critical
max anomaly candidates scored = 20
```

---

# Acceptance criteria

```text
[ ] NPC cannot route through completely unknown locations.
[ ] NPC can explore known neighbor frontiers.
[ ] Trader/shelter/artifact search uses known_locations.
[ ] Hunting respects known graph reachability.
[ ] Path cache invalidates on known_locations revision.
[ ] No all-world scan in normal NPC decision path except debug/admin or explicit world generation.
```
