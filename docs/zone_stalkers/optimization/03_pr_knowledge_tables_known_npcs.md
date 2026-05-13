# Memory Optimization PR 3 — Knowledge Tables and known_npcs

## Goal

Replace event-spam representation of world knowledge with compact structured knowledge.

Main target:

```text
stalkers_seen / target_seen / target_last_known_location / corpse reports
```

should update:

```text
agent_knowledge.known_npcs[other_agent_id]
```

instead of creating repeated memory records.

## Dependency

Requires:

```text
PR 1 — explicit memory policy and spam control.
Preferably PR 2 — incremental eviction, but not strictly required.
```

## Scope

In scope:

```text
1. Add agent knowledge state.
2. Add known_npcs table.
3. Add known_locations / known_traders / known_hazards minimal tables.
4. Update knowledge from memory events.
5. Expose knowledge_summary in debug.
6. Update context_builder to prefer knowledge tables.
7. Keep critical episodic memory for story/debug.
```

Out of scope:

```text
cold external store;
full relationship/faction simulation;
vector memory;
pathfinding changes;
complete social AI redesign.
```

## Data shape

Add to agent:

```json
"knowledge_v1": {
  "revision": 0,
  "known_npcs": {},
  "known_locations": {},
  "known_traders": {},
  "known_hazards": {},
  "stats": {
    "known_npcs_count": 0,
    "detailed_known_npcs_count": 0,
    "last_update_turn": 0
  }
}
```

In hot state this is allowed initially, but keep it compact.

## known_npcs schema

```json
{
  "agent_debug_42": {
    "agent_id": "agent_debug_42",
    "name": "Сталкер #42",
    "last_seen_location_id": "loc_Bar",
    "last_seen_turn": 12450,
    "last_seen_distance": 0,
    "is_alive": true,
    "alive_confidence": 0.8,
    "relation": "neutral",
    "relation_score": 0.0,
    "relation_updated_turn": 12450,
    "threat_level": 0.35,
    "combat_strength_estimate": 0.42,
    "equipment_summary": {
      "weapon_class": "rifle",
      "armor_class": "light",
      "detector_tier": 1
    },
    "role_hints": ["artifact_hunter"],
    "last_interaction_turn": 12020,
    "source": "direct_observation",
    "confidence": 0.85,
    "stale_after_turn": 13050,
    "detail_level": "detailed"
  }
}
```

Compact mode:

```json
{
  "agent_id": "agent_77",
  "name": "Сталкер #77",
  "last_seen_location_id": "loc_X",
  "last_seen_turn": 12300,
  "relation_score": 0,
  "threat_level": 0.2,
  "confidence": 0.5,
  "detail_level": "compact"
}
```

## Constants

```python
MAX_KNOWN_NPCS_PER_AGENT = 100
MAX_DETAILED_KNOWN_NPCS_PER_AGENT = 30

KNOWN_NPC_DIRECT_HALF_LIFE_TURNS = 1440
KNOWN_NPC_RUMOR_HALF_LIFE_TURNS = 720
KNOWN_NPC_THREAT_HALF_LIFE_TURNS = 2880
```

## 1. ensure_knowledge_v1

Add:

```python
def ensure_knowledge_v1(agent: dict[str, Any]) -> dict[str, Any]:
    ...
```

Migration:

```text
If missing, create empty structure.
If old provisional known_npcs exists elsewhere, normalize it.
```

Do not scan all memory on every call.

## 2. Upsert helpers

Add:

```python
def upsert_known_npc(
    agent: dict[str, Any],
    *,
    other_agent_id: str,
    name: str | None,
    location_id: str | None,
    world_turn: int,
    source: str,
    confidence: float,
    observed_agent: dict[str, Any] | None = None,
    relation_delta: float | None = None,
    threat_level: float | None = None,
    death_status: dict[str, Any] | None = None,
) -> None:
    ...
```

Sources:

```text
direct_observation
corpse_seen
witness_report
target_intel
combat
trade_interaction
```

Rules:

```text
direct observation > witness report > old memory
newer observation updates last_seen_turn/location
death confirmation sets is_alive=false with high confidence
corpse_seen sets is_alive=false if dead_agent_id known
```

## 3. Update knowledge from events

### stalkers_seen

```text
for each seen NPC:
  upsert_known_npc(... source=direct_observation)
write at most compact crowd_seen/story record
```

### target_seen

```text
upsert_known_npc(target_id, location, source=direct_observation, high confidence)
```

### corpse_seen

```text
upsert_known_npc(dead_agent_id, location, source=corpse_seen, is_alive=false)
```

### target_corpse_reported

