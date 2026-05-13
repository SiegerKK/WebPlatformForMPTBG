# Memory Optimization PR 9 — Knowledge-first Hunt Beliefs and Context Consumers

## Goal

Migrate readers from `memory_v3` scans to structured knowledge created in PR8.

PR8 changes the write side. This PR changes the read side:

```text
target_beliefs.py
context_builder.py
knowledge_builder.py
planner/hunt consumers
debug projections
```

After this PR, hunt logic should use `knowledge_v1.known_npcs`, `known_corpses`, and `hunt_evidence` as primary sources. `memory_v3` should be a legacy fallback only.

## Why this PR exists

The current hunt pipeline still scans `memory_v3` for target leads. If PR8 simply stops writing `target_seen` / `corpse_seen` / `target_last_known_location` records, hunt behavior can regress.

This PR makes the consumers knowledge-first so PR10 can safely disable routine observation memory writes.

## Dependencies

Requires:

```text
PR8 — Knowledge-first NPC Observations and Corpse Evidence
```

Requires existing systems:

```text
PR3 known_npcs
PR4 context cache
PR5 cold memory store
```

## Scope

In scope:

```text
1. Build hunt leads from knowledge_v1.
2. Build target belief from knowledge-first sources.
3. Make recent target contact knowledge-first.
4. Make context_builder use knowledge/hunt_evidence without scanning memory for normal cases.
5. Keep memory_v3 fallback for legacy states.
6. Add metrics for memory fallback usage.
7. Add tests proving hunt still works when memory_v3 observation records are absent.
```

Out of scope:

```text
turning off compat memory writes completely — PR10;
large redesign of hunt scoring;
new pathfinding;
new combat AI;
fixing corpse lifecycle bugs outside stale-corpse guard.
```

## Current problem

`target_beliefs.py` currently builds leads from `memory_v3` records. It maps memory kinds such as:

```text
target_seen
target_last_known_location
target_intel
target_moved
target_route_observed
target_not_found
target_corpse_reported
target_corpse_seen
target_death_confirmed
```

into `HuntLead` and `TargetBelief`.

This means observation records cannot be removed from memory until an equivalent knowledge-first path exists.

## Target architecture

`build_target_belief(...)` should use sources in this priority order:

```text
1. Direct visibility in current AgentContext / BeliefState.
2. knowledge_v1.known_npcs[target_id].
3. knowledge_v1.hunt_evidence[target_id].
4. knowledge_v1.known_corpses relevant to target_id.
5. memory_v3 legacy fallback, only when structured knowledge is missing.
6. omniscient debug fallback, only when debug flag is enabled.
```

`memory_v3` fallback must be counted with metrics so PR10 can verify it is no longer needed.

## Data interpretation rules

### 1. Direct visibility

Direct visibility always wins:

```text
visible target → target_seen lead, confidence 1.0, freshness 1.0
```

It should also contradict stale death evidence:

```text
if known_npcs[target_id].death_evidence.status in {reported_dead, corpse_seen}
and target is visible alive:
    mark death_evidence.status = contradicted
    set is_alive = true
    bump major_revision
```

### 2. known_npcs target entry

Map:

```text
known_npcs[target_id].last_seen_location_id → target_last_known_location lead
known_npcs[target_id].last_seen_turn → lead.created_turn
known_npcs[target_id].confidence/effective_confidence → lead.confidence
known_npcs[target_id].is_alive / death_evidence → TargetBelief.is_alive
known_npcs[target_id].equipment_summary → equipment_known / combat_strength estimate
```

### 3. death evidence

Map:

```text
death_evidence.status = reported_dead
  → weak target_last_known_location/death report lead, not kill confirmation

death_evidence.status = corpse_seen
  → strong corpse lead, target likely dead, not necessarily objective completion unless direct/target-specific rules say so

death_evidence.status = confirmed_dead
  → target death confirmed

death_evidence.status = contradicted
  → ignore death evidence, prefer alive observation
```

### 4. hunt_evidence

Map:

