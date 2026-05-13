# Memory Optimization PR 4 — Context Builder Cache

## Goal

Reduce Brain decision cost by caching derived context parts.

After PR 3, context should be derived mostly from:

```text
knowledge_v1
memory_summary
current location
current objective
target belief
world hazard/emission state
```

This PR prevents repeated memory/knowledge scans and repeated sorting for the same agent state.

## Dependencies

Requires:

```text
PR 3 — Knowledge tables / known_npcs
```

Preferably also:

```text
PR 2 — memory_revision and incremental store stats
```

## Scope

In scope:

```text
1. Add brain_context_cache to agent runtime.
2. Cache selected derived context sections.
3. Invalidate by knowledge_revision, memory_revision, location_id, objective_key, target_id and world conditions.
4. Add metrics for cache hits/misses.
5. Tests proving cache does not stale incorrectly.
```

Out of scope:

```text
cold memory store;
changing objective scoring;
changing Brain invalidation/budget;
caching full AgentContext with mutable references;
frontend changes.
```

## Cache shape

Add to agent hot runtime:

```json
"brain_context_cache": {
  "cache_key": {
    "knowledge_revision": 120,
    "memory_revision": 500,
    "world_turn_bucket": 123,
    "location_id": "loc_A",
    "objective_key": "FIND_ARTIFACTS",
    "target_id": "agent_debug_1",
    "emission_phase": "none"
  },
  "derived": {
    "known_entities": [],
    "known_locations": [],
    "known_traders": [],
    "known_hazards": [],
    "target_leads": [],
    "corpse_leads": []
  },
  "created_turn": 12300,
  "last_used_turn": 12304,
  "hits": 3
}
```

Do not cache large full memory records.

## Cache key

Include:

```text
knowledge_revision
memory_revision
location_id
objective_key
target_id / kill_target_id
global_goal
emission state/phase
world_turn_bucket
```

Use a bucket instead of exact turn to avoid invalidating every minute:

```python
CONTEXT_CACHE_TURN_BUCKET_SIZE = 10
world_turn_bucket = world_turn // CONTEXT_CACHE_TURN_BUCKET_SIZE
```

For urgent/combat/emission decisions, bypass or use smaller bucket.

## Invalidation rules

Cache invalid if any changed:

```text
knowledge_revision
memory_revision if context still uses memory fallback
location_id
current objective_key
global_goal
kill_target_id / target_id
emission phase
agent is dead / left_zone
```

Also bypass cache if:

```text
combat active
critical survival need
manual debug explain request with deep=True
context_builder called with force_refresh=True
```

## Implementation approach

In `context_builder.py`:

```python
def build_agent_context(...):
    cache_key = _build_context_cache_key(agent, state, world_turn, objective_key)
    cached = _get_context_cache(agent, cache_key)
    if cached:
        stats["context_builder_cache_hits"] += 1
        return _assemble_agent_context_from_cached_parts(...)

    derived = _build_derived_context_parts(...)
    _store_context_cache(agent, cache_key, derived, world_turn)
    stats["context_builder_cache_misses"] += 1
    return _assemble_agent_context(...)
```

Avoid returning cached mutable objects directly. Use shallow copies where needed.

## Metrics

Add:

```text
context_builder_calls
context_builder_cache_hits
context_builder_cache_misses
context_builder_cache_hit_rate
context_builder_memory_scan_records
context_builder_knowledge_entries_scanned
context_builder_ms
```

Expose in debug/perf stats.

## Memory scan limit

Even on cache miss:

```text
one pass max over memory fallback records
no repeated sorting for known_entities/locations/hazards
```

If knowledge_v1 has enough data, avoid scanning memory_v3 entirely.

## Tests

Add:

```text
backend/tests/decision/test_context_builder_cache.py
```

Required tests:

```python
def test_context_builder_cache_hit_for_same_revision_location_objective(): ...
def test_context_builder_cache_invalidates_on_knowledge_revision_change(): ...
def test_context_builder_cache_invalidates_on_location_change(): ...
def test_context_builder_cache_invalidates_on_objective_change(): ...
def test_context_builder_cache_invalidates_on_target_change(): ...
def test_context_builder_cache_bypassed_for_combat_active(): ...
def test_context_builder_cache_bypassed_for_critical_survival_need(): ...
def test_cached_context_does_not_share_mutable_lists_with_agent_state(): ...
def test_context_builder_scans_memory_once_on_cache_miss(monkeypatch): ...
```

## Manual validation

Run long scenario:

```text
10 NPC
x600
4000 turns
```

Check:

```text
context_builder_cache_hit_rate > 0 for stable scheduled-action/plan periods
context_builder_memory_scan_records reduced
Brain decisions still correct
```

For target/corpse scenario:

```text
[ ] witness report updates knowledge_revision;
[ ] killer context cache invalidates;
[ ] killer sees new corpse lead.
```

## Definition of Done

```text
[ ] Context cache exists.
[ ] Cache invalidates correctly.
[ ] Cache is bypassed for urgent cases.
[ ] Context builder avoids repeated memory scans/sorts.
[ ] Metrics expose cache behavior.
[ ] Brain v3 E2E remains green.
```
