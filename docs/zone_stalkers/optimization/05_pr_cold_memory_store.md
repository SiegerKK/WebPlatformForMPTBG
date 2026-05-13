# Memory Optimization PR 5 — Cold Memory Store

## Goal

Remove large `memory_v3.records/indexes` from hot world state.

This is the strategic PR for 100 NPC at x600.

Current hot-state problem:

```text
state["agents"][agent_id]["memory_v3"]
  records: up to 500
  indexes: multiple buckets
  stats/details
```

For 100 NPC this makes every state load/save/JSON/zlib/Redis/DB path pay for memory, even when NPCs are asleep, traveling or waiting.

Target:

```text
hot agent state:
  operational runtime + memory_ref + memory_summary

cold memory store:
  memory_v3 records/indexes/knowledge loaded on demand
```

## Dependencies

Requires:

```text
PR 1 — write policy stabilized
PR 2 — incremental indexes
PR 3 — knowledge tables
PR 4 — context cache preferred
```

Do not start this PR before those are merged.

## Scope

In scope:

```text
1. memory_ref and memory_summary in hot agent state.
2. Cold memory storage abstraction.
3. Redis-backed cold memory store if Redis available.
4. In-memory fallback for tests/local.
5. Lazy load on Brain/debug/export.
6. Save cold memory only when dirty.
7. Migration/normalization from old hot memory_v3.
8. Compatibility fallback for old states.
```

Out of scope:

```text
vector search;
external SQL memory table;
changing memory semantics;
changing Brain objective scoring;
deleting old save compatibility immediately.
```

## Hot agent shape

Replace large memory payload with:

```json
{
  "memory_ref": "ctx:agent_memory:<context_id>:<agent_id>",
  "memory_summary": {
    "records_count": 500,
    "memory_revision": 123,
    "knowledge_revision": 456,
    "last_memory_write_turn": 12400,
    "last_compaction_turn": 12300,
    "cold_store_version": 1,
    "is_loaded": false,
    "dirty": false
  }
}
```

During migration window, old agents may still have `memory_v3`.

## Cold memory blob

Redis key:

```text
ctx:agent_memory:<context_id>:<agent_id>
```

Value:

```json
{
  "version": 1,
  "agent_id": "agent_debug_1",
  "memory_v3": {
    "records": {},
    "indexes": {},
    "stats": {}
  },
  "knowledge_v1": {
    "revision": 0,
    "known_npcs": {},
    "known_locations": {},
    "known_traders": {},
    "known_hazards": {}
  }
}
```

Compression:

```text
Use same compression helper as state cache if available.
Compression level should be configurable.
```

## API

Add module:

```text
backend/app/games/zone_stalkers/memory/cold_store.py
```

Suggested functions:

```python
def get_agent_memory_ref(context_id: str, agent_id: str) -> str: ...

def load_agent_memory(
    *,
    context_id: str,
    agent_id: str,
    agent: dict[str, Any],
    redis_client: Any | None = None,
) -> dict[str, Any]: ...

def save_agent_memory_if_dirty(
    *,
    context_id: str,
    agent_id: str,
    agent: dict[str, Any],
    memory_blob: dict[str, Any],
    redis_client: Any | None = None,
) -> bool: ...

def ensure_agent_memory_loaded(
    *,
    context_id: str,
    agent_id: str,
    agent: dict[str, Any],
    state: dict[str, Any] | None = None,
) -> dict[str, Any]: ...

def mark_agent_memory_dirty(agent: dict[str, Any]) -> None: ...
```

## Runtime loading rules

Load cold memory when:

```text
Brain decision actually runs;
context_builder needs knowledge and cache miss;
memory event writes knowledge/episodic memory;
debug full profile requested;
NPC history export requested;
manual inspect command;
memory decay/compaction scheduled.
```

Do not load cold memory when:

```text
NPC simply continues scheduled_action;
NPC sleeps;
NPC travels;
NPC waits in shelter;
tick updates needs only;
agent is dead or left_zone and no debug/export requested.
```

## Write path

When writing memory:

```text
1. load cold memory if not loaded;
2. write/aggregate/upsert into memory blob;
3. update memory_summary in hot agent;
4. mark dirty;
5. save cold memory at controlled cadence or end of tick batch.
```

Avoid saving cold memory after every tiny write if batch can coalesce.

## Migration from hot memory_v3

On first load:

```python
if agent has memory_v3 and no memory_ref:
    create memory_ref
    move memory_v3 into cold blob
    move knowledge_v1 into cold blob if present
    set memory_summary
    remove agent["memory_v3"] from hot state
```

Compatibility mode:

```text
If cold store key missing but agent.memory_v3 exists, migrate.
If cold store unavailable in tests, use in-memory fallback.
```

## Dirty summary in hot state

Hot summary must stay enough for UI lists:

```text
records_count
top_kinds maybe cached
memory_health maybe cached
knowledge_revision
memory_revision
```

But not full records/indexes.

## Debug/export

Debug full profile should explicitly load cold memory:

```text
full_debug deep profile:
  load memory_v3/knowledge_v1
  build story_events
  include memory stats

map list / lightweight projection:
  use memory_summary only
```

## Tests

Add:

```text
backend/tests/decision/v3/test_cold_memory_store.py
backend/tests/decision/v3/test_cold_memory_migration.py
backend/tests/test_zone_stalkers_projections.py
```

Required tests:

```python
def test_migrates_hot_memory_v3_to_cold_store(): ...
def test_hot_agent_state_keeps_only_memory_ref_and_summary(): ...
def test_scheduled_action_tick_does_not_load_cold_memory(): ...
def test_brain_decision_loads_cold_memory_on_cache_miss(): ...
def test_memory_write_marks_cold_memory_dirty(): ...
def test_save_agent_memory_if_dirty_writes_once_per_batch(): ...
def test_debug_full_profile_loads_cold_memory(): ...
def test_old_state_with_memory_v3_still_works(): ...
def test_missing_cold_key_falls_back_to_hot_memory_if_available(): ...
def test_memory_summary_updates_after_write(): ...
def test_batch_tick_does_not_serialize_full_memory_in_hot_state(): ...
```

## Performance metrics

Add:

```text
cold_memory_loads
cold_memory_saves
cold_memory_load_ms
cold_memory_save_ms
cold_memory_bytes
hot_state_agent_memory_bytes
state_save_bytes
state_save_ms
state_load_ms
redis_payload_bytes
```

## Manual validation

Before/after 100 NPC simulation:

```text
Export:
  hot state size
  Redis state blob size
  cold memory total bytes
  state save/load ms
  zlib compress/decompress ms
  Effective speed
```

Expected:

```text
hot state is much smaller
state save/load/compress improves
scheduled-action ticks avoid cold memory load
Brain/debug loads memory on demand
```

## Definition of Done

```text
[ ] Hot agent state no longer contains full memory_v3 records/indexes for migrated agents.
[ ] memory_ref + memory_summary exist.
[ ] Cold memory loads only on demand.
[ ] Dirty cold memory saves correctly.
[ ] Old states migrate safely.
[ ] Debug/export can still show memory/story.
[ ] Brain behavior remains correct.
[ ] State save/load payload is measurably smaller.
```