```text
hunt_evidence[target_id].last_seen → target_seen / target_last_known_location lead
hunt_evidence[target_id].route_hints → target_route_observed leads
hunt_evidence[target_id].failed_search_locations → target_not_found / exhausted locations
hunt_evidence[target_id].death → death/corpse leads
hunt_evidence[target_id].recent_contact → recently_seen fields
```

### 5. known_corpses

If `known_corpses[corpse_id].dead_agent_id == target_id`:

```text
valid corpse → death/corpse lead
stale corpse → ignore
corpse for alive target → ignore and metric
```

## Implementation plan

### 1. Add `knowledge_hunt_builder.py`

New module:

```text
backend/app/games/zone_stalkers/knowledge/knowledge_hunt_builder.py
```

Public API:

```python
def build_hunt_leads_from_knowledge(
    *,
    agent: dict[str, Any],
    target_id: str,
    world_turn: int,
) -> list[HuntLead]:
    ...
```

```python
def build_recent_target_contact_from_knowledge(
    *,
    agent: dict[str, Any],
    target_id: str,
    world_turn: int,
) -> dict[str, Any] | None:
    ...
```

```python
def build_equipment_belief_from_knowledge(
    *,
    agent: dict[str, Any],
    target_id: str,
    world_turn: int,
) -> dict[str, Any]:
    ...
```

### 2. Refactor `target_beliefs.py`

Current broad shape should become:

```python
leads = []

visible_lead = build_visible_lead(...)
if visible_lead:
    leads.append(visible_lead)

knowledge_leads = build_hunt_leads_from_knowledge(...)
leads.extend(knowledge_leads)

if not knowledge_has_sufficient_target_signal(...):
    legacy_leads = build_hunt_leads_from_memory_v3(...)
    leads.extend(legacy_leads)
    metrics["target_belief_memory_fallbacks"] += 1
```

Do not remove the existing memory lead conversion immediately. Move it behind a helper:

```python
def build_hunt_leads_from_memory_v3_legacy(...):
    ...
```

### 3. Define “sufficient target signal”

Memory fallback should run only if structured knowledge lacks useful data:

```text
no known_npcs[target_id]
AND no hunt_evidence[target_id]
AND no known_corpses for target_id
```

If known_npcs has target but low confidence, fallback can be allowed in compat mode:

```text
if effective_confidence < 0.05 and legacy fallback enabled
```

### 4. Recent contact

Current recent contact is computed by scanning memory records for recent `target_seen`. Replace with:

```text
visible target → recent_contact age 0
hunt_evidence[target_id].recent_contact → recent_contact
known_npcs[target_id].last_direct_seen_turn within RECENT_TARGET_CONTACT_TURNS → recent_contact
legacy memory fallback only if no structured recent contact exists
```

### 5. Equipment and combat strength

Use:

```text
known_npcs[target_id].equipment_summary
known_npcs[target_id].threat_level
known_npcs[target_id].combat_strength_estimate
```

Fallback to memory only if missing.

### 6. Context builder changes

`context_builder._build_derived_context_parts(...)` currently merges knowledge and memory fallback. Keep fallback, but prefer knowledge-first and avoid memory scans when possible.

Add helper:

```python
def _should_scan_memory_for_context(agent: dict[str, Any]) -> bool:
    ...
```

Rules:

```text
If knowledge_v1 exists and has known_npcs/known_locations/known_traders/hazards:
    do not scan memory for routine known_entities/known_locations.

If legacy state has no knowledge_v1:
    scan memory.

If deep_debug=True:
    allow memory scan.

If target_id exists and hunt_evidence missing:
    memory fallback allowed until PR10.
```

Update metrics:

```python
context_builder_memory_fallbacks
context_builder_memory_fallback_records_scanned
context_builder_knowledge_primary_hits
```

### 7. Cache key revision

After PR8, `knowledge_v1` may have minor refreshes. `brain_context_cache` should not invalidate on every repeated observation.

Update cache key logic:

```text
use knowledge.major_revision for derived context invalidation;
keep knowledge.revision as debug/stats only;
memory_revision still included while memory fallback remains enabled.
```

If this is too risky in one PR, add feature flag:

```python
CONTEXT_CACHE_USE_KNOWLEDGE_MAJOR_REVISION = True
```

### 8. Debug projections

Add compact target knowledge block in full debug profile:

```json
"target_knowledge": {
  "target_id": "agent_debug_12",
  "known_npc": {... compact ...},
  "hunt_evidence": {... compact ...},
  "legacy_memory_fallback_used": false,
  "lead_sources": {
    "visible": 1,
    "knowledge": 3,
    "memory_v3": 0,
    "debug_state": 0
  }
}
```

Do not dump full known_npcs in lightweight projections.

## Tests

Add:

```text
backend/tests/decision/v3/test_target_beliefs_knowledge_first.py
backend/tests/decision/test_context_builder_knowledge_first.py
```

Required target belief tests:

```python
def test_target_belief_uses_known_npc_last_seen_without_memory_records(): ...
def test_target_belief_uses_hunt_evidence_last_seen_without_memory_records(): ...
def test_target_belief_uses_known_corpse_as_death_evidence(): ...
def test_target_belief_ignores_stale_corpse_for_alive_target(): ...
def test_visible_alive_target_contradicts_reported_dead_knowledge(): ...
def test_recently_seen_uses_hunt_evidence_recent_contact_without_memory_records(): ...
def test_equipment_known_uses_known_npc_equipment_summary(): ...
def test_failed_search_locations_from_hunt_evidence_suppress_exhausted_location(): ...
def test_route_hints_from_hunt_evidence_without_memory_records(): ...
def test_legacy_memory_fallback_used_when_knowledge_missing(): ...
def test_no_memory_scan_when_knowledge_is_sufficient(monkeypatch): ...
```

Required context builder tests:

```python
def test_context_builder_uses_known_npcs_without_memory_scan(): ...
def test_context_builder_uses_known_traders_without_memory_scan(): ...
def test_context_builder_uses_known_corpses_for_corpse_leads(): ...
def test_context_builder_memory_fallback_for_legacy_agent(): ...
def test_context_builder_cache_not_invalidated_by_minor_observation_refresh(): ...
def test_context_builder_cache_invalidated_by_major_location_change(): ...
```

## Metrics

Add per-run counters:

```python
target_belief_knowledge_leads
target_belief_memory_leads
target_belief_memory_fallbacks
target_belief_memory_records_scanned
target_belief_known_npc_hits
target_belief_known_corpse_hits
target_belief_hunt_evidence_hits
context_builder_knowledge_primary_hits
context_builder_memory_fallbacks
context_builder_memory_fallback_records_scanned
```

Acceptance targets after PR9 in a 40-NPC 24h run:

```text
target_belief_memory_fallbacks should be near 0 for new saves;
context_builder_memory_fallbacks should be near 0 for new saves;
legacy fallback should still work for old saves/tests.
```

## Migration / compatibility

Do not delete memory fallback in this PR.

Compatibility stages:

```text
Stage 1 — PR8 writes knowledge and keeps selected legacy memory records.
Stage 2 — PR9 reads knowledge first and falls back to memory.
Stage 3 — PR10 disables routine memory records and validates fallback not used in new saves.
```

For old saves:

```text
If knowledge_v1 missing or empty, target_beliefs scans memory_v3 exactly like before.
If knowledge exists but lacks hunt_evidence, memory fallback may fill missing target leads.
```

## Performance acceptance criteria

Run long simulation and compare before/after:

```text
context_builder_memory_scan_records per decision
context_builder_cache_hit_rate
target_belief_memory_records_scanned
target_belief_memory_fallbacks
memory_write_dropped
memory_evictions/tick
```

Expected:

```text
context/hunt memory scans drop substantially;
cache hit rate improves or remains stable;
hunt behavior does not regress;
kill/corpse target tracking still works.
```

## Definition of Done

```text
[ ] target_beliefs builds useful leads from knowledge_v1 without memory records.
[ ] recent target contact works without memory_v3 target_seen records.
[ ] corpse/death evidence works from known_corpses/known_npcs.
[ ] failed-search/exhausted-location logic works from hunt_evidence.
[ ] context_builder avoids memory scan when knowledge is sufficient.
[ ] legacy memory fallback remains available and tested.
[ ] metrics expose knowledge-vs-memory source usage.
[ ] No regression in hunt, kill confirmation, corpse reporting, target search tests.
```
