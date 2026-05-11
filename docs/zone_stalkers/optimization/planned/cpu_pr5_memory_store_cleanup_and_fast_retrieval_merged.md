# CPU Optimization PR 5 — Memory Store Cleanup and Fast Retrieval

> Goal:
>
> ```text
> Stop long-lived NPCs from slowing down due to growing local memory state and expensive memory retrieval.
> ```
>
> This document merges two earlier PR5 plans:
>
> ```text
> 1. Fast Memory Retrieval for Brain v3
> 2. Memory Store Cleanup and Fast Retrieval
> ```
>
> The second document is the broader and newer scope. The fast retrieval plan remains valid, but it is now one phase inside a larger memory-store cleanup PR.

---

# 1. Why this PR exists

Observed symptom:

```text
Empty / early map:
  high Effective speed

After a few hundred ticks with NPCs alive:
  Effective speed drops sharply
```

The likely cause is growth of hot NPC state:

```text
NPC lives longer
→ legacy memory grows
→ memory_v3 records and indexes grow
→ retrieve_memory candidate sets grow
→ state serialization / delta / save cost grows
→ Brain v3 decisions become more expensive
→ batch_tick_logic_ms / save_state_ms / delta_ms grow
→ Effective speed falls
```

This PR targets both sides of the problem:

```text
1. Reduce memory growth.
2. Make memory retrieval cheaper.
```

---

# 2. Relationship between the two previous documents

## 2.1. `cpu_pr_5_memory_retrieval_fast_path.md`

This was the narrow version.

It focused only on:

```text
- raw dict scoring;
- heap/top-k selection;
- candidate scan limit;
- read-only retrieval;
- retrieval metrics;
- tests for deterministic behavior.
```

It explicitly avoided changing memory semantics, legacy memory, memory caps, objective scoring, hunt behavior, or active plan mechanics.

## 2.2. `cpu_pr_5_memory_store_cleanup_and_fast_retrieval.md`

This was the broader version.

It includes everything from the fast retrieval document, plus:

```text
- phase out legacy agent["memory"];
- make memory_v3 canonical;
- reduce MEMORY_V3_MAX_RECORDS from 5000 to 500;
- normalize oversized old states;
- update tests and compatibility helpers;
- add memory size metrics, not only retrieval metrics.
```

## 2.3. Final decision

Use the broader document as the final PR5 scope.

The fast retrieval work is not discarded. It becomes:

```text
Phase 4 — Fast retrieve_memory(...)
```

inside this unified PR5.

---

# 3. Current growth points

Current memory-related growth points:

```text
agent["memory"] legacy list:
  up to MAX_AGENT_MEMORY = 2000

agent["memory_v3"]["records"]:
  up to MEMORY_V3_MAX_RECORDS = 5000

memory_v3 indexes:
  grow together with records

brain_trace.events:
  capped to BRAIN_TRACE_MAX_EVENTS = 5

brain_runtime.invalidators:
  capped by PR4 logic

state.decision_queue:
  capped by PR4 logic
```

The two main hot-state risks are:

```text
1. legacy agent["memory"]
2. memory_v3 records/indexes
```

---

# 4. Design decision: Do we still need memory_v3?

Yes.

`memory_v3` remains useful because Brain v3 needs structured retrieval over:

```text
layer
kind
location
entity
item_type
tag
confidence
importance
created_turn
status
```

But it should not be a large lifetime journal.

New intended role:

```text
memory_v3:
  small working memory / relevant facts / recent observations

GameEvent:
  durable event history

brain_trace:
  short debug reasoning trace

brain_v3_context:
  current decision summary

brain_runtime:
  cache / invalidation / budget runtime

active_plan_v3:
  current plan runtime
```

Therefore:

```text
memory_v3 remains, but cap is reduced to 500.
legacy agent["memory"] is phased out of runtime logic.
```

---

# 5. Scope

## 5.1. In scope

