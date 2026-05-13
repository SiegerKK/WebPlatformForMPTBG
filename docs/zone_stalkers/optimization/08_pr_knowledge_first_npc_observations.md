# Memory Optimization PR 8 — Knowledge-first NPC Observations and Corpse Evidence

## Goal

Complete the migration started by PR3: routine observations about NPCs and corpses must update compact structured knowledge instead of consuming `memory_v3` record budget.

This PR focuses on the write side:

```text
stalkers_seen
semantic_stalkers_seen
target_seen
target_last_known_location
corpse_seen
target_corpse_seen
target_corpse_reported
trader_seen
```

These events should become knowledge-first updates. `memory_v3` should keep only rare, meaningful, story/debug-critical episodes.

## Why this PR exists

Long-run NPC logs show that routine observation events dominate memory pressure:

```text
stalkers_seen / semantic_stalkers_seen — repeated social observation spam
corpse_seen — repeated threat/corpse observation spam
```

At 72h, nearly all living NPCs are close to the memory cap, many are at cap, and memory writes are being dropped. Moving routine observation facts to `knowledge_v1` should reduce:

```text
memory_write_attempts
memory_evictions
memory_write_dropped
context_builder_memory_scan_records
target_belief memory scans
cold memory blob size
debug export size
```

## Dependencies

Requires:

```text
PR1 — explicit memory write policy
PR2 — incremental eviction/indexing
PR3 — knowledge tables / known_npcs
PR4 — context builder cache
PR5 — cold memory store
```

Should be done after or alongside bugfixes for:

```text
trade_sell_item without sellable items
pending step timeouts
stale corpse objects pointing to living agents
```

The corpse consistency bug can be partly mitigated here, but the lifecycle bug itself must be fixed in world/corpse generation code.

## Scope

In scope:

```text
1. Expand knowledge_v1 schema for NPC observations and corpse evidence.
2. Add known_corpses table.
3. Make routine stalker/NPC observations pure knowledge upserts.
4. Make routine corpse observations pure knowledge upserts.
5. Keep critical transition events in memory_v3 only when they are meaningful.
6. Add stale-corpse safety checks before knowledge/memory write.
7. Add metrics to prove the reduction in memory pressure.
8. Preserve legacy fallback while readers are migrated in PR9.
```

Out of scope:

```text
full rewrite of target_beliefs consumers — PR9;
final removal of memory_v3 fallback scans — PR10;
new social AI / faction diplomacy;
vector memory;
pathfinding;
economy/water/trade fallback fixes.
```

## High-level architecture

After this PR:

```text
memory_v3
  = rare meaningful episodes, goal history, critical deaths, rare artifacts, major failures.

knowledge_v1.known_npcs
  = current compact facts about known NPCs.

knowledge_v1.known_corpses
  = current compact facts about known corpse objects.

knowledge_v1.hunt_evidence
  = bounded target-specific evidence required by hunt logic.
```

Routine observations should not create repeated episodic memory records.

## Data model changes

### 1. Extend `knowledge_v1`

Current shape:

```json
{
  "revision": 0,
  "known_npcs": {},
  "known_locations": {},
  "known_traders": {},
  "known_hazards": {},
  "stats": {}
}
```

New shape:

```json
{
  "revision": 0,
  "major_revision": 0,
  "minor_revision": 0,
  "known_npcs": {},
  "known_corpses": {},
  "known_locations": {},
  "known_traders": {},
  "known_hazards": {},
  "hunt_evidence": {},
  "stats": {
    "known_npcs_count": 0,
    "detailed_known_npcs_count": 0,
    "known_corpses_count": 0,
    "hunt_evidence_targets_count": 0,
    "last_update_turn": 0,
    "last_major_update_turn": 0,
    "last_minor_update_turn": 0
  }
}
```

`revision` can remain as backwards-compatible alias. New code should distinguish:

```text
major_revision — meaningful context-affecting change;
minor_revision — repeated observation refresh that should not invalidate brain_context_cache every tick.
```

### 2. `known_npcs` extended schema

Each observer NPC owns its own view of another NPC:

