# Memory Optimization PR 1 — Write Policy, Spam Control and Noisy Index Caps

## Goal

Reduce memory write churn before changing storage architecture.

This PR must stop the main sources of memory spam:

```text
repeated stalkers_seen;
repeated travel_hop;
repeated objective_decision with same objective;
active_plan failure/repair/abort loops;
oversized summaries/details/lists;
broad by_tag buckets.
```

Do not introduce cold memory storage in this PR.

## Scope

In scope:

```text
1. Explicit memory event policy.
2. active_plan failure aggregation.
3. stalkers_seen compression into semantic/crowd aggregate.
4. travel_hop aggregation into semantic_route_traveled.
5. routine objective_decision dedup/aggregation.
6. summary/details/list length limits.
7. by_tag denylist + bucket cap.
8. memory write metrics.
9. tests proving memory remains useful.
```

Out of scope:

```text
cold memory store;
external DB-backed memory;
vector search;
rewriting Brain v3;
changing target death confirmation semantics;
increasing MEMORY_V3_MAX_RECORDS.
```

## Files to inspect

```text
backend/app/games/zone_stalkers/memory/memory_events.py
backend/app/games/zone_stalkers/memory/store.py
backend/app/games/zone_stalkers/memory/retrieval.py
backend/app/games/zone_stalkers/rules/tick_rules.py
backend/app/games/zone_stalkers/decision/active_plan_runtime.py
backend/app/games/zone_stalkers/decision/plan_monitor.py
backend/app/games/zone_stalkers/projections.py
```

## 1. Explicit memory event policy

Replace ad-hoc checks with a central policy:

```python
MEMORY_EVENT_POLICY = {
    # Debug/trace only.
    "active_plan_created": "trace_only",
    "active_plan_step_started": "trace_only",
    "active_plan_step_completed": "trace_only",
    "active_plan_completed": "trace_only",
    "active_plan_repaired": "trace_only",
    "sleep_interval_applied": "trace_only",

    # Aggregate, not separate records.
    "active_plan_step_failed": "memory_aggregate",
    "active_plan_repair_requested": "memory_aggregate",
    "active_plan_aborted": "memory_aggregate",
    "plan_monitor_abort": "memory_aggregate",
    "support_source_exhausted": "memory_aggregate",
    "anomaly_search_exhausted": "memory_aggregate",
    "witness_source_exhausted": "memory_aggregate",
    "no_tracks_found": "memory_aggregate",
    "no_witnesses": "memory_aggregate",

    # Knowledge update + optional compact event.
    "stalkers_seen": "knowledge_upsert",
    "target_seen": "knowledge_upsert",
    "target_last_known_location": "knowledge_upsert",
    "target_corpse_reported": "knowledge_upsert",
    "corpse_seen": "knowledge_upsert",
    "trader_seen": "knowledge_upsert",
    "location_visited": "knowledge_upsert",
    "travel_hop": "knowledge_upsert",

    # Important episodic memory.
    "death": "memory_critical",
    "combat_kill": "memory_critical",
    "combat_killed": "memory_critical",
    "target_death_confirmed": "memory_critical",
    "global_goal_completed": "memory_critical",
    "left_zone": "memory_critical",
    "emission_started": "memory_critical",
    "emission_warning": "memory_critical",
    "emission_ended": "memory_critical",
    "rare_artifact_found": "memory_critical",
}
```

Add:

```python
def resolve_memory_event_policy(action_kind: str, effects: dict[str, Any]) -> str:
    ...
```

Write routing:

```text
trace_only        → debug trace only
knowledge_upsert → update knowledge/aggregate, usually no episodic record
memory_aggregate → update one aggregate record
memory_critical  → always write memory_v3 record
discard          → write nothing
```

## 2. active_plan failure aggregation

Aggregate repeated failures into one record:

```json
{
  "kind": "active_plan_failure_summary",
  "layer": "goal",
  "details": {
    "objective_key": "FIND_ARTIFACTS",
    "step_kind": "travel_to_location",
    "reason": "support_source_exhausted",
    "failed_count": 41,
    "repair_requested_count": 41,
    "repaired_count": 25,
    "aborted_count": 16,
    "first_turn": 11000,
    "last_turn": 12300,
    "last_plan_id": "..."
  }
}
```

Signature:

```text
objective_key + step_kind + reason
```

Cap:

```python
MAX_ACTIVE_PLAN_FAILURE_AGGREGATES = 20
```

Acceptance:

```text
[ ] repeated plan failure/repair events update one aggregate;
[ ] aggregate counters are correct;
[ ] important failure information remains visible in story/debug;
[ ] active_plan lifecycle/failure does not dominate memory_v3.
```

## 3. stalkers_seen compression

Do not store repeated full lists as primary memory.

On `stalkers_seen`:

```text
1. update semantic_stalkers_seen or crowd_seen aggregate;
2. update provisional known_npcs-lite if PR 3 is not ready yet;
3. store only capped episodic samples;
4. limit list sizes in details/summary.
```

Suggested constants:

```python
STALKERS_SEEN_DEDUP_WINDOW_TURNS = 60
STALKERS_SEEN_MAX_EPISODIC_PER_LOCATION = 5
MEMORY_V3_MAX_STALKERS_SEEN_RECORDS = 75
MAX_STALKERS_SEEN_NAMES_IN_SUMMARY = 5
MAX_STALKERS_SEEN_ENTITY_IDS_IN_RECORD = 10
```

For large groups:

```json
{
  "kind": "crowd_seen",
  "layer": "semantic",
  "location_id": "loc_A",
  "summary": "В локации замечены 17 сталкеров.",
  "details": {
    "count": 17,
    "sample_ids": ["agent_1", "agent_2", "agent_3"],
    "sample_names": ["Сталкер #1", "Сталкер #2", "Сталкер #3"],
    "first_seen_turn": 12000,
    "last_seen_turn": 12450,
    "times_seen": 24
  }
}
```

Acceptance:

```text
[ ] 200 repeated stalkers_seen events do not produce 200 episodic records.
[ ] memory_v3 keeps <= MEMORY_V3_MAX_STALKERS_SEEN_RECORDS stalkers_seen records.
[ ] semantic_stalkers_seen / crowd_seen has times_seen and last_seen_turn.
[ ] context_builder still has enough data to know nearby/known NPCs.
```

## 4. travel_hop aggregation

Create/update:

```json
{
  "kind": "semantic_route_traveled",
  "layer": "spatial",
  "details": {
    "from_location_id": "loc_A",
    "to_location_id": "loc_B",
    "times_traveled": 12,
    "last_traveled_turn": 12450,
    "known_safe": true,
    "known_risky": false
  }
}
```

## 5. objective_decision dedup

Routine repeated decisions should not create endless records.

Policy:

```text
same objective_key + same intent_kind + same reason class within N turns
⇒ update objective_decision_summary aggregate
⇒ do not create a new episodic record
```

Always keep episodic record for:

```text
objective changed;
global goal changed;
urgent survival/emission/combat;
target/corpse/kill confirmation;
first time choosing an objective after long gap.
```

## 6. summary/details/list length limits

Add sanitizer before persisting record:

```python
MEMORY_SUMMARY_MAX_CHARS = 240
MEMORY_DETAILS_STRING_MAX_CHARS = 160
MEMORY_DETAILS_LIST_MAX_ITEMS = 5
MEMORY_DETAILS_CRITICAL_LIST_MAX_ITEMS = 20
```

Do not truncate critical IDs:

```text
target_id
corpse_id
location_id
killer_id
combat_id
objective_key
```

## 7. by_tag denylist and cap

In `store.py`:

```python
DO_NOT_INDEX_TAGS = {
    "active_plan",
    "repair",
    "step",
    "objective",
    "decision",
    "routine",
}

DO_NOT_INDEX_TAG_PREFIXES = (
    "objective:",
    "step:",
    "repair:",
)

MAX_TAG_BUCKET_SIZE = 64
```

When bucket is full, skip indexing newest low-importance tag reference or remove oldest reference in that bucket. Do not drop the memory record only because a tag bucket is full.

## 8. Metrics

Add/update:

```text
memory_write_attempts
memory_write_written
memory_write_discarded
memory_write_trace_only
memory_write_aggregated
memory_write_knowledge_upserts
memory_write_critical
memory_evictions
memory_by_tag_refs
memory_by_tag_skipped_refs
memory_summary_truncations
memory_details_truncations
```

## Tests

Add/extend:

```text
backend/tests/decision/v3/test_memory_event_policy.py
backend/tests/decision/v3/test_memory_events.py
backend/tests/decision/v3/test_memory_store.py
backend/tests/decision/v3/test_memory_composition_long_run.py
backend/tests/test_zone_stalkers_projections.py
```

Required tests:

```python
def test_active_plan_failures_are_aggregated(): ...
def test_repeated_stalkers_seen_keeps_episodic_under_budget(): ...
def test_crowd_seen_summary_is_bounded(): ...
def test_repeated_travel_hop_updates_route_aggregate(): ...
def test_objective_decision_routine_repeats_are_aggregated(): ...
def test_by_tag_denylist_skips_noisy_tags(): ...
def test_by_tag_bucket_size_is_capped(): ...
def test_memory_summary_and_details_are_truncated_safely(): ...
def test_critical_target_death_confirmed_is_never_discarded(): ...
```

## Validation commands

```bash
pytest backend/tests/decision/v3/test_memory_event_policy.py -q
pytest backend/tests/decision/v3/test_memory_events.py -q
pytest backend/tests/decision/v3/test_memory_store.py -q
pytest backend/tests/decision/v3/test_memory_composition_long_run.py -q
pytest backend/tests/test_zone_stalkers_projections.py -q
pytest backend/tests -k "not e2e" -q
```

## Definition of Done

```text
[ ] Write policy is explicit.
[ ] Repeated social observations are compressed.
[ ] Plan failure loops are aggregated.
[ ] Noisy tags are bounded.
[ ] Memory payload fields are bounded.
[ ] Important gameplay memories are preserved.
[ ] Metrics show written vs aggregated vs discarded.
[ ] Full backend non-e2e tests pass.
```