```text
upsert_known_npc(target_id, reported_corpse_location_id, source=witness_report, is_alive=false, medium confidence)
```

Important: `target_corpse_reported` does not complete kill goal.

### combat observed

```text
update threat_level, equipment_summary, relation_score
```

## 4. known_locations/traders/hazards minimal tables

Add minimal shapes:

```json
"known_locations": {
  "loc_A": {
    "location_id": "loc_A",
    "name": "Бункер торговца",
    "last_visited_turn": 12000,
    "safe_shelter": true,
    "confidence": 1.0
  }
}
```

```json
"known_traders": {
  "trader_sidor": {
    "agent_id": "trader_sidor",
    "location_id": "loc_Bunker",
    "last_seen_turn": 12000,
    "buys_artifacts": true,
    "sells_food": true,
    "sells_drink": true,
    "confidence": 1.0
  }
}
```

```json
"known_hazards": {
  "loc_D6:emission_death": {
    "location_id": "loc_D6",
    "kind": "emission_death",
    "last_seen_turn": 4016,
    "confidence": 0.9
  }
}
```

Keep this PR minimal.

## 5. Staleness

Do not constantly mutate all records to decay confidence.

Compute effective confidence on read:

```python
def effective_known_npc_confidence(entry: dict[str, Any], world_turn: int) -> float:
    age = world_turn - int(entry.get("last_seen_turn", 0))
    half_life = ...
    return base_confidence * (0.5 ** (age / half_life))
```

Do not write this value back unless entry is updated for another reason.

## 6. Caps and detail demotion

If `known_npcs > 100`:

```text
drop oldest low-confidence compact entries first;
never drop kill target, enemies, recent combat, corpse/target death info, current location NPCs.
```

If detailed entries > 30:

```text
demote old neutral non-target entries to compact mode.
```

## 7. Context builder integration

In:

```text
backend/app/games/zone_stalkers/decision/context_builder.py
```

Prefer knowledge tables:

```python
known_entities = build_known_entities_from_knowledge(agent, world_turn, current_location, target_id)
known_locations = build_known_locations_from_knowledge(agent, world_turn)
known_traders = build_known_traders_from_knowledge(agent, world_turn)
known_hazards = build_known_hazards_from_knowledge(agent, world_turn)
```

Fallback:

```text
If knowledge_v1 missing or empty, use existing memory_v3 scan compatibility path.
```

Do not remove fallback in this PR.

## 8. Debug projection

Add:

```json
"knowledge_summary": {
  "revision": 123,
  "known_npcs_count": 42,
  "detailed_known_npcs_count": 12,
  "known_traders_count": 1,
  "known_hazards_count": 3,
  "top_recent_known_npcs": []
}
```

Do not dump all known_npcs into compact list views by default.

## Tests

Add:

```text
backend/tests/decision/v3/test_knowledge_tables.py
backend/tests/decision/test_context_builder_knowledge.py
```

Required tests:

```python
def test_stalkers_seen_upserts_known_npcs_without_many_memory_records(): ...
def test_target_seen_updates_known_npc_location_and_confidence(): ...
def test_corpse_seen_marks_known_npc_dead(): ...
def test_target_corpse_reported_is_lead_not_goal_completion(): ...
def test_known_npcs_cap_keeps_target_and_recent_enemies(): ...
def test_detailed_known_npcs_cap_demotes_neutral_old_entries(): ...
def test_context_builder_uses_known_npcs_for_target_location(): ...
def test_context_builder_falls_back_to_memory_v3_when_knowledge_missing(): ...
def test_known_npc_effective_confidence_decays_without_mutating_state(): ...
def test_debug_projection_includes_knowledge_summary(): ...
```

## Manual validation

Run 10-NPC or 20-NPC long simulation:

```text
Before:
  stalkers_seen = 300–400 / 500

After:
  stalkers_seen episodic records bounded;
  known_npcs_count grows compactly;
  target/corpse/trader information remains available.
```

For killer/corpse scenario:

```text
[ ] witness sees target corpse;
[ ] witness has known_npcs[target].is_alive=false;
[ ] killer gets target_corpse_reported lead;
[ ] context_builder can route killer to corpse location.
```

## Definition of Done

```text
[ ] knowledge_v1 exists and is compact.
[ ] known_npcs stores latest useful info per NPC.
[ ] repeated observations upsert knowledge instead of spamming events.
[ ] context_builder prefers knowledge tables.
[ ] memory_v3 keeps critical stories, not routine observation spam.
[ ] debug projection exposes knowledge summary.
[ ] target/corpse gameplay remains correct.
```