```json
{
  "agent_debug_12": {
    "agent_id": "agent_debug_12",
    "name": "Сталкер #12",

    "last_seen_location_id": "loc_debug_61",
    "last_seen_turn": 4300,
    "last_direct_seen_turn": 4300,
    "last_reported_seen_turn": 4180,
    "last_seen_distance": 0,

    "is_alive": true,
    "alive_confidence": 0.95,
    "death_evidence": {
      "status": "alive",
      "corpse_id": null,
      "corpse_location_id": null,
      "observed_turn": null,
      "reported_turn": null,
      "death_cause": null,
      "killer_id": null,
      "source_agent_id": null,
      "confidence": 0.0,
      "directly_observed": false
    },

    "equipment_summary": {
      "weapon_class": "rifle",
      "armor_class": "medium",
      "detector_tier": 1,
      "combat_strength_estimate": 0.62,
      "last_observed_turn": 4300
    },

    "relation": "neutral",
    "relation_score": 0.0,
    "relation_updated_turn": 4300,
    "threat_level": 0.35,

    "source": "direct_observation",
    "confidence": 0.9,
    "detail_level": "detailed"
  }
}
```

Allowed `death_evidence.status` values:

```text
alive
reported_dead
corpse_seen
confirmed_dead
contradicted
unknown
```

Recommended precedence:

```text
direct visible alive observation > stale corpse object
confirmed death > direct corpse_seen > witness report > rumor
```

Important: seeing a living NPC after an old corpse report must clear or mark old death evidence as contradicted.

### 3. Add `known_corpses`

```json
{
  "corpse_agent_debug_22_4079": {
    "corpse_id": "corpse_agent_debug_22_4079",
    "dead_agent_id": "agent_debug_22",
    "dead_agent_name": "Сталкер #22",
    "location_id": "loc_debug_61",
    "first_seen_turn": 4080,
    "last_seen_turn": 4300,
    "seen_count": 12,
    "death_cause": "starvation_or_thirst",
    "killer_id": null,
    "source": "direct_observation",
    "confidence": 0.95,
    "is_stale": false,
    "stale_reason": null
  }
}
```

Caps:

```python
MAX_KNOWN_CORPSES_PER_AGENT = 80
MAX_DETAILED_KNOWN_CORPSES_PER_AGENT = 20
```

Drop/demote order:

```text
stale corpses first;
low-confidence old corpses;
corpses unrelated to kill target;
corpses not seen recently;
never drop current kill target corpse evidence while goal is active.
```

### 4. Add `hunt_evidence`

This PR adds only the shape and write helpers. PR9 will migrate readers.

```json
{
  "target_agent_id": {
    "target_id": "agent_debug_12",
    "last_seen": {
      "location_id": "loc_A",
      "turn": 4200,
      "confidence": 0.95,
      "source": "direct_observation"
    },
    "death": {
      "status": "corpse_seen",
      "corpse_id": "corpse_agent_debug_12_4100",
      "location_id": "loc_B",
      "turn": 4100,
      "confidence": 0.95,
      "source": "corpse_seen"
    },
    "route_hints": [],
    "failed_search_locations": {
      "loc_C": {
        "failed_search_count": 3,
        "last_failed_turn": 4250,
        "cooldown_until_turn": 4550,
        "confidence": 0.8
      }
    },
    "recent_contact": {
      "turn": 4300,
      "location_id": "loc_A"
    },
    "revision": 0
  }
}
```

## Write path changes

All changes should go through `write_memory_event_to_v3` or helpers called from it. Do not create a second memory/knowledge write path.

### 1. Add knowledge helper APIs

In `knowledge_store.py`:

```python
def upsert_known_npc_observation(
    agent: dict[str, Any],
    *,
    other_agent_id: str,
    name: str | None,
    location_id: str | None,
    world_turn: int,
    observed_agent: dict[str, Any] | None = None,
    confidence: float = 0.95,
    source: str = "direct_observation",
) -> dict[str, Any]:
    ...
```

```python
def upsert_known_corpse(
    agent: dict[str, Any],
    *,
    corpse_id: str,
    dead_agent_id: str | None,
    dead_agent_name: str | None,
    location_id: str | None,
    world_turn: int,
    death_cause: str | None = None,
    killer_id: str | None = None,
    source_agent_id: str | None = None,
    confidence: float = 0.95,
    directly_observed: bool = True,
) -> dict[str, Any]:
    ...
```

```python
def upsert_hunt_evidence_from_observation(
    agent: dict[str, Any],
    *,
    target_id: str,
    kind: str,
    location_id: str | None,
    world_turn: int,
    confidence: float,
    source: str,
    details: dict[str, Any] | None = None,
) -> None:
    ...
```

Each helper should return an update result:

```python
{
    "changed_major": bool,
    "changed_minor": bool,
    "created": bool,
    "reason": "new_entry" | "location_changed" | "death_status_changed" | "minor_refresh" | ...,
}
```

