# Memory Optimization PR 7 — Validation, Metrics and 100 NPC x600 Benchmarking

## Goal

Create repeatable validation for all memory optimization PRs.

Final 100-NPC benchmark should run after:

```text
PR 1 — write policy/spam control
PR 2 — incremental eviction
PR 3 — knowledge tables
PR 4 — context cache
PR 5 — cold memory store
PR 6 — debug trace separation
```

## Scope

In scope:

```text
1. Memory export analyzer improvements.
2. Runtime memory/performance metrics.
3. Long-run benchmark scenario.
4. 10/50/100 NPC comparison.
5. Regression thresholds that avoid timing flakiness.
6. Manual validation checklist.
```

Out of scope:

```text
adding strict timing assertions to normal CI;
requiring 100 NPC long-run in every PR CI;
optimizing gameplay logic.
```

## Metrics to collect

Per tick/batch:

```text
auto_tick_effective_speed
batch_size
batch_total_ms
batch_tick_logic_ms
state_load_ms
state_save_ms
zlib_compress_ms
zlib_decompress_ms
redis_payload_bytes
```

Memory:

```text
memory_records_total
memory_records_by_kind
memory_records_by_layer
memory_bytes_estimated
memory_index_bytes_estimated
memory_by_tag_refs
memory_write_attempts
memory_write_written
memory_write_dropped
memory_write_aggregated
memory_write_knowledge_upserts
memory_evictions
memory_index_rebuilds
memory_revision
knowledge_revision
```

Retrieval/context:

```text
memory_retrieval_calls
memory_retrieval_candidates
memory_retrieval_scored
memory_retrieval_deserialized
memory_retrieval_total_ms
context_builder_calls
context_builder_cache_hits
context_builder_cache_misses
context_builder_memory_scan_records
context_builder_knowledge_entries_scanned
context_builder_total_ms
```

Cold memory:

```text
cold_memory_loads
cold_memory_saves
cold_memory_load_ms
cold_memory_save_ms
cold_memory_bytes
hot_state_memory_bytes
```

Trace:

```text
trace_entries_written
trace_entries_dropped_disabled
trace_entries_evicted_ring_buffer
```

## Analyzer script

Extend:

```text
scripts/zone_stalkers/analyze_npc_memory_exports.py
```

Output:

```text
aggregate table:
  agents
  records_total
  records_at_cap
  by_kind
  by_layer
  stalkers_seen_ratio
  semantic_ratio
  active_plan_failure_aggregate_count
  known_npcs_count
  top memory health warnings

behavior anomalies:
  dead_but_hp_positive
  left_zone_but_active_restore_goal
  target_death_confirmed_without_direct_confirmation
  remote_death_auto_completed_goal
  critical_thirst_without_recovery
  stale_scheduled_action
  full_debug_history_story_gap
```

Add JSON output mode:

```bash
python scripts/zone_stalkers/analyze_npc_memory_exports.py ./exports --json > report.json
```

## Benchmark scenarios

### Scenario A — 10 NPC sanity

```text
10 NPC
x600
4000 turns
debug trace enabled
exports enabled
```

Purpose:

```text
behavior validation and story/debug quality.
```

### Scenario B — 50 NPC performance

```text
50 NPC
x600
4000 turns
debug trace disabled
exports at end only
```

Purpose:

```text
mid-scale performance and memory shape.
```

### Scenario C — 100 NPC target

```text
100 NPC
x600
4000–10000 turns
debug trace disabled
full exports only after run
```

Purpose:

```text
target performance.
```

## Non-flaky automated tests

Do not assert wall-clock timings in normal CI.

Use operation-count tests:

```python
def test_saturated_memory_write_does_not_rebuild_indexes(): ...
def test_retrieval_deserializes_only_selected_records(): ...
def test_context_builder_cache_hit_avoids_memory_scan(): ...
def test_scheduled_action_tick_does_not_load_cold_memory(): ...
```

## Optional benchmark command

Add script:

```text
scripts/zone_stalkers/run_memory_benchmark.py
```

CLI:

```bash
python scripts/zone_stalkers/run_memory_benchmark.py \
  --npc-count 100 \
  --turns 5000 \
  --speed x600 \
  --trace off \
  --output /tmp/zs_benchmark
```

Output:

```text
metrics.json
memory_report.json
npc_exports/
summary.md
```

## Success thresholds

These should be manual/review thresholds, not hard CI at first.

After PR 1:

```text
stalkers_seen ratio should drop below 25% for most NPCs.
active_plan lifecycle/failure spam should not dominate memory.
by_tag refs bounded.
```

After PR 2:

```text
memory_index_rebuilds should be 0 during normal saturated writes.
memory_evictions/dropped stats should be visible.
```

After PR 3:

```text
known_npcs exists and stores latest facts.
context_builder can use known_npcs for target leads.
stalkers_seen event count stays low.
```

After PR 4:

```text
context_builder_cache_hit_rate is non-zero during stable periods.
memory scan records decreases.
```

After PR 5:

```text
hot state size significantly decreases.
state save/load and zlib time decrease.
cold memory loads happen mainly on Brain/debug.
```

After PR 6:

```text
trace disabled mode produces no large trace payload.
full debug can still show trace when enabled.
```

## Behavior regression checklist

For every benchmark, verify:

```text
[ ] dead NPC state normalized;
[ ] left_zone terminal projection correct;
[ ] target death confirmation requires direct confirmation;
[ ] corpse loot still works;
[ ] witnesses can report corpse locations;
[ ] killers can confirm corpse and then leave;
[ ] NPCs can still buy/consume water/food;
[ ] no critical thirst self-interrupt loop;
[ ] story_events remain meaningful.
```

## Required commands

Focused:

```bash
pytest backend/tests/decision/v3/test_memory_event_policy.py -q
pytest backend/tests/decision/v3/test_memory_incremental_eviction.py -q
pytest backend/tests/decision/v3/test_knowledge_tables.py -q
pytest backend/tests/decision/test_context_builder_cache.py -q
pytest backend/tests/decision/v3/test_cold_memory_store.py -q
pytest backend/tests/decision/v3/test_debug_trace.py -q
```

Full:

```bash
pytest backend/tests -k "not e2e" -q
pytest backend/tests/decision/v3/test_e2e_brain_v3_goals.py -q
pytest backend/tests/decision/v3/test_hunt_leads.py -q
pytest backend/tests/decision/v3/test_hunt_fixes.py -q
pytest backend/tests/decision/v3/test_hunt_kill_stalker_goal.py -q
```

Frontend:

```bash
cd frontend
npm run build
```

## Definition of Done

```text
[ ] Analyzer can summarize memory exports.
[ ] Metrics cover memory writes, retrieval, context builder and cold store.
[ ] Benchmark scripts can run 10/50/100 NPC scenarios.
[ ] Operation-count tests prevent regressions.
[ ] Manual benchmark report shows progress after each PR.
[ ] Gameplay correctness checks remain green.
```
