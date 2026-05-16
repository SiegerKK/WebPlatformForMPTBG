# PR 1 — Location Knowledge Model and Visit/Neighbor Updates

## Goal

Add `knowledge_v1.known_locations` as a compact structured table.

NPCs should initially know only their spawn/current location and immediate neighboring location identities. When they personally visit a location, they store a compact snapshot of that location at that time and learn that its neighbors exist.

This PR does **not** change pathfinding/planner behavior yet, except where passive knowledge updates are safe.

---

# Data model

Add or normalize:

```python
agent["knowledge_v1"]["known_locations"] = {
    "loc_A": {
        "location_id": "loc_A",
        "knowledge_level": "visited",
        "known_exists": True,
        "visited": True,
        "visit_count": 2,

        "first_known_turn": 100,
        "last_confirmed_turn": 420,
        "last_visited_turn": 420,
        "observed_turn": 420,
        "received_turn": 420,

        "source": "direct_visit",
        "source_agent_id": None,
        "confidence": 1.0,
        "stale_after_turn": 1860,

        "snapshot": {
            "name": "Old Bunker",
            "location_type": "bunker",
            "danger_level_estimate": 0.2,
            "has_trader": True,
            "known_trader_id": "trader_1",
            "has_shelter": True,
            "has_exit": False,
            "artifact_potential_estimate": 0.0,
            "anomaly_risk_estimate": 0.1,
            "last_artifact_seen_turn": None,
            "last_searched_turn": None,
            "search_exhausted_until": None,
            "known_neighbor_ids": ["loc_B", "loc_C"],
        },

        "edges": {
            "loc_B": {
                "target_location_id": "loc_B",
                "known_exists": True,
                "confirmed": True,
                "source": "direct_visit_neighbor",
                "observed_turn": 420,
                "confidence": 0.95,
                "travel_cost_estimate": 1,
            }
        },

        "stats": {
            "times_shared_out": 0,
            "times_received": 0,
            "last_used_for_path_turn": None,
        }
    }
}
```

## Knowledge levels

Use constants:

```python
LOCATION_KNOWLEDGE_UNKNOWN = "unknown"
LOCATION_KNOWLEDGE_EXISTS = "known_exists"
LOCATION_KNOWLEDGE_ROUTE_ONLY = "known_route_only"
LOCATION_KNOWLEDGE_SNAPSHOT = "known_snapshot"
LOCATION_KNOWLEDGE_VISITED = "visited"
```

Rules:
- no entry means `unknown`;
- neighbor discovered from current location means `known_exists`;
- route fragment from hearsay means `known_route_only`;
- hearsay with content means `known_snapshot`;
- direct visit means `visited`.

---

# Compact snapshot rules

Add helper:

```python
def build_location_knowledge_snapshot(
    *,
    state: dict[str, Any],
    location_id: str,
    world_turn: int,
) -> dict[str, Any]:
    ...
```

Snapshot must be small and immutable-ish.

Include:
- stable/static location fields;
- known gameplay facts;
- estimates.

Do not include:
- full `state["locations"][location_id]`;
- current list of all agents there;
- current full item lists;
- mutable nested references.

If location contains current agents/items and we need that knowledge later, it should go through existing `known_npcs`, item/artifact knowledge, or separate compact fields.

---

# Helpers

Create module:

```text
backend/app/games/zone_stalkers/knowledge/location_knowledge.py
```

Required functions:

```python
def ensure_location_knowledge_v1(agent: dict[str, Any]) -> dict[str, Any]:
    ...

def get_known_location(
    agent: dict[str, Any],
    location_id: str,
) -> dict[str, Any] | None:
    ...

def upsert_known_location(
    agent: dict[str, Any],
    *,
    location_id: str,
    world_turn: int,
    knowledge_level: str,
    source: str,
    confidence: float,
    source_agent_id: str | None = None,
    snapshot: dict[str, Any] | None = None,
    edges: dict[str, Any] | None = None,
    observed_turn: int | None = None,
    received_turn: int | None = None,
) -> dict[str, Any]:
    ...

def mark_location_visited(
    agent: dict[str, Any],
    *,
    state: dict[str, Any],
    location_id: str,
    world_turn: int,
) -> dict[str, Any]:
    ...

def mark_neighbor_locations_known(
    agent: dict[str, Any],
    *,
    state: dict[str, Any],
    location_id: str,
    world_turn: int,
) -> list[dict[str, Any]]:
    ...

def get_known_neighbor_ids(
    agent: dict[str, Any],
    location_id: str,
) -> tuple[str, ...]:
    ...

def summarize_location_knowledge(agent: dict[str, Any]) -> dict[str, Any]:
    ...
```

