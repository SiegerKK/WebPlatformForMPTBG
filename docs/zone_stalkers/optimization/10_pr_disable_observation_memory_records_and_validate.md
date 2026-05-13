# Memory Optimization PR 10 — Disable Routine Observation Memory Records and Validate Long-run Behavior

## Goal

Complete the cutover to the knowledge-first model by disabling routine NPC/corpse observation writes to `memory_v3` and validating long-run performance/correctness.

After this PR:

```text
stalkers_seen / routine NPC observations → knowledge only
corpse_seen / repeated corpse observations → knowledge only
hunt/context readers → knowledge-first
memory_v3 → meaningful episodes, critical events, goal/action history only
```

## Dependencies

Requires:

```text
PR8 — Knowledge-first NPC Observations and Corpse Evidence
PR9 — Knowledge-first Hunt Beliefs and Context Consumers
```

Should also include or be preceded by:

```text
trade_sell_item no-sellable-items fix;
pending active-plan timeout fix;
stale corpse lifecycle cleanup.
```

## Scope

In scope:

```text
1. Turn off routine observation memory compatibility writes.
2. Add strict memory policy tests for observation kinds.
3. Add stale corpse cleanup metrics and validation.
4. Add long-run regression benchmark/export checks.
5. Add debug UI/projection summaries for knowledge-first observation state.
6. Validate 12h/24h/48h/72h behavior against previous metrics.
```

Out of scope:

```text
new gameplay economy design;
new survival fallback design;
major UI redesign;
removing memory_v3 entirely.
```

## Policy cutover

Set defaults:

```python
KNOWLEDGE_FIRST_OBSERVATIONS_ENABLED = True
OBSERVATION_MEMORY_COMPAT_MODE = False
```

For routine observations, expected result:

```text
stalkers_seen → no memory_v3 record
same-location repeated NPC seen → no memory_v3 record
corpse_seen repeated same corpse → no memory_v3 record
target_last_known_location routine refresh → no memory_v3 record
trader_seen repeated same trader → no memory_v3 record
```

Allowed memory milestones:

```text
npc_observation_milestone
npc_alive_contradicts_death_report
npc_equipment_changed
kill_target_seen
kill_target_corpse_confirmed
target_death_confirmed
rare_artifact_found
global_goal_completed
combat_kill / combat_killed
death
emission_started / emission_warning / emission_ended
major active_plan failure summaries
trade_sell_failed cooldown records, until trade logic is fully knowledge/rule-based
```

## Strict memory policy

Add explicit policy classes:

```text
knowledge_only
knowledge_milestone
memory_critical
memory_aggregate
memory
trace_only
discard
```

Semantics:

```text
knowledge_only:
  update knowledge and return, never create memory_v3 record.

knowledge_milestone:
  update knowledge;
  write memory_v3 only if milestone predicate returns true.

memory_critical:
  always write memory_v3.

memory_aggregate:
  update bounded aggregate record.
```

Do not allow ambiguous behavior where `knowledge_upsert` sometimes silently also writes ordinary memory records.

## Milestone predicates

Create helper:

```python
def should_write_observation_milestone(
    *,
    event_kind: str,
    update_result: dict[str, Any],
    agent: dict[str, Any],
    effects: dict[str, Any],
    world_turn: int,
) -> bool:
    ...
```

Return `True` only for:

```text
new current kill target evidence;
known alive/dead status changed;
death evidence became direct/confirmed;
location of current target changed materially;
first encounter with NPC if debug/story flag enabled;
equipment/threat changed materially for enemy/target;
contradiction resolved.
```

Return `False` for:

```text
same NPC seen again in same location;
same corpse seen again;
same trader seen again;
same crowd seen again;
minor last_seen_turn refresh;
minor confidence refresh.
```

## Stale corpse cleanup

Add a world-level cleanup pass before observation writes or during tick maintenance:

```python
def cleanup_stale_corpses(state: dict[str, Any]) -> dict[str, int]:
    ...
```

Rules:

```text
If corpse.dead_agent_id points to an agent that is alive:
    remove corpse object or mark corpse.is_stale = true;
    do not allow corpse_seen for it;
    increment stale_corpse_removed / stale_corpse_ignored.

If corpse has no dead_agent_id:
    allow generic corpse object but do not mark any known_npc dead.

If agent is dead and corpse is valid:
    allow known_corpses update.
```

Metrics:

```python
stale_corpse_removed
stale_corpse_seen_ignored
corpse_seen_alive_agent_ignored
valid_corpse_seen_knowledge_updates
```

## Validation metrics

Add a compact export section:

```json
"knowledge_first_metrics": {
  "knowledge_only_events": 12345,
  "observation_memory_records_written": 42,
  "stalkers_seen_memory_records_written": 0,
  "corpse_seen_memory_records_written": 3,
  "target_belief_memory_fallbacks": 0,
  "context_builder_memory_fallbacks": 0,
  "stale_corpse_seen_ignored": 0,
  "corpse_seen_alive_agent_ignored": 0,
  "memory_evictions_per_tick": 1.2,
  "memory_drops_per_tick": 0.0
}
```