```text
1. Audit all legacy memory usage.
2. Make memory_v3 the canonical runtime memory store.
3. Disable legacy memory writes by default for new states.
4. Keep compatibility readers for old states/tests.
5. Reduce legacy memory cap if legacy writing remains enabled.
6. Reduce MEMORY_V3_MAX_RECORDS from 5000 to 500.
7. Enforce memory_v3 hard cap after writes.
8. Add old-state normalization for oversized memory_v3.
9. Optimize retrieve_memory(...):
   - raw dict scoring;
   - heap/top-k;
   - deterministic candidate limit;
   - most selective index bucket first;
   - read-only retrieval by default.
10. Add memory size and retrieval metrics.
11. Update tests away from direct legacy memory assumptions.
12. Keep Brain v3 E2E behavior unchanged.
```

## 5.2. Out of scope

Do not implement in this PR:

```text
- external DB-backed memory storage;
- vector search;
- semantic embeddings;
- full hot/cold archive system;
- full memory consolidation rewrite;
- objective scoring rewrite;
- hunt behavior rewrite;
- map topology optimization;
- dirty delta rewrite;
- PR4 brain invalidation/budget changes except test compatibility.
```

---

# 6. Phase 1 — Audit legacy memory usage

Before changing behavior, audit every runtime use of:

```text
agent["memory"]
agent.get("memory")
agent.setdefault("memory", [])
any_memory(...)
any_objective_decision(...)
mem["effects"]
```

Classify each usage:

```text
A. Runtime gameplay logic.
B. Brain decision context.
C. Tests / E2E helpers.
D. Debug UI / projection.
E. Migration / backward compatibility.
```

Acceptance:

```text
[ ] No runtime gameplay logic relies exclusively on legacy agent["memory"].
[ ] If something still reads legacy memory, it has a memory_v3 equivalent or compatibility helper.
```

---

# 7. Phase 2 — Canonical memory write path

## 7.1. Make `_add_memory(...)` write memory_v3 first

Current behavior:

```text
_add_memory(...)
→ writes legacy agent["memory"]
```

New behavior:

```text
_add_memory(...)
→ create canonical memory_v3 record
→ add_memory_record(agent, record)
→ optionally write compact legacy compatibility entry only if enabled
```

Add state/config flag:

```python
state["legacy_memory_write_enabled"] = False
```

Default for new states:

```text
False
```

## 7.2. Legacy compatibility entry must be tiny

If legacy writing remains enabled, reduce the cap:

```python
MAX_AGENT_MEMORY = 100
```

Legacy entry should keep only:

```text
world_turn
type
summary
effects.action_kind
effects.objective_key
effects.location_id
effects.agent_id
effects.target_id
```

Do not store heavy nested fields.

## 7.3. Tests should use helper readers

Update E2E helpers so they check memory_v3 first and legacy second.

Example:

```python
def any_objective_decision(agent: dict, objective_key: str) -> bool:
    return (
        any_objective_decision_v3(agent, objective_key)
        or any_objective_decision_legacy(agent, objective_key)
    )
```

Apply the same pattern to:

```text
any_memory
memories
first_objective_turn
first_memory_turn
```

Acceptance:

```text
[ ] New tests pass with legacy_memory_write_enabled=False.
[ ] Old saves/tests can still pass through compatibility readers.
```

---

# 8. Phase 3 — Reduce memory_v3 cap

Change:

```python
MEMORY_V3_MAX_RECORDS = 5000
```

to:

```python
MEMORY_V3_MAX_RECORDS = 500
```

Reason:

```text
memory_v3 is hot state.
500 records is enough for working memory.
Long-term history belongs in GameEvent or future cold archive, not in hot agent state.
```

## 8.1. Enforce hard cap

Current protected layers may include:

```python
_PROTECTED_LAYERS = {"threat", "goal", "semantic"}
```

Risk:

```text
If all or most records are protected, cap eviction may fail to reduce memory enough.
```

New rule:

```text
The cap is hard.
len(memory_v3.records) must never stay above 500 after write/trim.
```

Suggested eviction order:

```text
1. stale / contradicted / archived episodic records
2. low-importance episodic/social records
3. old low-confidence records
4. protected records only if still over hard cap
```

Acceptance:

```text
[ ] len(memory_v3.records) <= 500 after add_memory_record.
[ ] Protected layers are preferred, but not allowed to violate hard cap.
```