---

# Direct visit behavior

When NPC enters or starts in a location:

```text
1. mark current location as visited;
2. build compact snapshot;
3. increment visit_count;
4. update last_visited_turn / last_confirmed_turn;
5. learn immediate neighbors as known_exists;
6. add confirmed edges from current location to neighbors.
```

Important:
- learning neighbor existence does not reveal neighbor snapshot;
- neighbor's `knowledge_level` should be `known_exists` unless already higher;
- do not overwrite a visited snapshot with lower-quality neighbor knowledge.

---

# Spawn initialization

On NPC creation / first tick normalization:

```text
current location -> visited
current neighbors -> known_exists
```

If NPC has faction/home/trader background, optional starting knowledge can be seeded later, not in this PR unless already existing.

---

# Merge rules

Add source priority:

```python
SOURCE_PRIORITY = {
    "direct_visit": 100,
    "direct_neighbor_observation": 80,
    "trader_intel": 70,
    "shared_by_agent": 60,
    "witness_report": 50,
    "rumor": 30,
}
```

Update field if:
- incoming source priority is higher;
- incoming observation is newer and confidence not much lower;
- existing field is missing;
- direct visit always updates snapshot.

Do not downgrade:
- `visited` to `known_exists`;
- high-confidence direct trader/shelter facts to stale rumor.

---

# Size limits

Constants:

```python
MAX_KNOWN_LOCATIONS_PER_AGENT = 700
MAX_DETAILED_KNOWN_LOCATIONS_PER_AGENT = 350
MAX_KNOWN_LOCATION_EDGES_PER_AGENT = 1800
```

For expected 300–600 known locations per NPC, no eviction should happen in normal tests. Limits are safety guards.

If limit exceeded:
- keep visited locations;
- keep shelters/traders/exits;
- keep recently used path locations;
- compact or drop low-confidence stale rumors first.

---

# Knowledge revision

Every meaningful update increments:

```python
knowledge["revision"] += 1
knowledge["stats"]["known_locations_revision"] += 1
knowledge["stats"]["last_location_knowledge_update_turn"] = world_turn
```

This is required for context/path caches in later PRs.

---

# Integration points

## On spawn / normalization

Call:

```python
mark_location_visited(...)
mark_neighbor_locations_known(...)
```

## On travel arrival

When travel step completes and agent's `location_id` changes:

```python
mark_location_visited(...)
mark_neighbor_locations_known(...)
```

## On debug import/load

Normalize missing `knowledge_v1.known_locations`.

---

# Tests

Create:

```text
backend/tests/knowledge/test_location_knowledge.py
```

Required tests:

```python
def test_spawn_marks_current_location_visited_and_neighbors_known_exists(): ...
def test_visit_location_stores_compact_snapshot_not_full_location_dict(): ...
def test_neighbor_discovery_does_not_reveal_neighbor_snapshot(): ...
def test_revisit_updates_last_visited_and_visit_count(): ...
def test_direct_visit_not_overwritten_by_lower_confidence_rumor(): ...
def test_known_locations_revision_increments_on_update(): ...
def test_known_locations_size_limits_preserve_visited_shelters_traders(): ...
def test_snapshot_does_not_include_current_agents_list_or_mutable_location_ref(): ...
```

---

# Performance requirements

For PR 1:

```text
mark_location_visited: O(degree(location))
upsert_known_location: O(1)
summarize_location_knowledge: O(number_known_locations), but only for debug or cache miss
no all-world scan
no memory scan
no deep copy of 300–600 entries per tick
```

Add test/benchmark:

```python
def test_mark_location_visited_1000_locations_600_known_fast():
    # use time budget or operation counter, not strict wall-clock if flaky
```

---

# Acceptance criteria

```text
[ ] Agent starts knowing only current location and neighbors.
[ ] Direct visit stores compact snapshot.
[ ] Neighbor knowledge only records existence/edge, not contents.
[ ] Lower-quality shared knowledge cannot downgrade direct visit.
[ ] known_locations uses structured table, not memory spam.
[ ] Updates increment knowledge revision.
[ ] No full location dicts or mutable state refs stored in agent knowledge.
[ ] No planner/pathfinding behavior changes yet except passive knowledge recording.
```