Expose in:

```text
full debug projection
summary export JSON
benchmark output
```

Do not include full known_npcs/known_corpses in lightweight projections.

## Long-run benchmark protocol

Run the same 40-NPC scenario at:

```text
12h
24h
48h
60h
72h
```

Compare against previous baseline:

```text
memory records total
records per NPC
NPCs at cap 500
memory evictions
memory_write_dropped
stalkers_seen records
semantic_stalkers_seen records
corpse_seen records
corpse_seen against alive agents
target_belief_memory_fallbacks
context_builder_memory_fallbacks
effective speed
ZIP/unpacked export size
oldest remembered turn
```

Expected after cutover:

```text
stalkers_seen memory records near 0;
corpse_seen routine memory records near 0;
corpse_seen against alive agents = 0 or ignored/cleaned;
memory_write_dropped remains 0 longer than baseline;
NPCs reach memory cap later;
oldest remembered turn remains stable longer;
effective speed improves or degrades slower;
hunt behavior remains functional.
```

## Required tests

Add:

```text
backend/tests/decision/v3/test_observation_memory_cutover.py
backend/tests/decision/v3/test_stale_corpse_cleanup.py
backend/tests/decision/v3/test_knowledge_first_longrun_metrics.py
```

Tests:

```python
def test_stalkers_seen_writes_zero_memory_records_with_compat_off(): ...
def test_repeated_stalkers_seen_updates_known_npcs_only(): ...
def test_corpse_seen_writes_zero_memory_records_for_repeated_valid_corpse(): ...
def test_kill_target_corpse_seen_writes_milestone_once(): ...
def test_target_seen_for_non_target_is_knowledge_only(): ...
def test_target_seen_for_kill_target_writes_bounded_milestone(): ...
def test_same_target_seen_repeated_does_not_write_more_milestones(): ...
def test_stale_corpse_for_alive_agent_removed_before_observation(): ...
def test_stale_corpse_for_alive_agent_does_not_mark_known_npc_dead(): ...
def test_target_belief_does_not_need_memory_fallback_in_new_save(): ...
def test_context_builder_does_not_scan_memory_for_known_entities_in_new_save(): ...
def test_legacy_save_still_uses_memory_fallback(): ...
def test_observation_memory_metrics_exposed_in_summary(): ...
def test_knowledge_major_revision_not_bumped_by_minor_refresh(): ...
def test_context_cache_survives_minor_observation_refresh(): ...
```

## Regression tests to keep green

Run existing suites:

```bash
pytest backend/tests/decision/v3/test_knowledge_tables.py -q
pytest backend/tests/decision/test_context_builder_knowledge.py -q
pytest backend/tests/decision/test_context_builder_cache.py -q
pytest backend/tests/decision/v3/test_target_beliefs.py -q
pytest backend/tests/decision/v3/test_hunt_leads.py -q
pytest backend/tests/decision/v3/test_hunt_fixes.py -q
pytest backend/tests/decision/v3/test_hunt_kill_stalker_goal.py -q
pytest backend/tests/decision/v3/test_memory_event_policy.py -q
pytest backend/tests/decision/v3/test_memory_incremental_eviction.py -q
pytest backend/tests/decision/v3/test_cold_memory_store.py -q
pytest backend/tests -k "not e2e" -q
```

## Acceptance criteria

### Correctness

```text
[ ] NPCs still learn where other NPCs were last seen.
[ ] NPCs still know whether a target is alive/dead when evidence exists.
[ ] Hunt target belief works without memory_v3 target/corpse observation records.
[ ] Kill confirmation still requires valid direct/critical evidence.
[ ] Witness/rumor corpse reports do not incorrectly complete kill goals.
[ ] Stale corpse objects for living agents cannot poison knowledge.
[ ] Legacy saves with memory_v3 but no knowledge_v1 still work.
```

### Performance

```text
[ ] Routine stalker/corpse observations no longer fill memory_v3.
[ ] memory_write_dropped is significantly lower than baseline at 72h.
[ ] memory_evictions/tick is significantly lower than baseline.
[ ] target_belief memory fallback is near zero in new saves.
[ ] context_builder memory fallback is near zero in new saves.
[ ] effective speed improves or degrades slower than baseline.
```

### Debuggability

```text
[ ] Full debug profile shows known_npcs summary.
[ ] Full debug profile shows known_corpses summary.
[ ] Full debug profile shows hunt_evidence summary for current target.
[ ] Metrics show how many observation memory records were suppressed.
[ ] Stale corpse cleanup metrics are visible.
```

## Definition of Done

```text
[ ] Observation memory compat mode is off by default.
[ ] Routine NPC/corpse observations are knowledge-only.
[ ] Only meaningful milestones produce memory_v3 observation records.
[ ] Hunt/context consumers do not require memory_v3 observation records in new saves.
[ ] Stale corpse cleanup is implemented and tested.
[ ] Long-run benchmark shows reduced memory pressure.
[ ] All existing hunt/context/memory/cold-store tests pass.
```