Use this result to bump `major_revision` only when context-affecting fields change.

### 2. `stalkers_seen` routing

Current behavior is partly knowledge-upsert, but still allows memory record pressure. Replace with:

```text
For every visible non-self NPC:
    upsert_known_npc_observation(...)

Do not write memory_v3 record by default.
```

Write a `memory_v3` record only for meaningful transitions:

```text
first time this observer sees this NPC;
NPC was previously believed dead and is now seen alive;
known location changed and NPC is kill target;
equipment class changed significantly;
relation/threat changed significantly;
first time seeing current kill target;
first time seeing an enemy / attacker;
debug flag force_observation_memory_records is enabled.
```

Recommended memory kind for transitions:

```text
npc_observation_milestone
```

Not:

```text
stalkers_seen every tick
semantic_stalkers_seen every location crowd update
```

### 3. `target_seen` routing

```text
upsert known_npcs[target_id]
upsert hunt_evidence[target_id].last_seen
```

Memory write only if:

```text
target just became visible after not being seen recently;
target location changed materially;
target is current kill target;
combat context requires evidence trail;
debug flag enabled.
```

### 4. `target_last_known_location` routing

```text
upsert known_npcs[target_id].last_seen_location_id
upsert hunt_evidence[target_id].last_seen
```

Memory write only for legacy compatibility while PR9 is not merged. After PR9, this should become knowledge-only except for critical milestones.

### 5. `corpse_seen` routing

Before any write, validate corpse consistency:

```python
if dead_agent_id and dead_agent_id in state_agents:
    live = state_agents[dead_agent_id]
    if live.get("is_alive", True):
        mark_stale_corpse(...)
        increment stale_corpse_seen_ignored
        do not upsert death evidence
        do not write memory_v3 corpse_seen
        return
```

If corpse is valid:

```text
upsert known_corpses[corpse_id]
upsert known_npcs[dead_agent_id].death_evidence
upsert hunt_evidence[dead_agent_id].death if relevant
```

Memory write only if:

```text
first time seeing this corpse;
corpse belongs to current kill target;
corpse confirms a kill objective;
death evidence contradicts previous alive belief;
corpse has killer_id/source_agent_id relevant to social/combat logic.
```

Never write repeated `corpse_seen` memory every time an NPC remains co-located with the same corpse.

### 6. `target_corpse_seen` routing

This is stronger than generic corpse_seen when target is known:

```text
upsert known_npcs[target_id].death_evidence.status = corpse_seen
upsert known_corpses[corpse_id]
upsert hunt_evidence[target_id].death
```

Memory write only if:

```text
current kill target corpse directly seen;
this event completes a kill confirmation flow;
first direct corpse evidence.
```

### 7. `target_corpse_reported` routing

This is weaker than direct corpse_seen:

```text
upsert known_npcs[target_id].death_evidence.status = reported_dead
upsert hunt_evidence[target_id].death with confidence <= 0.75
```

Must not complete kill goal by itself.

Memory write only for major rumor/intel milestone if needed for story/debug.

### 8. `trader_seen` routing

Keep as knowledge-upsert. Do not write repeated trader memory records.

If trader capabilities changed materially, write optional milestone:

```text
trader_capability_changed
```

## Memory policy changes

Update `MEMORY_EVENT_POLICY`:

```python
"stalkers": "knowledge_only",
"stalkers_seen": "knowledge_only",
"target_seen": "knowledge_only_or_milestone",
"target_last_known_location": "knowledge_only_or_legacy",
"corpse_seen": "knowledge_only_or_milestone",
"target_corpse_seen": "knowledge_only_or_milestone",
"target_corpse_reported": "knowledge_only_or_milestone",
"trader_seen": "knowledge_only",
```

Implementation can keep policy string `knowledge_upsert`, but behavior must be explicit:

```text
knowledge_upsert no longer implies ordinary memory_v3 write.
```

Add metrics:

```python
knowledge_upsert_attempts
knowledge_upsert_major_updates
knowledge_upsert_minor_refreshes
knowledge_only_events
observation_memory_milestones_written
stalkers_seen_memory_suppressed
corpse_seen_memory_suppressed
stale_corpse_seen_ignored
corpse_seen_alive_agent_ignored
hunt_evidence_upserts
```

## Revision rules

Avoid invalidating `brain_context_cache` every time NPC sees the same people in the same location.

Major update examples:

```text
new known NPC;
known NPC location changed;
known NPC alive/dead status changed;
current kill target evidence changed;
known trader location/capability changed;
corpse created/first seen;
corpse validity contradicted;
new hunt evidence affects target belief.
```

Minor refresh examples:

```text
same NPC seen again in same location;
same corpse seen again;
same trader seen again;
last_seen_turn refresh only.
```

`context_builder` should eventually key on `major_revision`, not `revision`, for expensive derived context.

This PR may keep current key behavior if PR4 cache tests require it, but must expose `major_revision` for PR9/PR10.

## Compatibility mode

Add config flag:

```python
KNOWLEDGE_FIRST_OBSERVATIONS_ENABLED = True
OBSERVATION_MEMORY_COMPAT_MODE = True
```

Modes:

```text
compat mode on:
  critical target/corpse leads may still write memory_v3 records for legacy target_beliefs.

compat mode off:
  routine observations are knowledge-only;
  target_beliefs must read knowledge/hunt_evidence from PR9.
```

For this PR, default should be safe:

```text
KNOWLEDGE_FIRST_OBSERVATIONS_ENABLED = True
OBSERVATION_MEMORY_COMPAT_MODE = True
```

After PR9 passes, PR10 will turn compat memory writes off.

## Tests

Add/extend:

```text
backend/tests/decision/v3/test_knowledge_first_observations.py
backend/tests/decision/v3/test_known_corpses.py
backend/tests/decision/v3/test_observation_memory_policy.py
```

Required tests:

```python
def test_stalkers_seen_updates_known_npcs_without_episodic_memory_spam(): ...
def test_repeated_same_stalkers_seen_is_minor_refresh_only(): ...
def test_new_known_npc_bumps_major_revision(): ...
def test_same_known_npc_same_location_does_not_bump_major_revision(): ...
def test_known_npc_location_change_bumps_major_revision(): ...
def test_target_seen_updates_known_npc_and_hunt_evidence(): ...
def test_target_seen_milestone_written_for_kill_target_only(): ...
def test_corpse_seen_updates_known_corpse_and_known_npc_death_evidence(): ...
def test_repeated_same_corpse_seen_does_not_write_memory_records(): ...
def test_corpse_seen_for_alive_agent_is_ignored_and_records_metric(): ...
def test_target_corpse_reported_is_reported_dead_not_confirmed_kill(): ...
def test_living_observation_contradicts_stale_death_evidence(): ...
def test_known_corpses_cap_keeps_kill_target_corpse(): ...
def test_observation_memory_compat_mode_preserves_legacy_target_lead_records(): ...
def test_observation_memory_compat_off_writes_no_routine_corpse_or_stalker_records(): ...
```

## Performance acceptance criteria

Use a 40-NPC long-run benchmark/export comparison.

At equal simulation duration, expect:

```text
stalkers_seen memory records: down by at least 80%
corpse_seen memory records: down by at least 80%
memory_write_attempts from observations: down significantly
memory_evictions/tick: lower
memory_write_dropped: lower or zero at 72h
known_npcs_count: stable and bounded
known_corpses_count: stable and bounded
```

Minimum acceptance for PR:

```text
[ ] repeated same-location stalker observations do not create repeated memory records;
[ ] repeated same corpse observations do not create repeated memory records;
[ ] stale corpse pointing at living agent is ignored;
[ ] knowledge entries contain the latest facts required for context/hunt readers;
[ ] legacy tests still pass while compat mode is on.
```

## Manual validation checklist

Run a 12h and 24h simulation with 40 NPCs:

```text
[ ] known_npcs grows instead of memory_v3 social spam.
[ ] known_corpses grows only for real corpses.
[ ] corpse_seen_alive_agent_ignored stays 0 in normal run.
[ ] If non-zero, stale corpse cleanup is required.
[ ] memory_write_dropped remains 0.
[ ] context_builder cache hit rate does not collapse due to minor revisions.
```

## Definition of Done

```text
[ ] Knowledge schema extended with known_corpses and hunt_evidence placeholders.
[ ] Routine NPC observations update known_npcs, not memory_v3.
[ ] Routine corpse observations update known_corpses/death_evidence, not memory_v3.
[ ] Critical milestones still produce memory_v3 records.
[ ] Repeated same observation is minor refresh only.
[ ] Stale corpse observations for living agents are ignored and counted.
[ ] Tests cover observation routing, revisions, caps, stale corpse safety, and compat mode.
[ ] Metrics prove reduction in observation memory records.
```
