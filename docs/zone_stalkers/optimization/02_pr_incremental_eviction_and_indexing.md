# Memory Optimization PR 2 — Incremental Eviction and Index Maintenance

## Goal

Make saturated `memory_v3` writes cheap.

Current risk:

```text
memory_v3 at cap
→ add record
→ trim to cap
→ sort all records
→ evict
→ rebuild all indexes
```

This PR changes memory writes so eviction/index updates are incremental.

## Dependency

Requires PR 1:

```text
Memory write policy and spam control must be merged first.
```

## Scope

In scope:

```text
1. Incremental deindex/index.
2. No rebuild_all_indexes on every saturated write.
3. choose_eviction_candidate(...).
4. skip incoming low-priority records when full.
5. memory_revision and stats.
6. tests proving index consistency after eviction.
```

Out of scope:

```text
cold memory store;
known_npcs tables;
context_builder cache;
changing retrieval scoring;
changing gameplay memory semantics.
```

## Files to inspect

```text
backend/app/games/zone_stalkers/memory/store.py
backend/app/games/zone_stalkers/memory/models.py
backend/app/games/zone_stalkers/memory/retrieval.py
backend/tests/decision/v3/test_memory_store.py
backend/tests/decision/v3/test_memory_retrieval.py
```

## 1. Add deindex helper

Implement:

```python
def deindex_raw_record(mem: dict[str, Any], raw: dict[str, Any]) -> None:
    ...
```

It must remove the record id from all index dimensions:

```text
by_layer
by_kind
by_location
by_entity
by_item_type
by_tag
future indexes added later
```

It must tolerate missing indexes, missing ids, absent buckets, empty buckets and duplicate ids.

When bucket becomes empty, remove it.

## 2. Ensure index helper is idempotent

Existing `index_record(...)` should be safe.

If it appends without checking duplicates, fix it:

```python
def _append_unique(bucket: list[str], record_id: str) -> None:
    if record_id not in bucket:
        bucket.append(record_id)
```

Acceptance:

```text
[ ] indexing same record twice does not duplicate ids.
[ ] deindex removes ids from all buckets.
```

## 3. New add flow

Replace full rebuild trim flow with:

```python
def add_memory_record(agent: dict[str, Any], record: MemoryRecord) -> bool:
    mem = ensure_memory_v3(agent)
    records = mem["records"]
    raw = record.to_dict()

    stats = mem.setdefault("stats", {})
    stats["memory_write_attempts"] = int(stats.get("memory_write_attempts", 0)) + 1

    if len(records) >= MEMORY_V3_MAX_RECORDS:
        victim_id = choose_eviction_candidate(mem, incoming_raw=raw)

        if victim_id is None:
            stats["dropped_new_records"] = int(stats.get("dropped_new_records", 0)) + 1
            stats["memory_write_dropped"] = int(stats.get("memory_write_dropped", 0)) + 1
            return False

        victim_raw = records.pop(victim_id, None)
        if victim_raw:
            deindex_raw_record(mem, victim_raw)
            stats["memory_evictions"] = int(stats.get("memory_evictions", 0)) + 1

    records[record.id] = raw
    index_raw_record(mem, raw)
    stats["records_count"] = len(records)
    stats["memory_revision"] = int(stats.get("memory_revision", 0)) + 1
    return True
```

## 4. choose_eviction_candidate

Implement:

```python
def choose_eviction_candidate(
    mem: dict[str, Any],
    *,
    incoming_raw: dict[str, Any],
) -> str | None:
    ...
```

MVP may scan all records O(500).

Victim preference:

```text
1. archived/stale/contradicted;
2. low-retention active_plan/crowd/travel routine records;
3. old low-confidence episodic records;
4. old non-critical goal records;
5. protected/critical records only if incoming is more important and cap requires it.
```

Do not evict these for ordinary incoming records:

```text
target_death_confirmed
death
combat_kill
global_goal_completed
corpse_seen for active target
target_corpse_reported for active target
recent emission_started/warning
```

If incoming is lower priority than every existing record, drop incoming and return `None`.

## 5. Hard cap invariant

No matter what:

```python
len(mem["records"]) <= MEMORY_V3_MAX_RECORDS
```

For normal add, it is acceptable to drop incoming low-priority record instead of evicting protected records.

For explicit normalization of old states, hard cap must be enforced even if all records are protected.

## 6. Avoid rebuild_all_indexes except explicit repair

Keep `rebuild_memory_indexes(...)` only for:

```text
migration/normalization;
explicit debug/admin repair;
tests;
loading old corrupted state.
```

Do not call it in normal `add_memory_record(...)`.

Add stat:

```text
memory_index_rebuilds
```

Acceptance:

```text
[ ] normal saturated write does not increment memory_index_rebuilds.
[ ] explicit repair does increment it.
```

## 7. Memory revision

Maintain:

```json
"stats": {
  "memory_revision": 123,
  "records_count": 500,
  "memory_evictions": 50,
  "dropped_new_records": 30,
  "memory_write_attempts": 1000
}
```

Rules:

```text
memory_revision increments when records/indexes actually change.
Do not increment when incoming record is dropped.
```

Later PRs will use `memory_revision` and `knowledge_revision`.

## 8. Index consistency checks

Add helper used in tests:

```python
def validate_memory_indexes(mem: dict[str, Any]) -> list[str]:
    ...
```

It should verify:

```text
every indexed id exists in records;
every active record appears in expected indexes;
no duplicate ids in buckets;
stats.records_count == len(records).
```

Do not call on every runtime tick.

## Tests

Add:

```text
backend/tests/decision/v3/test_memory_incremental_eviction.py
```

Required tests:

```python
def test_deindex_raw_record_removes_id_from_all_indexes(): ...
def test_index_raw_record_is_idempotent(): ...
def test_saturated_add_evicts_one_record_without_rebuild(): ...
def test_low_priority_incoming_is_dropped_when_memory_full_of_critical_records(): ...
def test_high_priority_incoming_evicts_low_priority_record(): ...
def test_indexes_remain_consistent_after_many_evictions(): ...
def test_memory_revision_increments_only_on_actual_change(): ...
def test_records_count_never_exceeds_cap(): ...
def test_saturated_add_does_not_call_rebuild_indexes(monkeypatch): ...
```

## Validation

```bash
pytest backend/tests/decision/v3/test_memory_incremental_eviction.py -q
pytest backend/tests/decision/v3/test_memory_store.py -q
pytest backend/tests/decision/v3/test_memory_retrieval.py -q
pytest backend/tests/decision/v3/test_memory_event_policy.py -q
pytest backend/tests -k "not e2e" -q
```

Manual validation:

```text
simulate 1000 memory writes after cap reached
check:
  memory_index_rebuilds == 0 for normal writes
  records_count <= 500
  important records preserved
  memory_write_dropped/evicted/aggregated stats make sense
```

## Definition of Done

```text
[ ] Normal memory writes maintain indexes incrementally.
[ ] Saturated write does not rebuild all indexes.
[ ] Low-priority incoming records can be dropped.
[ ] Hard cap remains enforced.
[ ] Critical records remain protected.
[ ] Index consistency tests pass.
[ ] Metrics expose evictions/drops/revisions.
```