## 8.2. Normalize old oversized states

Add:

```python
def trim_memory_v3_to_cap(
    agent: dict[str, Any],
    *,
    max_records: int = MEMORY_V3_MAX_RECORDS,
) -> int:
    ...
```

It should:

```text
- remove over-cap records according to eviction policy;
- rebuild indexes or deindex removed records;
- update stats.records_count;
- return number of evicted records.
```

Add:

```python
def normalize_agent_memory_state(agent: dict[str, Any]) -> dict[str, int]:
    ...
```

Return counters:

```python
{
    "legacy_trimmed": int,
    "memory_v3_evicted": int,
    "indexes_rebuilt": int,
}
```

Do not normalize all agents on every read.

Recommended triggers:

```text
- on memory write;
- on explicit debug/admin normalize command;
- optionally on state load only if records_count > hard cap and normalization is enabled.
```

---

# 9. Phase 4 — Fast retrieve_memory(...)

This phase incorporates the entire previous `Fast Memory Retrieval for Brain v3` document.

## 9.1. Raw dict scoring

Add:

```python
def _score_record_raw(
    raw: dict,
    query: MemoryQuery,
    world_turn: int,
    query_tags_set: set[str],
    query_kinds_set: set[str],
) -> float:
    ...
```

It must match current scoring semantics:

```text
tag_match      * 0.25
kind_match     * 0.20
location_match * 0.20
confidence     * 0.15
importance     * 0.10
recency        * 0.10
- stale_penalty
```

Do not construct `MemoryRecord` during scoring.

## 9.2. Heap/top-k instead of full sort

Replace:

```python
scored.sort(...)
selected = scored[:cap]
```

with:

```python
selected = heapq.nsmallest(cap, scored, key=lambda t: (t[0], t[1], t[2]))
```

or an equivalent bounded heap.

Keep deterministic ordering:

```text
higher score
newer created_turn
record_id ascending
```

A typical tuple:

```python
(-score, -created_turn, rid)
```

## 9.3. Deserialize only selected records

Before:

```text
N candidates
→ N MemoryRecord.from_dict(...)
→ selected MemoryRecord.from_dict(...) again
```

After:

```text
N candidates
→ raw dict scoring only
K selected
→ K MemoryRecord.from_dict(...)
```

## 9.4. Candidate limit

Add:

```python
MEMORY_V3_RETRIEVAL_MAX_CANDIDATES = 200
```

Because `MEMORY_V3_MAX_RECORDS` becomes 500, a default candidate limit of 200 is reasonable.

If practical, add:

```python
MemoryQuery(max_candidates: int | None = None)
```

Effective limit:

```python
max_candidates = min(
    query.max_candidates or MEMORY_V3_RETRIEVAL_MAX_CANDIDATES,
    len(candidate_ids),
)
```

## 9.5. Deterministic candidate limiting

Do not randomly truncate.

Suggested priority:

```text
1. active status over stale/contradicted/archived
2. higher importance
3. higher confidence
4. newer created_turn
5. id ascending
```

Helper:

```python
def _limit_candidate_ids(records_raw, candidate_ids, max_candidates):
    ranked = sorted(
        candidate_ids,
        key=lambda rid: (
            _status_rank(records_raw[rid]),
            -float(records_raw[rid].get("importance", 0.5)),
            -float(records_raw[rid].get("confidence", 1.0)),
            -int(records_raw[rid].get("created_turn", 0)),
            rid,
        ),
    )
    return set(ranked[:max_candidates])
```

## 9.6. Most selective index first

Current retrieval intersects filters in fixed order.

Change to:

```text
- build candidate buckets for each filter category;
- OR values inside one category;
- AND categories by intersecting buckets;
- start from the smallest bucket.
```

Semantics must remain:

```text
layers: OR inside layers
kinds: OR inside kinds
entity_ids: OR inside entities
item_types: OR inside item types
tags: OR qualifies, scoring rewards more tag overlap
categories: AND together
```

## 9.7. Read-only retrieval by default

Current retrieval updates selected records:

```python
raw["last_accessed_turn"] = world_turn
```

Change API:

```python
def retrieve_memory(
    agent: dict[str, Any],
    query: MemoryQuery,
    world_turn: int,
    *,
    track_access: bool = False,
) -> list[MemoryRecord]:
    ...
```

Default:

```text
track_access=False
```

Only update `last_accessed_turn` if explicitly requested.

Reason:

```text
Memory retrieval should not dirty agent state by default.
Read-only retrieval reduces COW/delta/save pressure.
Read-only retrieval makes future caching possible.
```

---

# 10. Metrics and diagnostics

Add memory metrics to tick profiler or performance metrics.

## 10.1. Per retrieval

```text
memory_retrieval_calls
memory_retrieval_ms_total
memory_retrieval_ms_max
memory_retrieval_candidates_total
memory_retrieval_candidates_max
memory_retrieval_scored_total
memory_retrieval_scored_max
memory_retrieval_selected_total
memory_retrieval_from_dict_count
```

## 10.2. Per selected NPC / debug projection

```text
legacy_memory_len
memory_v3_records_count
memory_v3_index_entries_total
memory_v3_largest_bucket_size
memory_v3_records_json_size_bytes
agent_json_size_bytes
```

## 10.3. Per batch

```text
memory_v3_evicted_count
memory_v3_trim_ms
batch_tick_logic_ms
batch_save_state_ms
batch_delta_ms
auto_tick_effective_speed
```

Acceptance:

```text
[ ] We can compare turn 50 / 200 / 500 / 1000.
[ ] We can tell whether slowdown correlates with memory size, retrieval candidates, save size, or delta size.
```

---

# 11. Tests

## 11.1. Legacy removal / compatibility tests

```python
def test_add_memory_writes_memory_v3_by_default_not_legacy(): ...

def test_legacy_memory_write_can_be_enabled_for_compatibility(): ...

def test_any_objective_decision_reads_memory_v3_first(): ...

def test_any_memory_helpers_fallback_to_legacy_for_old_state(): ...
```

## 11.2. Cap tests

```python
def test_memory_v3_cap_is_500(): ...

def test_add_memory_record_trims_to_500(): ...

def test_trim_memory_v3_rebuilds_indexes(): ...

def test_trim_memory_v3_enforces_hard_cap_even_with_protected_records(): ...
```

## 11.3. Retrieval semantic tests

```python
def test_score_record_raw_matches_dataclass_score(): ...

def test_fast_retrieval_preserves_ordering(): ...

def test_fast_retrieval_is_deterministic_on_score_ties(): ...

def test_fast_retrieval_respects_include_stale_false(): ...

def test_fast_retrieval_respects_include_stale_true_with_penalty(): ...

def test_filter_semantics_or_within_category_and_across_categories(): ...
```

## 11.4. Retrieval performance-shape tests

Do not assert wall-clock timings. Assert operation shape.

```python
def test_retrieve_memory_deserializes_only_selected_records(monkeypatch):
    # 500 records, max_results=10
    # MemoryRecord.from_dict calls should be close to 10, not 500
```

```python
def test_retrieve_memory_applies_candidate_limit(monkeypatch):
    # 500 records, max_candidates=50
    # scored_count <= 50
```

```python
def test_retrieve_memory_does_not_update_last_accessed_by_default(): ...

def test_retrieve_memory_updates_last_accessed_when_track_access_true(): ...
```

## 11.5. E2E regression tests

Run existing Brain v3 E2E:

```bash
pytest backend/tests/decision/v3/test_e2e_brain_v3_goals.py -q
pytest backend/tests/decision/v3/test_hunt_leads.py -q
pytest backend/tests/decision/v3/test_hunt_fixes.py -q
pytest backend/tests/decision/v3/test_hunt_kill_stalker_goal.py -q
```

Important E2E behavior must remain:

```text
get_rich finds artifact, sells, records LEAVE_ZONE, leaves zone
kill_stalker still uses target intel
emission survival still works
trade/resupply still works
```

---

# 12. Acceptance criteria

