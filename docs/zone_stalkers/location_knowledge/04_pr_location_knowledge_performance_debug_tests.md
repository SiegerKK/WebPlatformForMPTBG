# PR 4 — Location Knowledge Performance, Debug, and Benchmarks

## Dependency

Requires:
- PR 1: known_locations table
- PR 2: known-graph planner
- PR 3: knowledge exchange

## Goal

Make location knowledge safe for expected scale:

```text
500–1000 world locations
300–600 known locations per NPC
~40 NPCs initially
```

Performance is the main risk. This PR adds indexes, cache rules, metrics, debug summaries and regression benchmarks.

---

# Performance risks

Avoid:
- deep-copying 300–600 known locations per NPC per tick;
- scanning all 1000 world locations for every decision;
- copying all location knowledge during conversations;
- rebuilding known graph path structures every tick;
- including huge `known_locations` arrays in `AgentContext`;
- sorting all known locations repeatedly for each objective;
- storing full location dict snapshots.

---

# Target budgets

For normal brain tick:

```text
known_locations direct lookup: O(1)
known graph path query: cached, otherwise O(E_known) but bounded
frontier candidate scoring: <= 20 candidates
trader/shelter scoring: <= 10 candidates
anomaly candidate scoring: <= 20 candidates
knowledge exchange: <= 5 locations per interaction
context_builder location summary: O(indexed summary), no full copy
```

Memory target per known location entry:
```text
compact: ~200–500 bytes ideal
600 entries ≈ 120–300 KB per NPC worst case before Python overhead
```

Given Python dict overhead is high, keep entries compact and avoid redundant nested structures.

---

# Indexes

Maintain inside `knowledge_v1`:

```python
"location_indexes": {
    "revision": 123,
    "visited_ids": set/list,
    "frontier_ids": set/list,
    "known_trader_location_ids": set/list,
    "known_shelter_location_ids": set/list,
    "known_exit_location_ids": set/list,
    "known_anomaly_location_ids": set/list,
    "recently_updated_ids": list,
}
```

Because JSON/state may need serializable structures, use lists in stored state and convert to sets only in local cached views.

Update indexes incrementally in `upsert_known_location`, not by scanning all known locations every tick.

---

# Context builder integration

`AgentContext` should not carry full `known_locations`.

Instead expose summaries:

```python
context.location_knowledge_summary = {
    "known_locations_count": 523,
    "visited_locations_count": 318,
    "frontier_count": 64,
    "known_traders_count": 2,
    "known_shelters_count": 5,
    "known_exits_count": 1,
    "stale_locations_count": 110,
    "knowledge_revision": 991,
}
```

Expose small candidate lists only when needed:

```python
context.known_trader_candidates <= 10
context.known_shelter_candidates <= 10
context.frontier_candidates <= 20
context.anomaly_candidates <= 20
```

Do not deep-copy all known location entries from cache.

The existing context cache design already expects derived context from `knowledge_v1` and warns against caching mutable full objects. Keep that principle.

---

# Known graph path cache

Per agent:

```python
"known_graph_path_cache": {
    "revision": known_locations_revision,
    "current_location_id": "loc_A",
    "entries": {
        "loc_A->loc_B": {
            "path": ["loc_A", "loc_X", "loc_B"],
            "cost": 3,
            "created_turn": 12300,
            "last_used_turn": 12320,
            "hits": 4,
        }
    },
    "max_entries": 128,
}
```

Rules:
- invalidate on `known_locations_revision` change;
- invalidate if current location changes and cache is start-specific;
- LRU evict beyond max entries;
- no all-pairs cache.

---

# Candidate cache

For stable objectives, cache top candidates by revision:

```python
"location_candidate_cache": {
    "revision": known_locations_revision,
    "frontier": [...],
    "traders": [...],
    "shelters": [...],
    "anomalies": [...],
    "created_turn": world_turn,
}
```

Use small TTL/bucket:

```text
10–30 turns
```

Bypass for:
- emission critical;
- combat;
- direct target visible;
- debug deep inspection.

---

# Debug projection

Add to NPC profile/debug:

```python
"location_knowledge": {
    "known_locations": 523,
    "visited_locations": 318,
    "frontiers": 64,
    "known_traders": 2,
    "known_shelters": 5,
    "known_exits": 1,
    "known_anomalies": 11,
    "stale_locations": 110,
    "last_update_turn": 12345,
    "revision": 991,
    "path_cache_hits": 120,
    "path_cache_misses": 8,
    "exchange_received_count": 14,
    "exchange_sent_count": 9,
}
```

Do not send full table to frontend by default.

Add optional debug endpoint/flag:
```text
deep_location_knowledge=true
```
that returns paginated entries:
```text
limit=50 offset=0
```

---

# Metrics

Add counters:

```text
location_knowledge_updates
location_knowledge_direct_visits
location_knowledge_neighbor_discoveries
location_knowledge_shared_received
location_knowledge_entries_evicted
known_graph_path_cache_hits
known_graph_path_cache_misses
known_graph_pathfinding_calls
known_graph_nodes_scanned
context_builder_known_location_entries_copied
location_exchange_entries_considered
location_exchange_entries_sent
```

Critical alert metric:
```text
context_builder_known_location_entries_copied should be near 0 in normal tick
```

---

# Benchmarks

Create:

```text
backend/tests/performance/test_location_knowledge_perf.py
```

Use deterministic synthetic world.

Scenarios:

```python
def test_upsert_known_location_600_entries_fast(): ...
def test_mark_visit_with_1000_world_locations_only_touches_degree(): ...
def test_known_path_cache_600_known_locations_reuses_result(): ...
def test_context_builder_does_not_copy_all_known_locations(): ...
def test_exchange_600_known_locations_sends_only_top_k(): ...
def test_40_agents_600_known_locations_summary_budget(): ...
```

Avoid flaky wall-clock-only tests. Prefer:
- operation counters;
- monkeypatch spies;
- max copied entries;
- max considered entries;
- path cache hit/miss assertions.

Use loose wall-clock smoke only if stable.

---

# Long-run smoke

Add optional/nightly or dedicated CI shard:

```python
def test_location_knowledge_long_run_40_agents_1000_locations_5000_turns_smoke():
    ...
```

Assertions:
- no unbounded growth above limits;
- known_locations count <= configured max;
- exchange does not copy full maps;
- context_builder does not deep-copy all known_locations;
- cache hit rate > threshold after warm-up;
- no memory event spam for location facts.

If too slow, mark as nightly/manual.

---

# Serialization

Ensure:
- known_locations entries are JSON-serializable;
- no sets stored directly in agent state;
- no object references to true location dicts;
- cache entries can be safely dropped on load.

---

# Acceptance criteria

```text
[ ] 40 NPC * 600 known locations does not create per-tick context slowdown.
[ ] context_builder does not deep-copy full known_locations.
[ ] pathfinding uses revision-based path cache.
[ ] exchange sends <= top-K entries.
[ ] debug projection returns summary by default, not full table.
[ ] performance tests cover 500–1000 locations.
[ ] no event-memory spam for location knowledge.
```