```text
[ ] memory_v3 hot cap is 500.
[ ] memory_v3 cap is actually enforced after writes.
[ ] legacy agent["memory"] is no longer the canonical write path.
[ ] legacy writes are disabled by default for new states.
[ ] compatibility readers can still read old legacy memories.
[ ] retrieve_memory does not deserialize every candidate.
[ ] retrieve_memory uses top-k selection, not full sort of all candidates.
[ ] retrieve_memory has deterministic candidate limit.
[ ] retrieve_memory is read-only by default.
[ ] last_accessed_turn updates only with track_access=True.
[ ] memory metrics expose records_count, candidate_count, scored_count, retrieval_ms.
[ ] Brain v3 E2E remains green.
[ ] Long-lived 1-NPC scenario shows stable or improved Effective speed.
```

---

# 13. Validation scenario

Use a long-lived 1-NPC map.

Run auto-run at x600 and collect metrics at:

```text
turn 50
turn 200
turn 500
turn 1000
```

Compare before/after PR5:

```text
legacy_memory_len
memory_v3_records_count
memory_v3_index_entries_total
memory_retrieval_candidates_max
memory_retrieval_scored_max
memory_retrieval_ms_total
batch_tick_logic_ms
batch_save_state_ms
batch_delta_ms
agent_json_size_bytes
auto_tick_effective_speed
```

Expected after PR5:

```text
memory_v3_records_count <= 500
legacy_memory_len <= 100 or 0 for new states
retrieval candidate count bounded
retrieval from_dict count near selected count
batch_tick_logic_ms grows slower
batch_save_state_ms grows slower
Effective does not collapse after a few hundred ticks
```

---

# 14. Suggested implementation order

```text
1. Audit legacy memory usage.
2. Add memory helper readers that check memory_v3 first, legacy second.
3. Make _add_memory write memory_v3 canonical records.
4. Disable legacy writes by default; keep opt-in compatibility flag.
5. Reduce legacy MAX_AGENT_MEMORY to 100 if legacy enabled.
6. Reduce MEMORY_V3_MAX_RECORDS to 500.
7. Implement hard-cap trim and index rebuild tests.
8. Add raw scoring helper and compatibility tests.
9. Change retrieve_memory to score raw dicts.
10. Change selection to heapq.nsmallest or bounded heap.
11. Deserialize only selected records.
12. Add candidate limit helper.
13. Make retrieval read-only by default.
14. Add memory size and retrieval metrics.
15. Run backend non-e2e tests and Brain v3 E2E.
16. Run long-lived NPC performance scenario.
```

---

# 15. Required test commands

```bash
pytest backend/tests/decision/v3/test_memory_retrieval.py -q
pytest backend/tests/decision/v3/test_memory_retrieval_fast_path.py -q
pytest backend/tests/decision/v3/test_e2e_brain_v3_goals.py -q
pytest backend/tests/decision/v3/test_hunt_leads.py -q
pytest backend/tests/decision/v3/test_hunt_fixes.py -q
pytest backend/tests/decision/v3/test_hunt_kill_stalker_goal.py -q
pytest backend/tests/decision/v3/test_ai_budget.py -q
pytest backend/tests/decision/v3/test_brain_invalidation.py -q
pytest backend/tests -k "not e2e" -q
```

---

# 16. Notes for Copilot

Do not simply lower constants and hope tests pass.

The important architectural change is:

```text
legacy memory stops being hot canonical state;
memory_v3 becomes smaller and bounded;
retrieval stops doing candidate-wide object reconstruction and full sorting.
```

Do not weaken E2E assertions such as:

```text
objective_decision LEAVE_ZONE exists
hunter uses target intel
trade/get_rich completion still works
```

If an E2E currently reads `agent["memory"]`, update the helper to read `memory_v3` first, not the test expectation.

---

# 17. Expected outcome

Before:

```text
NPC long run:
  agent.memory grows toward 2000
  memory_v3 grows toward 5000
  retrieve_memory scans/deserializes/sorts growing candidate sets
  save/delta/serialization grow with agent state
  Effective collapses after hundreds of ticks
```

After:

```text
NPC long run:
  legacy memory disabled or tiny
  memory_v3 bounded at 500
  retrieve_memory scans bounded candidate set
  selected records only are deserialized
  retrieval reads do not dirty state
  Effective should degrade much less over time
```
